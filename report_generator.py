"""
report_generator.py — Self-contained HTML flight report from a parsed log.

Generates a single HTML file (no external assets except Chart.js CDN) that
the pilot can open in any browser, print as PDF, or share.

Sections
--------
  • Header: filename, date, duration
  • 4 stat cards: duration, max altitude, min battery voltage, GPS quality
  • Altitude profile chart (BARO.Alt sampled to ≤300 pts)
  • Battery voltage chart (BAT/CURR.Volt sampled to ≤300 pts)
  • Flight mode timeline (coloured horizontal bar)
  • Vibration table (PASS / WARN / FAIL)
  • Errors & events table (ERR messages)
  • Footer with UAV-AI branding
"""

import json
import math
from datetime import datetime, timezone

# ── ArduCopter mode map ────────────────────────────────────────────────────
_COPTER_MODES = {
    0: 'STABILIZE', 1: 'ACRO', 2: 'ALT_HOLD', 3: 'AUTO',
    4: 'GUIDED', 5: 'LOITER', 6: 'RTL', 7: 'CIRCLE',
    9: 'LAND', 11: 'DRIFT', 13: 'SPORT', 15: 'AUTOTUNE',
    16: 'POSHOLD', 17: 'BRAKE', 18: 'THROW', 21: 'SMART_RTL',
}
_MODE_COLOURS = [
    '#3b82f6', '#10b981', '#f59e0b', '#ef4444',
    '#8b5cf6', '#ec4899', '#14b8a6', '#f97316',
    '#6366f1', '#84cc16', '#06b6d4', '#a855f7',
]

MAX_CHART_PTS = 300   # max data points per chart to keep report small


def _sample(lst, max_pts):
    """Uniformly subsample a list to at most max_pts entries."""
    if len(lst) <= max_pts:
        return lst
    step = len(lst) / max_pts
    return [lst[int(i * step)] for i in range(max_pts)]


def _fmt_duration(secs):
    if secs is None:
        return '--'
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    if h:
        return f'{h}h {m:02d}m'
    return f'{m}m {s:02d}s'


