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
