// ===== parameters.js — Parameter fetch, display, save =====

document.addEventListener('DOMContentLoaded', function() {
   var allParameters = {};
   var modifiedParameters = {};

   async function fetchParameters() {
      try {
         var response = await fetch('/api/parameters');
         if (!response.ok) {
            throw new Error('Failed to fetch parameters: ' + response.status);
         }

         allParameters = await response.json();
         modifiedParameters = {};
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
});
