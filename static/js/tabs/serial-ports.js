// ===== serial-ports.js — Serial Port Configuration Tab =====

document.addEventListener('DOMContentLoaded', function() {

   // ArduPilot SERIALn_PROTOCOL values
   var PROTOCOLS = {
      '-1': 'None',
      '0': 'None',
      '1': 'MAVLink1',
      '2': 'MAVLink2',
      '3': 'FrSky D',
      '4': 'FrSky SPort',
      '5': 'GPS',
      '7': 'Alexmos Gimbal Serial',
      '8': 'SToRM32 Gimbal Serial',
      '9': 'Rangefinder',
      '10': 'FrSky SPort Passthrough',
      '11': 'Lidar360',
      '12': 'Aerotenna uLanding',
      '13': 'Beacon',
      '14': 'Volz Servo',
      '15': 'SBus Servo',
      '16': 'ESC Telemetry',
      '17': 'Devo Telemetry',
      '18': 'OpticalFlow',
      '19': 'RobotisServo',
      '20': 'NMEA Output',
      '21': 'WindVane',
      '22': 'SLCAN',
      '23': 'RCIN',
      '24': 'MegaSquirt EFI',
      '25': 'LTM',
      '26': 'RunCam',
      '27': 'HottTelem',
      '28': 'Scripting',
      '29': 'Crossfire VTX',
      '30': 'Generator',
      '31': 'Winch',
      '32': 'MSP',
      '33': 'DJI FPV OSD',
      '34': 'AirSpeed',
      '35': 'ADSB',
      '36': 'AHRS',
      '37': 'SmartAudio',
      '38': 'FETtecOneWire',
      '39': 'Torqeedo',
      '40': 'AIS',
      '41': 'CoDevESC',
      '42': 'DisplayPort',
      '43': 'MAVLink High Latency',
      '44': 'IRC Tramp'
   };

   // ArduPilot SERIALn_BAUD: stored value → actual baud rate
   var BAUD_RATES = {
      '1': '1200',
      '2': '2400',
      '4': '4800',
      '9': '9600',
      '19': '19200',
      '38': '38400',
      '57': '57600',
      '111': '111100',
      '115': '115200',
      '230': '230400',
      '460': '460800',
      '500': '500000',
      '921': '921600',
      '1500': '1500000'
   };

   // Port labels for common ArduPilot serial assignments
   var PORT_LABELS = {
      0: 'USB',
      1: 'TELEM1',
      2: 'TELEM2',
      3: 'GPS1',
      4: 'GPS2',
      5: 'SERIAL5',
      6: 'SERIAL6',
      7: 'SERIAL7'
   };

   var serialData = {};   // { 0: { protocol: '2', baud: '57' }, ... }
   var originalData = {}; // snapshot for diff
   var loaded = false;

   async function loadSerialConfig() {
      var statusEl = document.getElementById('serialStatus');
      statusEl.textContent = 'Loading...';
      statusEl.style.color = 'var(--text-muted)';

      try {
         var response = await fetch('/api/config/domains/serial_ports');
         if (!response.ok) throw new Error('HTTP ' + response.status);
         var data = await response.json();
         if (data.status !== 'success') {
            throw new Error(data.message || 'Failed to load serial configuration');
         }

         var serial = data.params || {};
         serialData = {};

         for (var n = 0; n <= 7; n++) {
            var protKey = 'SERIAL' + n + '_PROTOCOL';
            var baudKey = 'SERIAL' + n + '_BAUD';
            if (serial[protKey] !== undefined) {
               serialData[n] = {
                  protocol: String(Math.round(serial[protKey])),
                  baud: String(Math.round(serial[baudKey] || 57))
               };
            }
         }

         // Deep copy for diff
         originalData = JSON.parse(JSON.stringify(serialData));
         renderTable();
         statusEl.textContent = '';
         loaded = true;
      } catch (err) {
         console.error('Serial config load error:', err);
         statusEl.textContent = 'Error loading serial config: ' + err.message;
         statusEl.style.color = 'var(--danger-color)';
      }
   }

   function renderTable() {
      var tbody = document.getElementById('serialPortBody');
      if (!tbody) return;
      tbody.innerHTML = '';

      var ports = Object.keys(serialData).sort(function(a, b) { return a - b; });
      for (var i = 0; i < ports.length; i++) {
         var n = ports[i];
         var cfg = serialData[n];

         var tr = document.createElement('tr');

         // Port column
         var tdPort = document.createElement('td');
         tdPort.style.fontWeight = 'bold';
         tdPort.textContent = 'SERIAL' + n;
         tr.appendChild(tdPort);

         // Label column
         var tdLabel = document.createElement('td');
         tdLabel.className = 'serial-port-label';
         tdLabel.textContent = PORT_LABELS[n] || '';
         tr.appendChild(tdLabel);

         // Protocol dropdown
         var tdProto = document.createElement('td');
         var selProto = document.createElement('select');
         selProto.dataset.port = n;
         selProto.dataset.field = 'protocol';

         var protoKeys = Object.keys(PROTOCOLS).sort(function(a, b) { return Number(a) - Number(b); });
         for (var j = 0; j < protoKeys.length; j++) {
            var opt = document.createElement('option');
            opt.value = protoKeys[j];
            opt.textContent = protoKeys[j] + ' - ' + PROTOCOLS[protoKeys[j]];
            if (protoKeys[j] === cfg.protocol) opt.selected = true;
            selProto.appendChild(opt);
         }
         selProto.addEventListener('change', onDropdownChange);
         tdProto.appendChild(selProto);
         tr.appendChild(tdProto);

         // Baud dropdown
         var tdBaud = document.createElement('td');
         var selBaud = document.createElement('select');
         selBaud.dataset.port = n;
         selBaud.dataset.field = 'baud';

         var baudKeys = Object.keys(BAUD_RATES).sort(function(a, b) { return Number(a) - Number(b); });
         for (var k = 0; k < baudKeys.length; k++) {
            var opt2 = document.createElement('option');
            opt2.value = baudKeys[k];
            opt2.textContent = BAUD_RATES[baudKeys[k]];
            if (baudKeys[k] === cfg.baud) opt2.selected = true;
            selBaud.appendChild(opt2);
         }
         selBaud.addEventListener('change', onDropdownChange);
         tdBaud.appendChild(selBaud);
         tr.appendChild(tdBaud);

         tbody.appendChild(tr);
      }
   }

   function onDropdownChange(e) {
      var port = e.target.dataset.port;
      var field = e.target.dataset.field;
      serialData[port][field] = e.target.value;
   }

   function buildSerialDiff() {
      var diff = {};
      for (var n in serialData) {
         if (!originalData[n]) continue;
         if (serialData[n].protocol !== originalData[n].protocol) {
            diff['SERIAL' + n + '_PROTOCOL'] = Number(serialData[n].protocol);
         }
         if (serialData[n].baud !== originalData[n].baud) {
            diff['SERIAL' + n + '_BAUD'] = Number(serialData[n].baud);
         }
      }
      return diff;
   }

   async function previewSerialConfig() {
      var statusEl = document.getElementById('serialStatus');
      var diff = buildSerialDiff();

      if (Object.keys(diff).length === 0) {
         statusEl.textContent = 'No changes to preview.';
         statusEl.style.color = 'var(--text-muted)';
         return { hasChanges: false, diff: [] };
      }

      statusEl.textContent = 'Previewing ' + Object.keys(diff).length + ' parameter(s)...';
      statusEl.style.color = 'var(--text-muted)';

      try {
         var response = await fetch('/api/config/domains/serial_ports/preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ changes: diff })
         });
         var result = await response.json();
         if (!response.ok || result.status !== 'success') {
            throw new Error(result.message || 'Preview failed');
         }

         var changedRows = (result.diff || []).filter(function(item) { return item.changed; });
         if (!changedRows.length) {
            statusEl.textContent = 'No effective changes detected after preview.';
            statusEl.style.color = 'var(--text-muted)';
            return { hasChanges: false, diff: result.diff || [] };
         }

         statusEl.textContent = 'Preview ready: ' + changedRows.length + ' parameter(s) will change.';
         statusEl.style.color = 'var(--primary-color)';
         return { hasChanges: true, diff: changedRows };
      } catch (err) {
         console.error('Serial config preview error:', err);
         statusEl.textContent = 'Preview error: ' + err.message;
         statusEl.style.color = 'var(--danger-color)';
         throw err;
      }
   }

   async function saveSerialConfig() {
      var statusEl = document.getElementById('serialStatus');
      var preview;
      try {
         preview = await previewSerialConfig();
      } catch (_err) {
         return;
      }
      if (!preview.hasChanges) return;

      var lines = preview.diff.map(function(item) {
         return item.param + ': ' + item.old + ' -> ' + item.new;
      });
      var confirmText = 'Apply these changes?\n\n' + lines.join('\n');
      if (!window.confirm(confirmText)) {
         statusEl.textContent = 'Apply cancelled.';
         statusEl.style.color = 'var(--text-muted)';
         return;
      }

      var diff = buildSerialDiff();
      statusEl.textContent = 'Applying and verifying ' + Object.keys(diff).length + ' parameter(s)...';
      statusEl.style.color = 'var(--text-muted)';
      try {
         var response = await fetch('/api/config/domains/serial_ports/apply', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ changes: diff, verify_timeout_ms: 5000, tolerance: 0.0001 })
         });
         var result = await response.json();
         if (response.status === 200 && result.status === 'success') {
            statusEl.textContent = 'Saved and verified (' + result.verified + ' param). Reboot FC if required.';
            statusEl.style.color = 'var(--success-color)';
            originalData = JSON.parse(JSON.stringify(serialData));
         } else if (response.status === 207 || result.status === 'partial') {
            var failedCount = (result.failed || []).length;
            var mismatchCount = (result.mismatched || []).length;
            statusEl.textContent = 'Partial apply: verified=' + result.verified + ', failed=' + failedCount + ', mismatched=' + mismatchCount + '.';
            statusEl.style.color = 'var(--warning-color)';
         } else {
            throw new Error(result.message || 'Unknown error');
         }
      } catch (err) {
         console.error('Serial config save error:', err);
         statusEl.textContent = 'Error saving: ' + err.message;
         statusEl.style.color = 'var(--danger-color)';
      }
   }

   // Wire up buttons
   var saveBtn = document.getElementById('serialSaveBtn');
   var previewBtn = document.getElementById('serialPreviewBtn');
   var refreshBtn = document.getElementById('serialRefreshBtn');
   if (saveBtn) saveBtn.addEventListener('click', saveSerialConfig);
   if (previewBtn) previewBtn.addEventListener('click', function() {
      previewSerialConfig().catch(function() { /* error already shown in status */ });
   });
   if (refreshBtn) refreshBtn.addEventListener('click', loadSerialConfig);

   // Load on first tab visit via MutationObserver
   var tabEl = document.getElementById('serial-ports-tab');
   if (tabEl) {
      var observer = new MutationObserver(function() {
         if (tabEl.style.display !== 'none' && !loaded) {
            loadSerialConfig();
         }
      });
      observer.observe(tabEl, { attributes: true, attributeFilter: ['style', 'class'] });
   }
});