def generate_flight_report(parser, filename: str) -> str:
    """
    Build and return a self-contained HTML string.

    Parameters
    ----------
    parser   : LogParser instance that has already been parsed (_is_parsed=True)
    filename : original log filename (for the report header)
    """
    pd = parser.parsed_data

    # ── Stats ──────────────────────────────────────────────────────────────
    stats = {}

    # Duration
    for mtype in ('ATT', 'GPS', 'BARO', 'RCOU'):
        msgs = pd.get(mtype, [])
        if len(msgs) >= 2:
            t0 = msgs[0].get('TimeUS') or 0
            t1 = msgs[-1].get('TimeUS') or 0
            if t1 > t0:
                stats['duration_s'] = round((t1 - t0) / 1e6)
                stats['t_origin_us'] = t0
                break

    t_origin = stats.get('t_origin_us', 0)

    # Max altitude (BARO)
    baro = pd.get('BARO', [])
    alts = [m.get('Alt') for m in baro if m.get('Alt') is not None]
    stats['max_alt_m'] = round(max(alts), 1) if alts else None

    # Battery (BAT or CURR)
    bat_msgs = pd.get('BAT', pd.get('CURR', []))
    volts = [m.get('Volt') for m in bat_msgs if m.get('Volt') is not None]
    stats['start_volt'] = round(volts[0], 2) if volts else None
    stats['min_volt']   = round(min(volts), 2) if volts else None

    # GPS quality
    gps_msgs = pd.get('GPS', [])
    gps_statuses = [m.get('Status', 0) for m in gps_msgs if m.get('Status') is not None]
    if gps_statuses:
        mx = max(gps_statuses)
        stats['gps_fix'] = (
            'RTK' if mx >= 5 else
            '3D Fix' if mx >= 3 else
            '2D Fix' if mx == 2 else 'No Fix'
        )
        stats['gps_ok'] = mx >= 3
    else:
        stats['gps_fix'] = '--'
        stats['gps_ok']  = False

    # ── Chart data ─────────────────────────────────────────────────────────
    # Altitude
    alt_xy = _sample(
        [{'x': round((m.get('TimeUS', 0) - t_origin) / 1e6, 1),
          'y': round(m.get('Alt', 0), 1)}
         for m in baro if m.get('Alt') is not None],
        MAX_CHART_PTS
    )

    # Battery voltage
    bat_xy = _sample(
        [{'x': round((m.get('TimeUS', 0) - t_origin) / 1e6, 1),
          'y': round(m.get('Volt', 0), 2)}
         for m in bat_msgs if m.get('Volt') is not None],
        MAX_CHART_PTS
    )

    # ── Mode timeline ──────────────────────────────────────────────────────
    mode_msgs = sorted(pd.get('MODE', []), key=lambda m: m.get('TimeUS', 0))
    dur_s = stats.get('duration_s', 1) or 1
    mode_segs = []
    seen_modes = []

    for i, m in enumerate(mode_msgs):
        mode_num = m.get('Mode', m.get('ModeNum', 0)) or 0
        name     = _COPTER_MODES.get(mode_num, f'MODE{mode_num}')
        start_s  = round((m.get('TimeUS', t_origin) - t_origin) / 1e6, 1)
        end_s    = (
            round((mode_msgs[i + 1].get('TimeUS', 0) - t_origin) / 1e6, 1)
            if i + 1 < len(mode_msgs) else dur_s
        )
        pct = round(max(0, (end_s - start_s) / dur_s) * 100, 1)
        if pct < 0.5:
            continue

        if name not in seen_modes:
            seen_modes.append(name)
        colour_idx = seen_modes.index(name) % len(_MODE_COLOURS)
        mode_segs.append({
            'name':   name,
            'pct':    pct,
            'colour': _MODE_COLOURS[colour_idx],
        })

    # ── Vibration table ────────────────────────────────────────────────────
    vibe_rows = []
    for m in pd.get('VIBE', []):
        t_s = round((m.get('TimeUS', 0) - t_origin) / 1e6, 1)
        for ax in ('VibeX', 'VibeY', 'VibeZ'):
            v = m.get(ax)
            if v is None:
                continue
            status = 'PASS' if v < 15 else ('WARN' if v < 30 else 'FAIL')
            vibe_rows.append({'axis': ax, 'value': round(v, 1),
                              'time_s': t_s, 'status': status})
    # Keep only worst per axis
    worst = {}
    for r in vibe_rows:
        k = r['axis']
        if k not in worst or r['value'] > worst[k]['value']:
            worst[k] = r
    vibe_rows = sorted(worst.values(), key=lambda r: r['axis'])

    # ── Error table ────────────────────────────────────────────────────────
    error_rows = []
    for m in pd.get('ERR', []):
        ec = m.get('ECode', 0)
        if ec:
            error_rows.append({
                'subsys': m.get('Subsys', '?'),
                'ecode':  ec,
                'time_s': round((m.get('TimeUS', 0) - t_origin) / 1e6, 1),
            })
    error_rows = error_rows[:10]

    # ── Assemble JSON payload for the inline script ────────────────────────
    report_data = {
        'filename':  filename,
        'generated': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
        'stats':     stats,
        'alt_xy':    alt_xy,
        'bat_xy':    bat_xy,
        'mode_segs': mode_segs,
        'vibe_rows': vibe_rows,
        'error_rows': error_rows,
    }
    data_json = json.dumps(report_data)

    # ── HTML ───────────────────────────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Flight Report — {filename}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',Arial,sans-serif;background:#f0f4f8;color:#1e293b;padding:2rem}}
