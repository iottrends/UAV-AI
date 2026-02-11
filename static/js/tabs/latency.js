// ===== latency.js — Latency & Parameters tab =====

(function() {
   var latencyChart = null;
   var latencyData = [];
   var MAX_DATA_POINTS = 60;

   var currentLatency = null;
   var avgLatency = null;
   var maxLatency = null;
   var minLatency = null;
   var latencyStatusIndicator = null;
   var latencyStatusText = null;
   var paramPercentage = null;
   var paramCount = null;
   var paramProgressBar = null;
   var paramStatus = null;
   var paramTimeElapsed = null;
   var paramTimeRemaining = null;
   var paramCategoriesTable = null;

   document.addEventListener('DOMContentLoaded', function() {
      currentLatency = document.getElementById('currentLatency');
      avgLatency = document.getElementById('avgLatency');
      maxLatency = document.getElementById('maxLatency');
      minLatency = document.getElementById('minLatency');
      latencyStatusIndicator = document.getElementById('latencyStatusIndicator');
      latencyStatusText = document.getElementById('latencyStatusText');
      paramPercentage = document.getElementById('paramPercentage');
      paramCount = document.getElementById('paramCount');
      paramProgressBar = document.getElementById('paramProgressBar');
      paramStatus = document.getElementById('paramStatus');
      paramTimeElapsed = document.getElementById('paramTimeElapsed');
      paramTimeRemaining = document.getElementById('paramTimeRemaining');
      paramCategoriesTable = document.getElementById('paramCategoriesTable');
   });

   function initLatencyChart() {
      var ctx = document.getElementById('latencyChart');
      if (!ctx) return;

      latencyChart = new Chart(ctx, {
         type: 'line',
         data: {
            labels: Array(MAX_DATA_POINTS).fill(''),
            datasets: [{
               label: 'Latency (ms)',
               data: Array(MAX_DATA_POINTS).fill(null),
               borderColor: 'var(--primary-color)',
               backgroundColor: 'rgba(52, 152, 219, 0.1)',
               borderWidth: 2,
               fill: true,
               tension: 0.4
            }]
         },
         options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
               legend: { display: false },
               tooltip: { mode: 'index', intersect: false }
            },
            scales: {
               y: {
                  beginAtZero: true,
                  title: { display: true, text: 'Milliseconds' }
               },
               x: { display: false }
            }
         }
      });
   }

   function updateLatencyChart(newLatency) {
      if (!latencyChart) {
         initLatencyChart();
         if (!latencyChart) return;
      }

      latencyData.push(newLatency);

      if (latencyData.length > MAX_DATA_POINTS) {
         latencyData = latencyData.slice(-MAX_DATA_POINTS);
      }

      latencyChart.data.datasets[0].data = latencyData;
      latencyChart.data.labels = Array(latencyData.length).fill('');
      latencyChart.update();

      var noDataMsg = document.getElementById('noLatencyData');
      if (noDataMsg) {
         noDataMsg.style.display = latencyData.length > 0 ? 'none' : 'block';
      }
   }

   function updateLatencyAndParams(data) {
      if (data.latency !== undefined) {
         if (currentLatency) currentLatency.textContent = data.latency + ' ms';

         if (latencyStatusIndicator && latencyStatusText) {
            var statusColor, statusLabel;

            if (data.latency < 100) {
               statusColor = 'var(--success-color)';
               statusLabel = 'Excellent';
            } else if (data.latency < 200) {
               statusColor = 'var(--success-color)';
               statusLabel = 'Good';
            } else if (data.latency < 300) {
               statusColor = 'var(--warning-color)';
               statusLabel = 'Fair';
            } else {
               statusColor = 'var(--danger-color)';
               statusLabel = 'Poor';
            }

            latencyStatusIndicator.style.backgroundColor = statusColor;
            latencyStatusText.textContent = 'Latency Status: ' + statusLabel;
         }

         if (data.latency_stats) {
            if (avgLatency) avgLatency.textContent = data.latency_stats.avg + ' ms';
            if (maxLatency) maxLatency.textContent = data.latency_stats.max + ' ms';
            if (minLatency) minLatency.textContent = data.latency_stats.min + ' ms';
         }

         updateLatencyChart(data.latency);
      }

      if (data.params !== undefined) {
         var percentage = data.params.percentage || 0;
         var downloaded = data.params.downloaded || 0;
         var total = data.params.total || 0;
         var status = data.params.status || 'Not Started';
         var timeElapsed = data.params.time_elapsed || '00:00';
         var timeRemaining = data.params.time_remaining || '--:--';

         if (paramPercentage) paramPercentage.textContent = percentage + '%';
         if (paramProgressBar) paramProgressBar.style.width = percentage + '%';
         if (paramCount) paramCount.textContent = downloaded + '/' + total + ' Parameters';
         if (paramStatus) paramStatus.textContent = status;
         if (paramTimeElapsed) paramTimeElapsed.textContent = timeElapsed;
         if (paramTimeRemaining) paramTimeRemaining.textContent = timeRemaining;

         if (data.params.categories && paramCategoriesTable) {
            paramCategoriesTable.innerHTML = '';

            Object.entries(data.params.categories).forEach(function(entry) {
               var category = entry[0];
               var info = entry[1];

               var row = document.createElement('tr');

               var categoryCell = document.createElement('td');
               categoryCell.style.padding = '0.5rem';
               categoryCell.style.borderBottom = '1px solid #f0f0f0';
               categoryCell.textContent = category;

               var progressCell = document.createElement('td');
               progressCell.style.padding = '0.5rem';
               progressCell.style.borderBottom = '1px solid #f0f0f0';

               var progressBar = document.createElement('div');
               progressBar.style.height = '10px';
               progressBar.style.backgroundColor = '#f0f0f0';
               progressBar.style.borderRadius = '5px';
               progressBar.style.overflow = 'hidden';

               var progressFill = document.createElement('div');
               progressFill.style.height = '100%';
               progressFill.style.width = info.percentage + '%';
               progressFill.style.backgroundColor = 'var(--primary-color)';
               progressFill.style.borderRadius = '5px';

               progressBar.appendChild(progressFill);
               progressCell.appendChild(progressBar);

               var countCell = document.createElement('td');
               countCell.style.padding = '0.5rem';
               countCell.style.borderBottom = '1px solid #f0f0f0';
               countCell.textContent = info.downloaded + '/' + info.total;

               row.appendChild(categoryCell);
               row.appendChild(progressCell);
               row.appendChild(countCell);

               paramCategoriesTable.appendChild(row);
            });
         }
      }
   }

   // Register as system status hook
   window._app.systemStatusHooks.push(updateLatencyAndParams);
})();
