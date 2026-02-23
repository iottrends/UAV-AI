// ===== parameters.js — Parameter fetch, display, save + Filter Visualizer =====

document.addEventListener('DOMContentLoaded', function() {
   var allParameters = {};
   var modifiedParameters = {};

   // ---- Sub-tab switching for Tuning tab ----
   document.querySelectorAll('#parameters-tab .subtab-btn').forEach(function(btn) {
      btn.addEventListener('click', function() {
         document.querySelectorAll('#parameters-tab .subtab-btn').forEach(function(b) { b.classList.remove('active'); });
         document.querySelectorAll('#parameters-tab .subtab-panel').forEach(function(p) { p.style.display = 'none'; });
         btn.classList.add('active');
         var target = document.getElementById(btn.dataset.subtab);
         if (target) target.style.display = 'block';
         if (btn.dataset.subtab === 'tuning-filter-viz')    renderFilterViz();
         if (btn.dataset.subtab === 'tuning-step-predictor') initStepPredictor();
      });
   });

   // ---- Helper: flatten categorized params to { NAME: value } ----
   function flattenParams(categorized) {
      var flat = {};
      for (var cat in categorized) {
         for (var name in categorized[cat]) {
            flat[name] = parseFloat(categorized[cat][name]) || 0;
         }
      }
      return flat;
   }

   async function fetchParameters() {
      try {
         var response = await fetch('/api/parameters');
         if (!response.ok) {
            throw new Error('Failed to fetch parameters: ' + response.status);
         }

         allParameters = await response.json();
         modifiedParameters = {};
         // Expose flat params globally for Filter Visualizer + Spectrum overlay
         window._app.flatParams = flattenParams(allParameters);
         displayParameters();
         return allParameters;
      } catch (error) {
         console.error('Error fetching parameters:', error);
         window._app.addMessage({
            text: '<strong>Error:</strong> Failed to fetch parameters: ' + error.message,
            time: window._app.getCurrentTime()
         });
         return {};
      }
   }

   function displayParameters() {
      var tableBody = document.getElementById('parametersTableBody');
      if (!tableBody) return;

      tableBody.innerHTML = '';

      var categoryFilter = document.getElementById('categoryFilter').value;
      var searchTerm = document.getElementById('paramSearch').value.toLowerCase();

      var filteredParams = {};

      if (categoryFilter === 'All Categories') {
         for (var category in allParameters) {
            filteredParams[category] = {};
            for (var param in allParameters[category]) {
               if (param.toLowerCase().includes(searchTerm)) {
                  filteredParams[category][param] = allParameters[category][param];
               }
            }
         }
      } else {
         if (allParameters[categoryFilter]) {
            filteredParams[categoryFilter] = {};
            for (var param2 in allParameters[categoryFilter]) {
               if (param2.toLowerCase().includes(searchTerm)) {
                  filteredParams[categoryFilter][param2] = allParameters[categoryFilter][param2];
               }
            }
         }
      }

      for (var cat in filteredParams) {
         for (var p in filteredParams[cat]) {
            var value = filteredParams[cat][p];

            var row = document.createElement('tr');

            var nameCell = document.createElement('td');
            nameCell.style.padding = '0.75rem';
            nameCell.style.borderBottom = '1px solid #ddd';
            nameCell.textContent = p;

            var valueCell = document.createElement('td');
            valueCell.style.padding = '0.75rem';
            valueCell.style.borderBottom = '1px solid #ddd';

            var valueInput = document.createElement('input');
            valueInput.type = 'text';
            valueInput.value = modifiedParameters[p] !== undefined ? modifiedParameters[p] : value;
            valueInput.style.width = '100%';
            valueInput.style.padding = '0.25rem';
            valueInput.style.border = '1px solid #ddd';
            valueInput.style.borderRadius = '3px';

            if (modifiedParameters[p] !== undefined) {
               valueInput.style.backgroundColor = '#fff8e1';
               valueInput.style.borderColor = 'var(--warning-color)';
            }

            (function(paramName, origValue, input) {
               input.addEventListener('change', function() {
                  var newValue = this.value.trim();
                  if (newValue !== origValue.toString()) {
                     modifiedParameters[paramName] = newValue;
                     this.style.backgroundColor = '#fff8e1';
                     this.style.borderColor = 'var(--warning-color)';
                  } else {
                     delete modifiedParameters[paramName];
                     this.style.backgroundColor = '';
                     this.style.borderColor = '#ddd';
                  }
                  document.getElementById('saveParams').disabled = Object.keys(modifiedParameters).length === 0;
               });
            })(p, value, valueInput);

            valueCell.appendChild(valueInput);

            var descCell = document.createElement('td');
            descCell.style.padding = '0.75rem';
            descCell.style.borderBottom = '1px solid #ddd';
            descCell.textContent = getParameterDescription(p);

            var rangeCell = document.createElement('td');
            rangeCell.style.padding = '0.75rem';
            rangeCell.style.borderBottom = '1px solid #ddd';
            rangeCell.textContent = getParameterRange(p);

            var actionsCell = document.createElement('td');
            actionsCell.style.padding = '0.75rem';
            actionsCell.style.borderBottom = '1px solid #ddd';

            var resetButton = document.createElement('button');
            resetButton.innerHTML = '<i class="fas fa-undo"></i>';
            resetButton.title = 'Reset to default';
            resetButton.style.backgroundColor = 'transparent';
            resetButton.style.border = 'none';
            resetButton.style.cursor = 'pointer';
            resetButton.style.color = 'var(--primary-color)';
            resetButton.style.borderRadius = '50%';
            resetButton.style.width = '30px';
            resetButton.style.height = '30px';
            resetButton.style.display = 'flex';
            resetButton.style.justifyContent = 'center';
            resetButton.style.alignItems = 'center';

            (function(paramName, origValue, input) {
               resetButton.addEventListener('click', function() {
                  input.value = origValue;
                  delete modifiedParameters[paramName];
                  input.style.backgroundColor = '';
                  input.style.borderColor = '#ddd';
                  document.getElementById('saveParams').disabled = Object.keys(modifiedParameters).length === 0;
               });
            })(p, value, valueInput);

            actionsCell.appendChild(resetButton);

            row.appendChild(nameCell);
            row.appendChild(valueCell);
            row.appendChild(descCell);
            row.appendChild(rangeCell);
            row.appendChild(actionsCell);

            tableBody.appendChild(row);
         }
      }
   }

   async function saveParameters() {
      if (Object.keys(modifiedParameters).length === 0) {
         return;
      }

      try {
         var response = await fetch('/api/parameters', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(modifiedParameters)
         });

         if (!response.ok) {
            throw new Error('Failed to save parameters: ' + response.status);
         }

         var result = await response.json();

         if (result.status === 'success') {
            window._app.addMessage({
               text: '<strong>Success:</strong> Parameters saved successfully.',
               time: window._app.getCurrentTime()
            });
            fetchParameters();
         } else {
            throw new Error(result.message || 'Unknown error');
         }
      } catch (error) {
         console.error('Error saving parameters:', error);
         window._app.addMessage({
            text: '<strong>Error:</strong> Failed to save parameters: ' + error.message,
            time: window._app.getCurrentTime()
         });
      }
   }

   function getParameterDescription(param) {
      var descriptions = {
         'BATT_CAPACITY': 'Battery capacity in mAh',
         'BATT_CRT_VOLT': 'Battery critical voltage threshold',
         'BATT_LOW_VOLT': 'Battery low voltage threshold',
         'BATT_MONITOR': 'Battery monitoring type',
         'AHRS_GPS_USE': 'Use GPS for attitude estimation',
         'GPS_HDOP_GOOD': 'GPS HDOP good threshold',
         'GPS_TYPE': 'GPS type/provider',
         'MOT_PWM_TYPE': 'Motor PWM output type',
         'MOT_SPIN_MAX': 'Maximum motor output',
         'MOT_SPIN_MIN': 'Throttle minimum when armed'
      };
      return descriptions[param] || '';
   }

   function getParameterRange(param) {
      var ranges = {
         'BATT_CAPACITY': '0-50000 mAh',
         'BATT_CRT_VOLT': '-',
         'BATT_LOW_VOLT': '-',
         'BATT_MONITOR': '-',
         'AHRS_GPS_USE': '-',
         'GPS_HDOP_GOOD': '-',
         'GPS_TYPE': 'Various',
         'MOT_PWM_TYPE': 'Various',
         'MOT_SPIN_MAX': '0.7-1.0',
         'MOT_SPIN_MIN': '0.0-0.3'
      };
      return ranges[param] || '-';
   }

   // Event listeners
   document.getElementById('refreshParams')?.addEventListener('click', fetchParameters);
   document.getElementById('saveParams')?.addEventListener('click', saveParameters);
   document.getElementById('searchBtn')?.addEventListener('click', displayParameters);
   document.getElementById('categoryFilter')?.addEventListener('change', displayParameters);
   document.getElementById('paramSearch')?.addEventListener('keyup', function(e) {
      if (e.key === 'Enter') {
         displayParameters();
      }
   });

   // Fetch parameters when the parameters tab is first shown
   document.querySelector('.menu-item[data-tab="parameters"]')?.addEventListener('click', function() {
      if (Object.keys(allParameters).length === 0) {
         fetchParameters();
      }
   });

   // Initially disable save button
   if (document.getElementById('saveParams')) {
      document.getElementById('saveParams').disabled = true;
   }

   // ============================================================
   // Filter Visualizer — Live Bode plot of the gyro filter stack
   // ============================================================

   var filterChart = null;

   // --- Filter math (2nd-order Butterworth LPF + notch) ---

   function lpfDb(freqs, fc) {
      // 2nd-order Butterworth LPF magnitude in dB
      if (!fc || fc <= 0) return freqs.map(function() { return 0; });
      return freqs.map(function(f) {
         return 20 * Math.log10(1 / Math.sqrt(1 + Math.pow(f / fc, 4)));
      });
   }

   function notchDb(freqs, fc, bw) {
      // 2nd-order notch magnitude in dB: |H| = |fc²-f²| / sqrt((fc²-f²)²+(fc·f/Q)²)
      if (!fc || fc <= 0 || !bw || bw <= 0) return freqs.map(function() { return 0; });
      var Q = fc / bw;
      return freqs.map(function(f) {
         var r  = f / fc;
         var r2 = r * r;
         var num = Math.abs(1 - r2);
         var den = Math.sqrt(Math.pow(1 - r2, 2) + Math.pow(r / Q, 2));
         return 20 * Math.log10((num / den) + 1e-9);
      });
   }

   function harmonicNotchDb(freqs, fc, bw, harmonicsMask) {
      // Sum of notch filters at each active harmonic
      var result = freqs.map(function() { return 0; });
      if (!fc || fc <= 0) return result;
      for (var h = 1; h <= 8; h++) {
         if (harmonicsMask & (1 << (h - 1))) {
            var nd = notchDb(freqs, fc * h, bw * h);
            result = result.map(function(v, i) { return v + nd[i]; });
         }
      }
      return result;
   }

   function renderFilterViz() {
      var p = window._app.flatParams;
      if (!p || Object.keys(p).length === 0) {
         var noConn = document.getElementById('filterVizNoConn');
         var panel  = document.getElementById('filterVizPanel');
         if (noConn) noConn.style.display = 'flex';
         if (panel)  panel.style.display  = 'none';
         return;
      }

      var noConn = document.getElementById('filterVizNoConn');
      var panel  = document.getElementById('filterVizPanel');
      if (noConn) noConn.style.display = 'none';
      if (panel)  panel.style.display  = 'block';

      // Read filter params (default to typical values if missing)
      var loopRate   = p['SCHED_LOOP_RATE'] || 400;
      var nyquist    = loopRate / 2;
      var gyroLpf    = p['INS_GYRO_FILTER'] || 0;
      var notchEn    = p['INS_NOTCH_ENABLE'] || 0;
      var notchFreq  = p['INS_NOTCH_FREQ']   || 0;
      var notchBw    = p['INS_NOTCH_BW']     || 0;
      var hntchEn    = p['INS_HNTCH_ENABLE'] || 0;
      var hntchFreq  = p['INS_HNTCH_FREQ']   || 0;
      var hntchBw    = p['INS_HNTCH_BW']     || 0;
      var hntchHmncs = p['INS_HNTCH_HMNCS']  || 1;
      var dtermLpf   = p['ATC_RAT_RLL_FLTD'] || 0;

      // Build frequency array: 1 Hz to Nyquist, 300 points linear
      var maxHz = Math.min(nyquist, 500);
      var freqs = [];
      for (var i = 0; i < 300; i++) {
         freqs.push(1 + (maxHz - 1) * i / 299);
      }

      // Compute each filter series in dB
      var lpf    = lpfDb(freqs, gyroLpf);
      var notch  = notchEn  ? notchDb(freqs, notchFreq, notchBw)              : freqs.map(function(){ return 0; });
      var hntch  = hntchEn  ? harmonicNotchDb(freqs, hntchFreq, hntchBw, hntchHmncs) : freqs.map(function(){ return 0; });
      var dterm  = lpfDb(freqs, dtermLpf);
      var total  = freqs.map(function(_, i) { return lpf[i] + notch[i] + hntch[i] + dterm[i]; });

      // Params summary row
      var summaryEl = document.getElementById('filterParamsSummary');
      if (summaryEl) {
         var parts = [];
         if (gyroLpf)   parts.push('Gyro LPF: ' + gyroLpf + ' Hz');
         if (notchEn)   parts.push('Notch: ' + notchFreq + ' Hz (BW ' + notchBw + ')');
         if (hntchEn)   parts.push('HNTCH: ' + hntchFreq + ' Hz (BW ' + hntchBw + ', mask ' + hntchHmncs + ')');
         if (dtermLpf)  parts.push('D-term LPF: ' + dtermLpf + ' Hz');
         summaryEl.textContent = parts.length ? parts.join('  |  ') : 'No active filters detected';
      }

      // Build Chart.js datasets
      var SERIES = [
         { label: 'Gyro LPF',     data: lpf,   color: '#3498db', dash: [] },
         { label: 'Static Notch', data: notch,  color: '#e74c3c', dash: [4,3] },
         { label: 'Harm. Notch',  data: hntch,  color: '#f39c12', dash: [4,3] },
         { label: 'D-term LPF',   data: dterm,  color: '#9b59b6', dash: [6,3] },
         { label: 'Total',        data: total,  color: '#2ecc71', dash: [] },
      ];

      var checkboxes = document.querySelectorAll('#filterVizPanel input[data-series]');
      var datasets = SERIES.map(function(s, idx) {
         var visible = !checkboxes[idx] || checkboxes[idx].checked;
         return {
            label: s.label,
            data: s.data,
            borderColor: s.color,
            backgroundColor: 'transparent',
            borderWidth: idx === 4 ? 2.5 : 1.5,
            borderDash: s.dash,
            pointRadius: 0,
            hidden: !visible,
            tension: 0.2,
         };
      });

      if (filterChart) { filterChart.destroy(); filterChart = null; }
      var canvas = document.getElementById('filterVizCanvas');
      if (!canvas) return;

      filterChart = new Chart(canvas.getContext('2d'), {
         type: 'line',
         data: { labels: freqs.map(function(f){ return f.toFixed(1); }), datasets: datasets },
         options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
               legend: { labels: { color: '#ccc', font: { size: 11 }, boxWidth: 20 } },
               tooltip: {
                  callbacks: {
                     title: function(items) { return items[0].label + ' Hz'; },
                     label: function(item)  { return item.dataset.label + ': ' + item.raw.toFixed(1) + ' dB'; },
                  }
               },
            },
            scales: {
               x: {
                  type: 'linear',
                  ticks: { color: '#888', maxTicksLimit: 12, font: { size: 10 },
                           callback: function(v) { return v + ' Hz'; } },
                  grid: { color: 'rgba(255,255,255,0.05)' },
                  title: { display: true, text: 'Frequency (Hz)', color: '#888' },
               },
               y: {
                  ticks: { color: '#888', font: { size: 10 },
                           callback: function(v) { return v + ' dB'; } },
                  grid: { color: 'rgba(255,255,255,0.08)' },
                  title: { display: true, text: 'Gain (dB)', color: '#888' },
                  suggestedMin: -60,
                  suggestedMax: 5,
               },
            },
         },
      });

      // Series toggle checkboxes
      checkboxes.forEach(function(cb) {
         cb.addEventListener('change', function() {
            var idx = parseInt(cb.dataset.series);
            if (filterChart && filterChart.data.datasets[idx]) {
               filterChart.data.datasets[idx].hidden = !cb.checked;
               filterChart.update('none');
            }
         });
      });
   }

   // ============================================================
   // Step Predictor — Numerical step response + Flight Feel sliders
   // ============================================================

   var stepChart    = null;
   var stepAxis     = 'roll';           // roll | pitch | yaw
   var stepMode     = 'feel';           // feel | expert
   var stepBaseGains = {};              // FC values at init time
   var stepCurrentGains = {};          // currently previewed gains

   var GAIN_PARAMS = {
      roll:  { P: 'ATC_RAT_RLL_P', I: 'ATC_RAT_RLL_I', D: 'ATC_RAT_RLL_D' },
      pitch: { P: 'ATC_RAT_PIT_P', I: 'ATC_RAT_PIT_I', D: 'ATC_RAT_PIT_D' },
      yaw:   { P: 'ATC_RAT_YAW_P', I: 'ATC_RAT_YAW_I', D: 'ATC_RAT_YAW_D' },
   };

   // --- Numerical step response simulation ---
   // Models the ArduPilot cascaded attitude+rate PID loop
   function simulateStepResponse(P_att, P_rate, I_rate, D_rate, accel_max_dss) {
      var dt       = 0.0025;   // 400 Hz sim
      var duration = 2.0;      // 2 seconds
      var setpoint = 20.0;     // 20 deg step command
      var steps    = Math.floor(duration / dt);
      var angle = 0, rate = 0, iterm = 0, prevRateErr = 0;
      var out = [];

      for (var i = 0; i < steps; i++) {
         // Outer P loop: angle error → desired rate
         var angleErr   = setpoint - angle;
         var rateTarget = Math.max(-200, Math.min(200, angleErr * P_att));

         // Inner PID loop: rate error → motor output
         var rateErr = rateTarget - rate;
         iterm += rateErr * dt * I_rate;
         iterm  = Math.max(-1.0, Math.min(1.0, iterm));   // anti-windup
         var dterm  = ((rateErr - prevRateErr) / dt) * D_rate;
         prevRateErr = rateErr;

         var output = Math.max(-1, Math.min(1, rateErr * P_rate + iterm + dterm));

         // Plant: angular acceleration limited by accel_max
         rate  += output * accel_max_dss * dt;
         angle += rate * dt;

         if (i % 4 === 0) {  // store every 4th point (100 Hz output)
            out.push({ t: parseFloat((i * dt).toFixed(4)), y: angle / setpoint });
         }
      }
      return out;
   }

   function getGainsForAxis(axis) {
      var p = stepCurrentGains;
      var names = GAIN_PARAMS[axis];
      return {
         P_att:     p['ATC_ANG_RLL_P']  || p['ATC_ANG_PIT_P'] || 4.5,
         P_rate:    p[names.P] || 0.135,
         I_rate:    p[names.I] || 0.135,
         D_rate:    p[names.D] || 0.0036,
         accel_max: (p['ATC_ACCEL_R_MAX'] || 110000) / 100.0,  // centideg/s/s → deg/s/s
      };
   }

   function renderStepChart() {
      var g  = getGainsForAxis(stepAxis);
      var data = simulateStepResponse(g.P_att, g.P_rate, g.I_rate, g.D_rate, g.accel_max);

      var names = GAIN_PARAMS[stepAxis];
      var summaryEl = document.getElementById('stepParamSummary');
      if (summaryEl) {
         summaryEl.textContent =
            'P=' + (stepCurrentGains[names.P] || '?') +
            '  I=' + (stepCurrentGains[names.I] || '?') +
            '  D=' + (stepCurrentGains[names.D] || '?') +
            '  Accel max: ' + Math.round(g.accel_max) + ' deg/s²';
      }

      if (stepChart) { stepChart.destroy(); stepChart = null; }
      var canvas = document.getElementById('stepPredCanvas');
      if (!canvas) return;

      stepChart = new Chart(canvas.getContext('2d'), {
         type: 'line',
         data: {
            labels: data.map(function(d) { return d.t.toFixed(2); }),
            datasets: [{
               label: stepAxis.charAt(0).toUpperCase() + stepAxis.slice(1) + ' response',
               data: data.map(function(d) { return d.y; }),
               borderColor: '#2ecc71',
               backgroundColor: 'rgba(46,204,113,0.07)',
               borderWidth: 2,
               pointRadius: 0,
               fill: true,
               tension: 0.3,
            }, {
               label: 'Setpoint',
               data: data.map(function() { return 1.0; }),
               borderColor: '#555',
               borderWidth: 1,
               borderDash: [4, 4],
               pointRadius: 0,
               fill: false,
            }],
         },
         options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
               legend: { labels: { color: '#ccc', font: { size: 11 }, boxWidth: 20 } },
               tooltip: {
                  callbacks: {
                     title: function(items) { return items[0].label + ' s'; },
                     label: function(item)  { return (item.raw * 100).toFixed(1) + '%'; },
                  }
               },
            },
            scales: {
               x: {
                  type: 'linear',
                  ticks: { color: '#888', maxTicksLimit: 10, font: { size: 10 },
                           callback: function(v) { return v + ' s'; } },
                  grid: { color: 'rgba(255,255,255,0.05)' },
                  title: { display: true, text: 'Time (s)', color: '#888' },
               },
               y: {
                  ticks: { color: '#888', font: { size: 10 },
                           callback: function(v) { return Math.round(v * 100) + '%'; } },
                  grid: { color: 'rgba(255,255,255,0.08)' },
                  title: { display: true, text: 'Normalised angle', color: '#888' },
                  suggestedMin: -0.2,
                  suggestedMax: 1.4,
               },
            },
         },
      });
   }

   // ---- Feel → gain mapping ----
   function applyFeelToGains() {
      var aggr     = parseInt(document.getElementById('feelAggressive').value) / 100;
      var smooth   = parseInt(document.getElementById('feelSmooth').value)     / 100;
      var posHold  = parseInt(document.getElementById('feelPosHold').value)    / 100;
      var stick    = parseInt(document.getElementById('feelStick').value);

      // Clone base gains
      stepCurrentGains = Object.assign({}, stepBaseGains);

      ['roll','pitch'].forEach(function(ax) {
         var names = GAIN_PARAMS[ax];
         if (stepBaseGains[names.P]) stepCurrentGains[names.P] = parseFloat((stepBaseGains[names.P] * aggr).toFixed(4));
         if (stepBaseGains[names.D]) stepCurrentGains[names.D] = parseFloat((stepBaseGains[names.D] * smooth).toFixed(4));
      });
      if (stepBaseGains['PSC_POSXY_P']) stepCurrentGains['PSC_POSXY_P'] = parseFloat((stepBaseGains['PSC_POSXY_P'] * posHold).toFixed(4));
      stepCurrentGains['RC_FEEL_RP'] = stick;

      // Update expert inputs to reflect feel changes
      updateExpertInputs();
      renderStepChart();
   }

   function updateExpertInputs() {
      var axes = ['roll', 'pitch', 'yaw'];
      var ids  = { roll: 'Roll', pitch: 'Pitch', yaw: 'Yaw' };
      axes.forEach(function(ax) {
         var names = GAIN_PARAMS[ax];
         var sfx   = ids[ax];
         var p = document.getElementById('exp' + sfx + 'P');
         var i = document.getElementById('exp' + sfx + 'I');
         var d = document.getElementById('exp' + sfx + 'D');
         if (p) p.value = stepCurrentGains[names.P] || '';
         if (i) i.value = stepCurrentGains[names.I] || '';
         if (d) d.value = stepCurrentGains[names.D] || '';
      });
   }

   function applyExpertToGains() {
      stepCurrentGains = Object.assign({}, stepBaseGains);
      var axes = ['roll', 'pitch', 'yaw'];
      var ids  = { roll: 'Roll', pitch: 'Pitch', yaw: 'Yaw' };
      axes.forEach(function(ax) {
         var names = GAIN_PARAMS[ax];
         var sfx   = ids[ax];
         var p = document.getElementById('exp' + sfx + 'P');
         var i = document.getElementById('exp' + sfx + 'I');
         var d = document.getElementById('exp' + sfx + 'D');
         if (p && p.value !== '') stepCurrentGains[names.P] = parseFloat(p.value);
         if (i && i.value !== '') stepCurrentGains[names.I] = parseFloat(i.value);
         if (d && d.value !== '') stepCurrentGains[names.D] = parseFloat(d.value);
      });
      renderStepChart();
   }

   function initStepPredictor() {
      var p = window._app.flatParams;
      var noConn = document.getElementById('stepPredNoConn');
      var panel  = document.getElementById('stepPredPanel');
      if (!p || Object.keys(p).length === 0) {
         if (noConn) noConn.style.display = 'flex';
         if (panel)  panel.style.display  = 'none';
         return;
      }
      if (noConn) noConn.style.display = 'none';
      if (panel)  panel.style.display  = 'block';

      // Load FC gains into base
      stepBaseGains    = Object.assign({}, p);
      stepCurrentGains = Object.assign({}, p);

      // Populate feel sliders to neutral
      ['feelAggressive','feelSmooth','feelPosHold'].forEach(function(id) {
         var el = document.getElementById(id);
         if (el) el.value = 100;
      });
      var stickEl = document.getElementById('feelStick');
      if (stickEl) stickEl.value = p['RC_FEEL_RP'] || 50;

      updateExpertInputs();
      updateFeelLabels();
      renderStepChart();
   }

   function updateFeelLabels() {
      var map = { feelAggressive: 'feelAggressiveVal', feelSmooth: 'feelSmoothVal',
                  feelPosHold: 'feelPosHoldVal', feelStick: 'feelStickVal' };
      for (var id in map) {
         var el  = document.getElementById(id);
         var lbl = document.getElementById(map[id]);
         if (!el || !lbl) continue;
         lbl.textContent = (id === 'feelStick') ? el.value : (parseInt(el.value) / 100).toFixed(2) + '×';
      }
   }

   // ---- Wire up all controls ----

   // Axis buttons
   document.querySelectorAll('[data-stepaxis]').forEach(function(btn) {
      btn.addEventListener('click', function() {
         document.querySelectorAll('[data-stepaxis]').forEach(function(b) { b.classList.remove('active'); });
         btn.classList.add('active');
         stepAxis = btn.dataset.stepaxis;
         renderStepChart();
      });
   });

   // Mode toggle
   var feelBtn   = document.getElementById('stepModeFeelBtn');
   var expertBtn = document.getElementById('stepModeExpertBtn');
   if (feelBtn) feelBtn.addEventListener('click', function() {
      stepMode = 'feel';
      feelBtn.classList.add('active'); expertBtn.classList.remove('active');
      document.getElementById('stepFeelMode').style.display   = 'grid';
      document.getElementById('stepExpertMode').style.display = 'none';
   });
   if (expertBtn) expertBtn.addEventListener('click', function() {
      stepMode = 'expert';
      expertBtn.classList.add('active'); feelBtn.classList.remove('active');
      document.getElementById('stepFeelMode').style.display   = 'none';
      document.getElementById('stepExpertMode').style.display = 'grid';
   });

   // Feel sliders
   ['feelAggressive','feelSmooth','feelPosHold','feelStick'].forEach(function(id) {
      var el = document.getElementById(id);
      if (el) el.addEventListener('input', function() { updateFeelLabels(); applyFeelToGains(); });
   });

   // Expert inputs
   ['expRollP','expRollI','expRollD','expPitchP','expPitchI','expPitchD','expYawP','expYawI','expYawD'].forEach(function(id) {
      var el = document.getElementById(id);
      if (el) el.addEventListener('input', applyExpertToGains);
   });

   // Apply to FC
   var stepApplyBtn = document.getElementById('stepApplyBtn');
   if (stepApplyBtn) {
      stepApplyBtn.addEventListener('click', function() {
         // Compute diff vs base gains
         var diff = {};
         var tracked = [];
         ['roll','pitch'].forEach(function(ax) {
            var names = GAIN_PARAMS[ax];
            ['P','I','D'].forEach(function(g) {
               var name = names[g];
               if (name && stepCurrentGains[name] !== undefined &&
                   stepCurrentGains[name] !== stepBaseGains[name]) {
                  diff[name] = stepCurrentGains[name];
                  tracked.push(name + '=' + stepCurrentGains[name]);
               }
            });
         });
         if (!diff['RC_FEEL_RP'] && stepCurrentGains['RC_FEEL_RP'] !== stepBaseGains['RC_FEEL_RP']) {
            diff['RC_FEEL_RP'] = stepCurrentGains['RC_FEEL_RP'];
         }
         if (Object.keys(diff).length === 0) {
            alert('No changes to apply.'); return;
         }
         if (!confirm('Apply ' + Object.keys(diff).length + ' parameter change(s) to the FC?\n\n' + tracked.join('\n'))) return;

         fetch('/api/parameters', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(diff),
         })
         .then(function(r) { return r.json(); })
         .then(function(result) {
            if (result.status === 'success') {
               stepBaseGains = Object.assign({}, stepCurrentGains);
               alert('Parameters applied successfully.');
            } else {
               alert('Failed: ' + (result.message || 'Unknown error'));
            }
         })
         .catch(function(e) { alert('Error: ' + e.message); });
      });
   }

   // Reset to FC values
   var stepResetBtn = document.getElementById('stepResetBtn');
   if (stepResetBtn) {
      stepResetBtn.addEventListener('click', function() {
         stepCurrentGains = Object.assign({}, stepBaseGains);
         ['feelAggressive','feelSmooth','feelPosHold'].forEach(function(id) {
            var el = document.getElementById(id); if (el) el.value = 100;
         });
         updateFeelLabels(); updateExpertInputs(); renderStepChart();
      });
   }

   // Ask JARVIS
   var stepAskJarvisBtn = document.getElementById('stepAskJarvisBtn');
   if (stepAskJarvisBtn) {
      stepAskJarvisBtn.addEventListener('click', function() {
         var names = GAIN_PARAMS[stepAxis];
         var query = 'My ' + stepAxis + ' axis PID gains are: ' +
            'P=' + (stepCurrentGains[names.P] || '?') + ', ' +
            'I=' + (stepCurrentGains[names.I] || '?') + ', ' +
            'D=' + (stepCurrentGains[names.D] || '?') + '. ' +
            'The step response shows ' + (function() {
               // Quick overshoot check from last simulation
               var g = getGainsForAxis(stepAxis);
               var data = simulateStepResponse(g.P_att, g.P_rate, g.I_rate, g.D_rate, g.accel_max);
               var maxY = Math.max.apply(null, data.map(function(d){ return d.y; }));
               var overshoot = Math.round((maxY - 1) * 100);
               return overshoot > 0 ? overshoot + '% overshoot' : 'no overshoot';
            }()) + '. Are these gains well tuned? Any suggestions?';
         var logTabBtn = document.querySelector('.menu-item[data-tab="logs"]');
         if (logTabBtn) logTabBtn.click();
         setTimeout(function() {
            var analystBtn = document.querySelector('#logs-tab .subtab-btn[data-subtab="logs-ai-analyst"]');
            if (analystBtn) analystBtn.click();
            var input = document.getElementById('logAnalystInput');
            if (input) { input.value = query; input.focus(); }
         }, 150);
      });
   }

   // Ask JARVIS button for Filter Visualizer
   var filterAskJarvis = document.getElementById('filterVizAskJarvis');
   if (filterAskJarvis) {
      filterAskJarvis.addEventListener('click', function() {
         var p = window._app.flatParams || {};
         var query = 'Review my ArduPilot filter configuration: ' +
            'Gyro LPF=' + (p['INS_GYRO_FILTER'] || 'N/A') + ' Hz, ' +
            'HNTCH enabled=' + (p['INS_HNTCH_ENABLE'] || 0) +
            ' freq=' + (p['INS_HNTCH_FREQ'] || 0) + ' Hz' +
            ' bw=' + (p['INS_HNTCH_BW'] || 0) + ' Hz' +
            ' harmonics=' + (p['INS_HNTCH_HMNCS'] || 1) + ', ' +
            'D-term LPF=' + (p['ATC_RAT_RLL_FLTD'] || 'N/A') + ' Hz. ' +
            'Is my filter stack well configured? Any phase lag concerns?';
         // Switch to AI Analyst in Logs tab for response
         var logTabBtn = document.querySelector('.menu-item[data-tab="logs"]');
         if (logTabBtn) logTabBtn.click();
         setTimeout(function() {
            var analystBtn = document.querySelector('#logs-tab .subtab-btn[data-subtab="logs-ai-analyst"]');
            if (analystBtn) analystBtn.click();
            var input = document.getElementById('logAnalystInput');
            if (input) { input.value = query; input.focus(); }
         }, 150);
      });
   }
});