h1{{font-size:1.7rem;color:#1e3a5f;display:flex;align-items:center;gap:.5rem}}
.meta{{color:#64748b;font-size:.85rem;margin-top:.25rem}}
.stat-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:1rem;margin:1.5rem 0}}
.stat-card{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:1rem;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
.stat-value{{font-size:1.9rem;font-weight:700;color:#1e3a5f}}
.stat-label{{font-size:.7rem;color:#94a3b8;text-transform:uppercase;letter-spacing:.6px;margin-top:.2rem}}
h2{{font-size:1rem;font-weight:600;color:#334155;margin:1.5rem 0 .5rem;padding-bottom:.3rem;border-bottom:2px solid #e2e8f0}}
.chart-wrap{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:1rem;margin-bottom:1rem;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
.mode-bar{{display:flex;height:34px;border-radius:6px;overflow:hidden;margin-bottom:.6rem}}
.mode-seg{{display:flex;align-items:center;justify-content:center;font-size:.62rem;font-weight:700;color:#fff;overflow:hidden;white-space:nowrap;padding:0 4px}}
.mode-legend{{display:flex;flex-wrap:wrap;gap:.4rem;margin-bottom:1rem}}
.mode-dot{{display:flex;align-items:center;gap:.3rem;font-size:.75rem;color:#475569}}
.mode-dot-circle{{width:10px;height:10px;border-radius:50%;flex-shrink:0}}
table{{width:100%;border-collapse:collapse;font-size:.83rem;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
th{{background:#f8fafc;padding:.5rem .75rem;text-align:left;font-weight:600;color:#374151;font-size:.78rem;border-bottom:2px solid #e2e8f0}}
td{{padding:.4rem .75rem;border-bottom:1px solid #f1f5f9;color:#374151}}
.pass{{color:#16a34a;font-weight:700}}
.warn{{color:#d97706;font-weight:700}}
.fail{{color:#dc2626;font-weight:700}}
.footer{{margin-top:2.5rem;text-align:center;font-size:.75rem;color:#94a3b8}}
@media print{{
  body{{background:#fff;padding:1rem}}
  .chart-wrap,.stat-card{{break-inside:avoid}}
  .stat-grid{{grid-template-columns:repeat(4,1fr)}}
}}
@media(max-width:600px){{.stat-grid{{grid-template-columns:repeat(2,1fr)}}}}
</style>
</head>
<body>

<div style="margin-bottom:1.5rem">
  <h1>&#9992;&#xFE0F; UAV-AI Flight Report</h1>
  <div class="meta" id="metaLine"></div>
</div>

<div class="stat-grid">
  <div class="stat-card"><div class="stat-value" id="sDur">--</div><div class="stat-label">Duration</div></div>
  <div class="stat-card"><div class="stat-value" id="sAlt">--</div><div class="stat-label">Max Altitude (m)</div></div>
  <div class="stat-card"><div class="stat-value" id="sVolt">--</div><div class="stat-label">Min Battery (V)</div></div>
  <div class="stat-card"><div class="stat-value" id="sGps">--</div><div class="stat-label">GPS Quality</div></div>
</div>

<h2>Altitude Profile</h2>
<div class="chart-wrap"><canvas id="altChart" height="90"></canvas></div>

<h2>Battery Voltage</h2>
<div class="chart-wrap"><canvas id="batChart" height="90"></canvas></div>

<h2>Flight Modes</h2>
<div class="mode-bar" id="modeBar"></div>
<div class="mode-legend" id="modeLegend"></div>

<h2>Vibration</h2>
<table><thead><tr><th>Axis</th><th>Peak (m/s&sup2;)</th><th>Status</th></tr></thead>
<tbody id="vibeBody"></tbody></table>

<h2>Errors &amp; Events</h2>
<table><thead><tr><th>Subsystem</th><th>Error Code</th><th>Time (s)</th></tr></thead>
<tbody id="errBody"></tbody></table>

<div class="footer">Generated by <strong>UAV-AI</strong> &nbsp;|&nbsp; <span id="genTs"></span></div>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script>
var D = {data_json};

// Header
document.getElementById('metaLine').textContent =
  D.filename + '  \u00b7  ' + D.generated;
document.getElementById('genTs').textContent = D.generated;

// Stat cards
var s = D.stats;
document.getElementById('sDur').textContent  = s.duration_s != null ? fmtDur(s.duration_s) : '--';
document.getElementById('sAlt').textContent  = s.max_alt_m  != null ? s.max_alt_m + ' m'    : '--';
document.getElementById('sVolt').textContent = s.min_volt   != null ? s.min_volt + ' V'      : '--';
document.getElementById('sGps').textContent  = s.gps_fix   || '--';

function fmtDur(secs){{
  var h=Math.floor(secs/3600), m=Math.floor((secs%3600)/60), s=secs%60;
  return h ? h+'h '+pad(m)+'m' : m+'m '+pad(s)+'s';
}}
function pad(n){{return n<10?'0'+n:n;}}

// Altitude chart
if (D.alt_xy.length) {{
  new Chart(document.getElementById('altChart'), {{
    type:'line',
    data:{{datasets:[{{label:'Altitude (m)',data:D.alt_xy,borderColor:'#3b82f6',
      backgroundColor:'rgba(59,130,246,.08)',borderWidth:1.5,pointRadius:0,fill:true}}]}},
    options:{{animation:false,parsing:false,
      scales:{{x:{{type:'linear',title:{{display:true,text:'Time (s)'}}}},
               y:{{title:{{display:true,text:'m'}}}}}},
      plugins:{{legend:{{display:false}}}}}}
  }});
}}

// Battery chart
if (D.bat_xy.length) {{
  new Chart(document.getElementById('batChart'), {{
    type:'line',
    data:{{datasets:[{{label:'Voltage (V)',data:D.bat_xy,borderColor:'#f59e0b',
      backgroundColor:'rgba(245,158,11,.08)',borderWidth:1.5,pointRadius:0,fill:true}}]}},
    options:{{animation:false,parsing:false,
      scales:{{x:{{type:'linear',title:{{display:true,text:'Time (s)'}}}},
               y:{{title:{{display:true,text:'V'}}}}}},
      plugins:{{legend:{{display:false}}}}}}
  }});
}}

// Mode timeline
var bar = document.getElementById('modeBar');
var leg = document.getElementById('modeLegend');
var seen = {{}};
D.mode_segs.forEach(function(seg){{
  var div = document.createElement('div');
  div.className = 'mode-seg';
  div.style.width = seg.pct+'%';
  div.style.background = seg.colour;
  div.title = seg.name + ' (' + seg.pct + '%)';
  if (seg.pct > 5) div.textContent = seg.name;
  bar.appendChild(div);
  if (!seen[seg.name]){{
    seen[seg.name]=1;
    var item = document.createElement('div');
    item.className='mode-dot';
    item.innerHTML='<div class="mode-dot-circle" style="background:'+seg.colour+'"></div>'+seg.name;
    leg.appendChild(item);
  }}
}});

// Vibration
var statusCls = {{PASS:'pass',WARN:'warn',FAIL:'fail'}};
var vb = document.getElementById('vibeBody');
if (!D.vibe_rows.length) {{
  vb.innerHTML = '<tr><td colspan="3" style="color:#94a3b8">No VIBE data in log</td></tr>';
}} else {{
  D.vibe_rows.forEach(function(r){{
    vb.innerHTML += '<tr><td>'+r.axis+'</td><td>'+r.value+'</td>'
      +'<td class="'+statusCls[r.status]+'">'+r.status+'</td></tr>';
  }});
}}

// Errors
var eb = document.getElementById('errBody');
if (!D.error_rows.length) {{
  eb.innerHTML = '<tr><td colspan="3" style="color:#94a3b8">No errors recorded</td></tr>';
}} else {{
  D.error_rows.forEach(function(r){{
    eb.innerHTML += '<tr><td>'+r.subsys+'</td><td>'+r.ecode+'</td><td>'+r.time_s+'s</td></tr>';
  }});
}}
</script>
</body>
</html>"""
    return html
