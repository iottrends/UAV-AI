// ===== logs.js — Log Upload, Chart Rendering + FC Log Fetch =====

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

            // Notify user via main chat
            window._app.addMessage({
               text: '<strong>System:</strong> Log loaded: ' + data.summary.filename +
                     ' (' + data.summary.total_messages + ' messages, ' +
                     Object.keys(data.summary.message_types).length + ' types). You can now ask questions about this flight in the chat.',
               time: window._app.getCurrentTime()
            });
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
