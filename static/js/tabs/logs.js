// ===== logs.js — Log Upload, Chart Rendering, Sub-tabs + AI Analyst =====

document.addEventListener('DOMContentLoaded', function() {
   // ---- Elements ----
   var fetchLogsButton = document.getElementById('fetchLogsButton');
   var logContent = document.getElementById('logContent');
   var uploadArea = document.getElementById('logUploadArea');
   var fileInput = document.getElementById('logFileInput');
   var statusBar = document.getElementById('logStatusBar');
   var statusText = document.getElementById('logStatusText');
   var vizArea = document.getElementById('logVizArea');

   var chartInstances = [];

   // ---- Sub-tab switching ----
   document.querySelectorAll('#logs-tab .subtab-btn').forEach(function(btn) {
      btn.addEventListener('click', function() {
         document.querySelectorAll('#logs-tab .subtab-btn').forEach(function(b) { b.classList.remove('active'); });
         document.querySelectorAll('#logs-tab .subtab-panel').forEach(function(p) { p.style.display = 'none'; });
         btn.classList.add('active');
         var target = document.getElementById(btn.dataset.subtab);
         if (target) target.style.display = 'block';
      });
   });

   // ---- AI Analyst panel elements ----
   var summaryChips   = document.getElementById('logSummaryChips');
   var analystNoLog   = document.getElementById('logAnalystNoLog');
   var analystPanel   = document.getElementById('logAnalystPanel');
   var analystMessages= document.getElementById('logAnalystMessages');
   var analystInput   = document.getElementById('logAnalystInput');
   var analystSend    = document.getElementById('logAnalystSend');

   function showAnalystPanel() {
      if (analystNoLog)  analystNoLog.style.display = 'none';
      if (analystPanel)  analystPanel.style.display  = 'flex';
      if (summaryChips)  summaryChips.style.display  = 'flex';
      // Add a welcome message if panel is empty
      if (analystMessages && analystMessages.children.length === 0) {
         addAnalystMessage(
            '<i class="fas fa-robot"></i> Log loaded. Ask me anything about this flight — ' +
            'vibration, battery, GPS quality, flight modes, failsafes, or tuning.',
            'jarvis'
         );
      }
   }

   function addAnalystMessage(text, role) {
      if (!analystMessages) return;
      var div = document.createElement('div');
      div.className = 'analyst-msg analyst-msg-' + (role === 'user' ? 'user' : 'jarvis');
      div.innerHTML = text;
      analystMessages.appendChild(div);
      analystMessages.scrollTop = analystMessages.scrollHeight;
   }

   function sendAnalystQuery() {
      if (!analystInput || !analystInput.value.trim()) return;
      var query = analystInput.value.trim();
      analystInput.value = '';
      addAnalystMessage(query, 'user');
      addAnalystMessage('<i class="fas fa-circle-notch fa-spin"></i> Analysing...', 'jarvis');

      var socket = window._app && window._app.socket;
      if (socket) {
         var provider = (document.getElementById('chatProviderSelect') || {}).value || 'gemini';
         socket.emit('chat_message', { message: query, provider: provider });
      }
   }

   if (analystSend) {
      analystSend.addEventListener('click', sendAnalystQuery);
   }
   if (analystInput) {
      analystInput.addEventListener('keydown', function(e) {
         if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendAnalystQuery(); }
      });
   }

   // Listen for chat_response and route to analyst panel when log is active
   function setupAnalystSocketHandler() {
      var socket = window._app && window._app.socket;
      if (!socket) { setTimeout(setupAnalystSocketHandler, 100); return; }
      socket.on('chat_response', function(data) {
         if (!window._app.logLoaded) return;
         // Remove the last "Analysing..." spinner
         var spinning = analystMessages && analystMessages.querySelector('.analyst-msg-jarvis:last-child .fa-spin');
         if (spinning) spinning.closest('.analyst-msg').remove();

         var text = '';
         if (data.error) {
            text = '<span style="color:var(--danger-color);">' + data.error + '</span>';
         } else if (data.source === 'log_analysis' && data.analysis) {
            // Log analysis: markdown text
            text = typeof window._app.renderSimpleMarkdown === 'function'
               ? window._app.renderSimpleMarkdown(data.analysis)
               : data.analysis;
         } else if (data.response && typeof data.response === 'object') {
            // JARVIS dict response: extract message + format extras
            var resp = data.response;
            text = (resp.message || '') + '<br>';
            if (typeof window._app.formatResponse === 'function') {
               text += window._app.formatResponse(resp);
            }
         } else if (data.response && typeof data.response === 'string') {
            text = data.response;
         } else if (data.message && typeof data.message === 'string') {
            text = data.message;
         } else {
            text = 'No response.';
         }

         if (text) addAnalystMessage(text, 'jarvis');
      });
   }
   setupAnalystSocketHandler();

   // ---- Auto-summary chips ----
   function renderSummaryChips(stats) {
      if (!summaryChips) return;
      summaryChips.innerHTML = '';

      function chip(icon, label, warn) {
         var el = document.createElement('span');
         el.className = 'log-summary-chip' + (warn ? ' log-summary-chip-warn' : ' log-summary-chip-ok');
         el.innerHTML = '<i class="fas fa-' + icon + '"></i> ' + label;
         summaryChips.appendChild(el);
      }

      if (stats.duration_s !== undefined) {
         var m = Math.floor(stats.duration_s / 60), s = stats.duration_s % 60;
         chip('clock', m + 'm ' + s + 's', false);
      }
      if (stats.max_alt_m !== undefined) {
         chip('mountain', stats.max_alt_m + ' m max alt', false);
      }
      if (stats.gps_fix) {
         chip('satellite', stats.gps_fix + (stats.gps_dropout ? ' (dropout)' : ''), !!stats.gps_dropout);
      }
      if (stats.vibe_alerts && stats.vibe_alerts.length) {
         var peak = stats.vibe_alerts[0];
         chip('exclamation-triangle', 'Vibe ' + peak.axis + ' ' + peak.value + ' m/s² @ ' + peak.time_s + 's', true);
      } else if (stats.duration_s) {
         chip('check-circle', 'Vibration OK', false);
      }
      if (stats.min_volt !== undefined) {
         var lowBatt = stats.min_volt < 3.5;
         chip('battery-half', stats.min_volt + 'V min', lowBatt);
      }
      if (stats.modes && stats.modes.length) {
         chip('gamepad', stats.modes.join(' → '), false);
      }
      if (stats.errors && stats.errors.length) {
         chip('times-circle', stats.errors.length + ' failsafe event(s)', true);
      }
   }

   // Expose logLoaded flag on _app so chat.js can check it
   window._app.logLoaded = false;

   // ---- File Upload ----
   if (uploadArea && fileInput) {
      uploadArea.addEventListener('click', function() {
         fileInput.click();
      });

      fileInput.addEventListener('change', function() {
         if (fileInput.files.length > 0) {
            uploadFile(fileInput.files[0]);
         }
      });

      // Drag and drop
      uploadArea.addEventListener('dragover', function(e) {
         e.preventDefault();
         uploadArea.classList.add('drag-over');
      });
      uploadArea.addEventListener('dragleave', function() {
         uploadArea.classList.remove('drag-over');
      });
      uploadArea.addEventListener('drop', function(e) {
         e.preventDefault();
         uploadArea.classList.remove('drag-over');
         if (e.dataTransfer.files.length > 0) {
            uploadFile(e.dataTransfer.files[0]);
         }
      });
   }

   function uploadFile(file) {
      var ext = file.name.split('.').pop().toLowerCase();
      if (ext !== 'bin' && ext !== 'tlog') {
         showStatus('Unsupported file type. Use .bin or .tlog', true);
         return;
      }

      uploadArea.classList.add('uploading');
      showStatus('Uploading and parsing ' + file.name + '...');

      var formData = new FormData();
      formData.append('file', file);

      fetch('/api/upload_log', {
         method: 'POST',
         body: formData
      })
      .then(function(resp) { return resp.json(); })
      .then(function(data) {
         uploadArea.classList.remove('uploading');
         if (data.status === 'success') {
            window._app.logLoaded = true;
            showStatus(data.message);
            clearCharts();
            renderDefaultCharts(data.summary.message_types);

            // Init Spectrum panel
            initSpectrumPanel();

            // Fetch flight summary stats and populate AI Analyst panel
            fetch('/api/log_summary')
               .then(function(r) { return r.json(); })
               .then(function(s) {
                  if (s.status === 'success') {
                     renderSummaryChips(s.stats);
                     showAnalystPanel();
                  }
               })
               .catch(function() {});  // non-critical
         } else {
            showStatus(data.message, true);
         }
      })
      .catch(function(err) {
         uploadArea.classList.remove('uploading');
         showStatus('Upload failed: ' + err.message, true);
      });
   }

   function showStatus(msg, isError) {
      if (statusBar && statusText) {
         statusBar.style.display = 'flex';
         statusText.textContent = msg;
         statusText.className = isError ? 'status-error' : 'status-success';
      }
   }

   // ---- Chart rendering (exposed to chat.js via window._app) ----
   window._app.renderLogCharts = function(chartConfigs) {
      chartConfigs.forEach(function(cfg) {
         fetchAndRenderChart(cfg);
      });
   };

   function clearCharts() {
      chartInstances.forEach(function(c) { c.destroy(); });
      chartInstances = [];
      if (vizArea) vizArea.innerHTML = '';
   }

   function fetchAndRenderChart(cfg) {
      var msgType = cfg.msg_type;
      if (!msgType) return;

      fetch('/api/log_message/' + encodeURIComponent(msgType) + '?max_points=500')
      .then(function(resp) { return resp.json(); })
      .then(function(result) {
         if (result.status !== 'success' || !result.data || result.data.length === 0) {
            console.warn('No data for chart:', msgType);
            return;
         }
         createChart(cfg, result.data);
      })
      .catch(function(err) {
         console.error('Error fetching chart data:', err);
      });
   }

   function createChart(cfg, data) {
      var card = document.createElement('div');
      card.className = 'log-chart-card';
      var title = document.createElement('h4');
      title.textContent = cfg.title || cfg.msg_type;
      card.appendChild(title);
      var canvas = document.createElement('canvas');
      card.appendChild(canvas);
      vizArea.appendChild(card);

      var xField = cfg.x_field || 'TimeUS';
      var yFields = cfg.y_fields || [];
      if (yFields.length === 0) return;

      var labels = data.map(function(d, i) {
         var val = d[xField];
         if (xField === 'TimeUS' && typeof val === 'number') {
            return (val / 1000000).toFixed(1);
         }
         return val !== undefined ? val : i;
      });

      var colors = ['#3498db', '#e74c3c', '#2ecc71', '#f1c40f', '#9b59b6', '#e67e22', '#1abc9c', '#e84393'];
      var datasets = yFields.map(function(field, idx) {
         return {
            label: field,
            data: data.map(function(d) { return d[field]; }),
            borderColor: colors[idx % colors.length],
            backgroundColor: 'transparent',
            borderWidth: 1.5,
            pointRadius: 0,
            tension: 0.1,
         };
      });

      var chartType = cfg.type || 'line';
      var chart = new Chart(canvas, {
         type: chartType,
         data: { labels: labels, datasets: datasets },
         options: {
            responsive: true,
            maintainAspectRatio: true,
            animation: false,
            plugins: {
               legend: { labels: { color: '#ccc', font: { size: 11 } } },
            },
            scales: {
               x: {
                  ticks: { color: '#888', maxTicksLimit: 10, font: { size: 10 } },
                  grid: { color: 'rgba(255,255,255,0.05)' },
                  title: { display: true, text: xField === 'TimeUS' ? 'Time (s)' : xField, color: '#888' },
               },
               y: {
                  ticks: { color: '#888', font: { size: 10 } },
                  grid: { color: 'rgba(255,255,255,0.08)' },
                  title: { display: !!cfg.y_label, text: cfg.y_label || '', color: '#888' },
               },
            },
         },
      });
      chartInstances.push(chart);
   }

   // ---- Default charts after upload ----
   // Defines which message types to auto-plot and which fields
   var DEFAULT_CHARTS = [
      { msg_type: 'ATT',  title: 'Attitude',           y_fields: ['Roll', 'Pitch', 'Yaw'],           y_label: 'Degrees' },
      { msg_type: 'GPS',  title: 'GPS',                 y_fields: ['Alt', 'Spd', 'NSats'],            y_label: '' },
      { msg_type: 'BARO', title: 'Barometer',           y_fields: ['Alt', 'Press'],                   y_label: '' },
      { msg_type: 'BAT',  title: 'Battery',             y_fields: ['Volt', 'Curr'],                   y_label: '' },
      { msg_type: 'RCOU', title: 'RC Output (Motors)',   y_fields: ['C1', 'C2', 'C3', 'C4'],          y_label: 'PWM' },
      { msg_type: 'RCIN', title: 'RC Input (Sticks)',    y_fields: ['C1', 'C2', 'C3', 'C4'],          y_label: 'PWM' },
      { msg_type: 'VIBE', title: 'Vibration',           y_fields: ['VibeX', 'VibeY', 'VibeZ'],        y_label: 'm/s/s' },
      { msg_type: 'CTUN', title: 'Control Tuning',      y_fields: ['Alt', 'DAlt', 'TAlt'],            y_label: 'm' },
      { msg_type: 'MOT',  title: 'Motor Output',        y_fields: ['Mot1', 'Mot2', 'Mot3', 'Mot4'],   y_label: '' },
   ];

   function renderDefaultCharts(messageTypes) {
      if (!messageTypes) return;

      // Build message type selector for custom exploration
      renderMessageTypeSelector(messageTypes);

      // Render default charts for types that exist in this log
      var rendered = 0;
      DEFAULT_CHARTS.forEach(function(cfg) {
         if (messageTypes[cfg.msg_type]) {
            // Filter y_fields to only those that actually exist
            var availableFields = messageTypes[cfg.msg_type].fields || [];
            var validFields = cfg.y_fields.filter(function(f) {
               return availableFields.indexOf(f) !== -1;
            });
            if (validFields.length > 0) {
               fetchAndRenderChart({
                  msg_type: cfg.msg_type,
                  title: cfg.title,
                  y_fields: validFields,
                  y_label: cfg.y_label,
                  x_field: 'TimeUS'
               });
               rendered++;
            }
         }
      });

      if (rendered === 0) {
         // Fallback: render first few available message types
         var types = Object.keys(messageTypes);
         types.slice(0, 4).forEach(function(msgType) {
            var info = messageTypes[msgType];
            if (info.fields && info.fields.length > 0) {
               // Pick up to 4 numeric-looking fields (skip TimeUS)
               var fields = info.fields.filter(function(f) { return f !== 'TimeUS'; }).slice(0, 4);
               if (fields.length > 0) {
                  fetchAndRenderChart({
                     msg_type: msgType,
                     title: msgType,
                     y_fields: fields,
                     x_field: 'TimeUS'
                  });
               }
            }
         });
      }
   }

   function renderMessageTypeSelector(messageTypes) {
      // Create a selector bar above the charts
      var selectorDiv = document.createElement('div');
      selectorDiv.className = 'log-msg-selector';
      selectorDiv.style.cssText = 'display:flex; gap:0.5rem; align-items:center; margin-bottom:1rem; flex-wrap:wrap;';

      var label = document.createElement('span');
      label.textContent = 'Add chart:';
      label.style.cssText = 'color:#aaa; font-size:0.85rem; font-weight:bold;';
      selectorDiv.appendChild(label);

      var select = document.createElement('select');
      select.style.cssText = 'padding:0.4rem; border-radius:5px; border:1px solid #444; background:#1a1a2e; color:#e0e0e0; font-size:0.85rem;';
      var defaultOpt = document.createElement('option');
      defaultOpt.textContent = '-- Select message type --';
      defaultOpt.value = '';
      select.appendChild(defaultOpt);

      var sortedTypes = Object.keys(messageTypes).sort();
      sortedTypes.forEach(function(t) {
         var opt = document.createElement('option');
         opt.value = t;
         opt.textContent = t + ' (' + messageTypes[t].count + ')';
         select.appendChild(opt);
      });
      selectorDiv.appendChild(select);

      // Field multi-select (populated when msg type changes)
      var fieldContainer = document.createElement('span');
      fieldContainer.id = 'logFieldSelect';
      selectorDiv.appendChild(fieldContainer);

      var addBtn = document.createElement('button');
      addBtn.textContent = 'Add';
      addBtn.style.cssText = 'padding:0.4rem 0.8rem; border:none; border-radius:5px; background:var(--primary-color); color:white; cursor:pointer; font-size:0.85rem;';
      selectorDiv.appendChild(addBtn);

      select.addEventListener('change', function() {
         fieldContainer.innerHTML = '';
         var msgType = select.value;
         if (!msgType || !messageTypes[msgType] || !messageTypes[msgType].fields) return;

         var fields = messageTypes[msgType].fields.filter(function(f) { return f !== 'TimeUS'; });
         fields.forEach(function(f) {
            var lbl = document.createElement('label');
            lbl.style.cssText = 'color:#ccc; font-size:0.8rem; margin-left:0.3rem; cursor:pointer;';
            var cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.value = f;
            cb.checked = fields.indexOf(f) < 4; // check first 4 by default
            cb.style.marginRight = '2px';
            lbl.appendChild(cb);
            lbl.appendChild(document.createTextNode(f));
            fieldContainer.appendChild(lbl);
         });
      });

      addBtn.addEventListener('click', function() {
         var msgType = select.value;
         if (!msgType) return;
         var checked = [];
         fieldContainer.querySelectorAll('input[type="checkbox"]:checked').forEach(function(cb) {
            checked.push(cb.value);
         });
         if (checked.length === 0) return;
         fetchAndRenderChart({
            msg_type: msgType,
            title: msgType,
            y_fields: checked,
            x_field: 'TimeUS'
         });
      });

      vizArea.appendChild(selectorDiv);
   }

   // ---- Spectrum (FFT) sub-tab ----

   var spectrumChart    = null;
   var spectrumAxis     = 'roll';    // roll | pitch | yaw
   var spectrumSrc      = 'imu';     // imu  | rate
   var spectrumCachedData = {};      // keyed by "src:axis"

   var spectrumNoLog  = document.getElementById('spectrumNoLog');
   var spectrumPanel  = document.getElementById('spectrumPanel');
   var spectrumPeaks  = document.getElementById('spectrumPeaks');
   var spectrumCanvas = document.getElementById('spectrumCanvas');
   var spectrumAskJarvis = document.getElementById('spectrumAskJarvis');

   // Axis and source toggle buttons
   document.querySelectorAll('.spectrum-axis-btn').forEach(function(btn) {
      btn.addEventListener('click', function() {
         document.querySelectorAll('.spectrum-axis-btn').forEach(function(b) { b.classList.remove('active'); });
         btn.classList.add('active');
         spectrumAxis = btn.dataset.axis;
         renderSpectrum();
      });
   });
   document.querySelectorAll('.spectrum-src-btn').forEach(function(btn) {
      btn.addEventListener('click', function() {
         document.querySelectorAll('.spectrum-src-btn').forEach(function(b) { b.classList.remove('active'); });
         btn.classList.add('active');
         spectrumSrc = btn.dataset.src;
         renderSpectrum();
      });
   });

   // --- Pure JS Cooley-Tukey radix-2 FFT ---
   function fft(re, im) {
      var n = re.length;
      // Bit-reversal permutation
      for (var i = 1, j = 0; i < n; i++) {
         var bit = n >> 1;
         for (; j & bit; bit >>= 1) j ^= bit;
         j ^= bit;
         if (i < j) {
            var t = re[i]; re[i] = re[j]; re[j] = t;
            t = im[i]; im[i] = im[j]; im[j] = t;
         }
      }
      // Butterfly
      for (var len = 2; len <= n; len <<= 1) {
         var ang = -2 * Math.PI / len;
         var wRe = Math.cos(ang), wIm = Math.sin(ang);
         for (var i = 0; i < n; i += len) {
            var curRe = 1, curIm = 0;
            for (var j = 0; j < len / 2; j++) {
               var uRe = re[i+j],      uIm = im[i+j];
               var vRe = re[i+j+len/2]*curRe - im[i+j+len/2]*curIm;
               var vIm = re[i+j+len/2]*curIm + im[i+j+len/2]*curRe;
               re[i+j]         =  uRe + vRe;  im[i+j]         = uIm + vIm;
               re[i+j+len/2]   =  uRe - vRe;  im[i+j+len/2]  = uIm - vIm;
               var newRe = curRe*wRe - curIm*wIm;
               curIm = curRe*wIm + curIm*wRe;
               curRe = newRe;
            }
         }
      }
   }

   function computeSpectrum(samples, timeUS) {
      // Infer sample rate from median TimeUS delta
      var dts = [];
      for (var i = 1; i < Math.min(samples.length, 200); i++) {
         var d = (timeUS[i] - timeUS[i-1]) / 1e6;
         if (d > 0 && d < 1) dts.push(d);
      }
      dts.sort(function(a,b){return a-b;});
      var medianDt = dts[Math.floor(dts.length/2)] || 0.0025; // default 400 Hz
      var sampleRate = 1 / medianDt;

      // Next power of 2, max 4096
      var n = 1;
      while (n < samples.length && n < 4096) n <<= 1;

      var re = new Float64Array(n);
      var im = new Float64Array(n);

      // Apply Hann window
      for (var i = 0; i < n; i++) {
         var s = i < samples.length ? (samples[i] || 0) : 0;
         var w = 0.5 * (1 - Math.cos(2 * Math.PI * i / (n - 1)));
         re[i] = s * w;
      }

      fft(re, im);

      // Magnitude in dB (one-sided spectrum up to Nyquist)
      var half = n / 2;
      var freqs = [], dbs = [];
      for (var i = 1; i < half; i++) {
         var mag = Math.sqrt(re[i]*re[i] + im[i]*im[i]) / n * 2;
         var db  = 20 * Math.log10(mag + 1e-12);
         freqs.push(sampleRate * i / n);
         dbs.push(db);
      }
      return { freqs: freqs, dbs: dbs, sampleRate: Math.round(sampleRate) };
   }

   function findPeaks(freqs, dbs, topN) {
      // Simple local-max peak finder: must be higher than both neighbours by 3 dB
      var peaks = [];
      for (var i = 1; i < dbs.length - 1; i++) {
         if (dbs[i] > dbs[i-1] && dbs[i] > dbs[i+1] && freqs[i] > 5) {
            peaks.push({ freq: Math.round(freqs[i]), db: Math.round(dbs[i]) });
         }
      }
      peaks.sort(function(a,b){ return b.db - a.db; });
      return peaks.slice(0, topN || 5);
   }

   // Field names by axis and source
   var FIELD_MAP = {
      imu:  { roll: 'GyrX', pitch: 'GyrY', yaw: 'GyrZ' },
      rate: { roll: 'Roll', pitch: 'Pitch', yaw: 'Yaw'  },
   };
   var MSG_TYPE = { imu: 'IMU', rate: 'RATE' };

   function renderSpectrum() {
      if (!window._app.logLoaded) return;

      var cacheKey = spectrumSrc + ':' + spectrumAxis;
      if (spectrumCachedData[cacheKey]) {
         drawSpectrumChart(spectrumCachedData[cacheKey]);
         return;
      }

      var msgType = MSG_TYPE[spectrumSrc];
      var field   = FIELD_MAP[spectrumSrc][spectrumAxis];

      if (spectrumCanvas) spectrumCanvas.style.opacity = '0.3';

      fetch('/api/log_message/' + encodeURIComponent(msgType) + '?max_points=4096')
         .then(function(r) { return r.json(); })
         .then(function(result) {
            if (result.status !== 'success' || !result.data || result.data.length < 32) {
               if (spectrumPeaks) spectrumPeaks.textContent = 'No ' + msgType + ' data in this log.';
               if (spectrumCanvas) spectrumCanvas.style.opacity = '1';
               return;
            }
            var samples  = result.data.map(function(d) { return d[field] || 0; });
            var timeUS   = result.data.map(function(d) { return d['TimeUS'] || 0; });
            var spectrum = computeSpectrum(samples, timeUS);
            var peaks    = findPeaks(spectrum.freqs, spectrum.dbs, 5);
            spectrumCachedData[cacheKey] = { spectrum: spectrum, peaks: peaks, field: field, msgType: msgType };
            drawSpectrumChart(spectrumCachedData[cacheKey]);
         })
         .catch(function(e) {
            if (spectrumPeaks) spectrumPeaks.textContent = 'Error loading data: ' + e.message;
            if (spectrumCanvas) spectrumCanvas.style.opacity = '1';
         });
   }

   function drawSpectrumChart(cached) {
      var spectrum = cached.spectrum;
      var peaks    = cached.peaks;

      // Limit to 0–500 Hz display range
      var maxHz = Math.min(500, spectrum.sampleRate / 2);
      var freqs  = [], dbs = [];
      for (var i = 0; i < spectrum.freqs.length; i++) {
         if (spectrum.freqs[i] <= maxHz) {
            freqs.push(spectrum.freqs[i]);
            dbs.push(spectrum.dbs[i]);
         }
      }

      // Render peaks summary
      if (spectrumPeaks) {
         var label = (spectrumSrc === 'imu' ? 'Raw Gyro' : 'PID Rate') +
                     ' — ' + cached.field + ' @ ' + spectrum.sampleRate + ' Hz sample rate  |  ' +
                     'Peaks: ' + (peaks.length ? peaks.map(function(p){ return p.freq + ' Hz (' + p.db + ' dB)'; }).join('  ') : 'none detected');
         spectrumPeaks.textContent = label;
      }

      // Build filter overlay lines from flatParams (set by parameters.js after fetch)
      var overlayLines = [];
      var fp = window._app.flatParams || {};
      var gyroLpf   = parseFloat(fp['INS_GYRO_FILTER']) || 0;
      var hntchEn   = parseFloat(fp['INS_HNTCH_ENABLE']) || 0;
      var hntchFreq = parseFloat(fp['INS_HNTCH_FREQ'])  || 0;
      if (gyroLpf  > 0) overlayLines.push({ hz: gyroLpf,   color: '#3498db', label: 'LPF '   + gyroLpf   + ' Hz' });
      if (hntchEn && hntchFreq > 0) overlayLines.push({ hz: hntchFreq, color: '#f39c12', label: 'HNTCH ' + hntchFreq + ' Hz' });

      // Per-chart inline plugin for filter overlay lines
      var overlayPlugin = {
         id: 'spectrumOverlay',
         afterDraw: function(chart) {
            if (!overlayLines.length) return;
            var ctx = chart.ctx;
            var xScale = chart.scales.x;
            var top    = chart.chartArea.top;
            var bottom = chart.chartArea.bottom;
            overlayLines.forEach(function(line) {
               var x = xScale.getPixelForValue(line.hz);
               if (x < chart.chartArea.left || x > chart.chartArea.right) return;
               ctx.save();
               ctx.beginPath();
               ctx.moveTo(x, top);
               ctx.lineTo(x, bottom);
               ctx.strokeStyle = line.color;
               ctx.lineWidth = 1.5;
               ctx.setLineDash([5, 4]);
               ctx.stroke();
               ctx.setLineDash([]);
               ctx.font = '10px sans-serif';
               ctx.fillStyle = line.color;
               ctx.fillText(line.label, x + 3, top + 12);
               ctx.restore();
            });
         }
      };

      // Destroy old chart
      if (spectrumChart) { spectrumChart.destroy(); spectrumChart = null; }
      if (!spectrumCanvas) return;
      spectrumCanvas.style.opacity = '1';

      var ctx = spectrumCanvas.getContext('2d');
      spectrumChart = new Chart(ctx, {
         type: 'line',
         data: {
            labels: freqs.map(function(f) { return f.toFixed(1); }),
            datasets: [{
               label: cached.field + ' Spectrum',
               data: dbs,
               borderColor: '#3498db',
               backgroundColor: 'rgba(52,152,219,0.08)',
               borderWidth: 1.5,
               pointRadius: 0,
               fill: true,
               tension: 0.2,
            }],
         },
         options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
               legend: { display: false },
               tooltip: {
                  callbacks: {
                     title: function(items) { return items[0].label + ' Hz'; },
                     label: function(item)  { return item.raw.toFixed(1) + ' dB'; },
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
                  title: { display: true, text: 'Magnitude (dB)', color: '#888' },
               },
            },
         },
         // Per-chart plugin: draws filter overlay lines on this chart only
         plugins: [overlayPlugin],
      });
   }

   // Show/hide spectrum panel when log loads
   function initSpectrumPanel() {
      if (spectrumNoLog) spectrumNoLog.style.display = 'none';
      if (spectrumPanel) spectrumPanel.style.display  = 'block';
      spectrumCachedData = {}; // invalidate cache on new log
      // Pre-render if spectrum tab is currently visible
      var specTab = document.getElementById('logs-spectrum');
      if (specTab && specTab.style.display !== 'none') renderSpectrum();
   }

   // Re-render when user switches to spectrum tab
   document.querySelectorAll('#logs-tab .subtab-btn').forEach(function(btn) {
      btn.addEventListener('click', function() {
         if (btn.dataset.subtab === 'logs-spectrum' && window._app.logLoaded) {
            renderSpectrum();
         }
      });
   });

   // Ask JARVIS button
   if (spectrumAskJarvis) {
      spectrumAskJarvis.addEventListener('click', function() {
         var cacheKey = spectrumSrc + ':' + spectrumAxis;
         var cached   = spectrumCachedData[cacheKey];
         if (!cached || !cached.peaks.length) {
            alert('Run the FFT first — switch to the Spectrum tab with a log loaded.');
            return;
         }
         var peakStr  = cached.peaks.slice(0, 3).map(function(p) { return p.freq + ' Hz'; }).join(', ');
         var axis     = spectrumAxis.charAt(0).toUpperCase() + spectrumAxis.slice(1);
         var src      = spectrumSrc === 'imu' ? 'raw gyro' : 'PID rate';
         var query    = axis + ' axis ' + src + ' noise peaks at ' + peakStr +
                        '. Sample rate ' + cached.spectrum.sampleRate + ' Hz. ' +
                        'Do these peaks indicate a problem? Should I adjust the notch filter?';

         // Switch to AI Analyst tab and pre-fill query
         var analystBtn = document.querySelector('#logs-tab .subtab-btn[data-subtab="logs-ai-analyst"]');
         if (analystBtn) analystBtn.click();
         var input = document.getElementById('logAnalystInput');
         if (input) { input.value = query; input.focus(); }
      });
   }

   // ---- FC Log Fetch (existing functionality) ----
   if (fetchLogsButton) {
      fetchLogsButton.addEventListener('click', async function() {
         if (!window._app || !window._app.isConnected) {
            if (window._app && window._app.addMessage) {
               window._app.addMessage({
                  text: '<strong>System:</strong> Please connect to a drone first to fetch FC logs.',
                  time: window._app.getCurrentTime()
               });
            }
            return;
         }

         logContent.textContent = 'Fetching logs...';
         fetchLogsButton.disabled = true;

         try {
            var response = await fetch('/api/fc_logs');
            if (!response.ok) throw new Error('Failed to fetch logs: ' + response.status);
            var data = await response.json();
            if (data.status === 'success') {
               if (data.log_count === 0) {
                  logContent.innerHTML = '<span style="color:#888;">' + data.message + '</span>';
               } else {
                  var html = '<strong>' + data.message + '</strong><br>';
                  data.logs.forEach(function(log) {
                     var sizeKB = (log.size_bytes / 1024).toFixed(1);
                     html += log.filename + ' (' + sizeKB + ' KB)<br>';
                  });
                  logContent.innerHTML = html;
               }
            } else {
               logContent.innerHTML = '<span style="color:var(--danger-color);">' + (data.message || 'Failed') + '</span>';
            }
         } catch (error) {
            logContent.innerHTML = '<span style="color:var(--danger-color);">Error: ' + error.message + '</span>';
         } finally {
            fetchLogsButton.disabled = false;
         }
      });
   }
});
