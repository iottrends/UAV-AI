// ===== firmware.js — Firmware flash tab logic =====

(function() {
   'use strict';

   var socket = window._app ? window._app.socket : null;
   var manifestData = null;  // cached manifest grouped by vehicle type

   // ───── DOM element cache ─────
   var els = {};
   function el(id) {
      if (!els[id]) els[id] = document.getElementById(id);
      return els[id];
   }

   // ───── Current Firmware Info ─────
   function refreshCurrentFirmware() {
      fetch('/api/firmware')
         .then(function(r) { return r.json(); })
         .then(function(data) {
            var container = el('fwCurrentInfo');
            if (!container) return;
            if (data.status === 'success' && data.firmware) {
               var fw = data.firmware;
               container.innerHTML =
                  '<table style="width:100%; border-collapse:collapse;">' +
                  '<tr><td style="padding:0.25rem 0.5rem; font-weight:600;">Version</td><td>' + (fw.firmware_version || 'N/A') + '</td></tr>' +
                  '<tr><td style="padding:0.25rem 0.5rem; font-weight:600;">Board Version</td><td>' + (fw.board_version || 'N/A') + '</td></tr>' +
                  '<tr><td style="padding:0.25rem 0.5rem; font-weight:600;">Git Hash</td><td style="font-family:monospace;">' + (fw.flight_custom_version || 'N/A') + '</td></tr>' +
                  '<tr><td style="padding:0.25rem 0.5rem; font-weight:600;">Vendor / Product ID</td><td>' + (fw.vendor_id || '?') + ' / ' + (fw.product_id || '?') + '</td></tr>' +
                  '<tr><td style="padding:0.25rem 0.5rem; font-weight:600;">Capabilities</td><td>' + (fw.capabilities ? fw.capabilities.join(', ') : 'N/A') + '</td></tr>' +
                  '</table>';
            } else {
               container.textContent = 'Not connected \u2014 connect a flight controller to see firmware info.';
            }
         })
         .catch(function() {
            var container = el('fwCurrentInfo');
            if (container) container.textContent = 'Failed to fetch firmware info.';
         });
   }

   // ───── Online Firmware Manifest ─────
   function refreshManifest() {
      var btn = el('fwRefreshManifest');
      if (btn) {
         btn.disabled = true;
         btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Loading...';
      }

      fetch('/api/firmware/manifest')
         .then(function(r) { return r.json(); })
         .then(function(data) {
            if (btn) {
               btn.disabled = false;
               btn.innerHTML = '<i class="fas fa-sync-alt"></i> Refresh';
            }
            if (data.status === 'success') {
               manifestData = data.firmware;
               renderManifest();
            } else {
               el('fwOnlineList').innerHTML = '<p style="color:var(--danger-color);">' + (data.message || 'Failed') + '</p>';
            }
         })
         .catch(function(err) {
            if (btn) {
               btn.disabled = false;
               btn.innerHTML = '<i class="fas fa-sync-alt"></i> Refresh';
            }
            el('fwOnlineList').innerHTML = '<p style="color:var(--danger-color);">Error: ' + err.message + '</p>';
         });
   }

   function renderManifest() {
      var container = el('fwOnlineList');
      if (!container || !manifestData) return;

      var filter = el('fwVehicleFilter') ? el('fwVehicleFilter').value : '';
      var html = '';
      var vehicles = Object.keys(manifestData).sort();

      if (vehicles.length === 0) {
         container.innerHTML = '<p style="color:var(--text-muted);">No firmware found for this board.</p>';
         return;
      }

      vehicles.forEach(function(vehicle) {
         if (filter && vehicle !== filter) return;
         var items = manifestData[vehicle];
         if (!items || items.length === 0) return;

         html += '<div style="margin-bottom:0.75rem;">';
         html += '<div style="font-weight:600; margin-bottom:0.25rem; color:var(--primary-color);">' + vehicle + ' (' + items.length + ')</div>';
         html += '<table style="width:100%; border-collapse:collapse; font-size:0.8rem;">';
         html += '<tr style="background:var(--bg-color);"><th style="padding:0.3rem 0.5rem; text-align:left;">Version</th><th style="padding:0.3rem 0.5rem; text-align:left;">Platform</th><th style="padding:0.3rem 0.5rem; text-align:left;">Board ID</th><th style="padding:0.3rem 0.5rem;"></th></tr>';

         // Show max 20 per vehicle
         var shown = items.slice(0, 20);
         shown.forEach(function(fw) {
            html += '<tr style="border-bottom:1px solid var(--border-color);">';
            html += '<td style="padding:0.3rem 0.5rem;">' + (fw.version || 'unknown') + '</td>';
            html += '<td style="padding:0.3rem 0.5rem;">' + (fw.platform || '') + '</td>';
            html += '<td style="padding:0.3rem 0.5rem;">' + (fw.board_id || '') + '</td>';
            html += '<td style="padding:0.3rem 0.5rem; text-align:right; white-space:nowrap;">';
            html += '<button class="fw-download-btn" data-url="' + fw.url + '" style="background:var(--primary-color); color:white; border:none; border-radius:4px; padding:0.2rem 0.5rem; cursor:pointer; font-size:0.75rem; margin-right:0.25rem;"><i class="fas fa-plug"></i> Serial</button>';
            html += '<button class="fw-dfu-btn" data-url="' + fw.url + '" style="background:var(--secondary-color); color:white; border:none; border-radius:4px; padding:0.2rem 0.5rem; cursor:pointer; font-size:0.75rem;"><i class="fas fa-microchip"></i> DFU</button>';
            html += '</td></tr>';
         });

         if (items.length > 20) {
            html += '<tr><td colspan="4" style="padding:0.3rem 0.5rem; color:var(--text-muted);">... and ' + (items.length - 20) + ' more</td></tr>';
         }

         html += '</table></div>';
      });

      if (!html) {
         html = '<p style="color:var(--text-muted);">No firmware matches the selected filter.</p>';
      }

      container.innerHTML = html;

      // Serial flash buttons
      container.querySelectorAll('.fw-download-btn').forEach(function(btn) {
         btn.addEventListener('click', function() {
            downloadAndFlash(this.getAttribute('data-url'));
         });
      });
      // DFU flash buttons
      container.querySelectorAll('.fw-dfu-btn').forEach(function(btn) {
         btn.addEventListener('click', function() {
            if (window._fwTab && window._fwTab.dfuDownloadAndFlash) {
               window._fwTab.dfuDownloadAndFlash(this.getAttribute('data-url'));
            }
         });
      });
   }

   // ───── Download & Flash (Online) ─────
   function downloadAndFlash(url) {
      if (!confirm('This will download firmware and flash it to the flight controller.\nThe drone will be disconnected during flashing.\n\nProceed?')) {
         return;
      }

      showProgress();
      appendLog('Downloading firmware from server...');

      fetch('/api/firmware/download', {
         method: 'POST',
         headers: {'Content-Type': 'application/json'},
         body: JSON.stringify({url: url})
      })
      .then(function(r) { return r.json(); })
      .then(function(data) {
         if (data.status === 'success') {
            appendLog('Download complete: ' + data.filename + ' (' + data.size + ' bytes)');
            startFlash(data.path, false);
         } else {
            appendLog('ERROR: ' + (data.message || 'Download failed'));
         }
      })
      .catch(function(err) {
         appendLog('ERROR: ' + err.message);
      });
   }

   // ───── Flash Local File ─────
   function flashLocalFile() {
      var fileInput = el('fwLocalFile');
      if (!fileInput || !fileInput.files || !fileInput.files[0]) return;

      if (!confirm('This will flash the selected firmware to the flight controller.\nThe drone will be disconnected during flashing.\n\nProceed?')) {
         return;
      }

      showProgress();
      appendLog('Uploading firmware file...');

      var formData = new FormData();
      formData.append('file', fileInput.files[0]);

      fetch('/api/firmware/flash', {
         method: 'POST',
         body: formData
      })
      .then(function(r) { return r.json(); })
      .then(function(data) {
         if (data.status === 'success') {
            appendLog('Flash started...');
         } else {
            appendLog('ERROR: ' + (data.message || 'Flash failed to start'));
         }
      })
      .catch(function(err) {
         appendLog('ERROR: ' + err.message);
      });
   }

   function startFlash(path, force) {
      appendLog('Starting flash...');

      fetch('/api/firmware/flash', {
         method: 'POST',
         headers: {'Content-Type': 'application/json'},
         body: JSON.stringify({path: path, force: force || false})
      })
      .then(function(r) { return r.json(); })
      .then(function(data) {
         if (data.status === 'success') {
            appendLog('Flash started...');
         } else {
            appendLog('ERROR: ' + (data.message || 'Flash failed to start'));
         }
      })
      .catch(function(err) {
         appendLog('ERROR: ' + err.message);
      });
   }

   // ───── Progress Display ─────
   function showProgress() {
      var card = el('fwProgressCard');
      if (card) card.style.display = '';
      var bar = el('fwProgressBar');
      if (bar) bar.style.width = '0%';
      var log = el('fwProgressLog');
      if (log) log.textContent = '';
      var stage = el('fwProgressStage');
      if (stage) stage.textContent = 'Starting...';
   }

   function appendLog(msg) {
      var log = el('fwProgressLog');
      if (!log) return;
      log.textContent += msg + '\n';
      log.scrollTop = log.scrollHeight;
   }

   function updateProgress(data) {
      var bar = el('fwProgressBar');
      var stage = el('fwProgressStage');
      var card = el('fwProgressCard');

      if (card) card.style.display = '';
      if (bar) bar.style.width = (data.percent || 0) + '%';

      var stageLabels = {
         'init': 'Initializing',
         'reboot': 'Rebooting to Bootloader',
         'parse': 'Parsing Firmware',
         'connect': 'Connecting to Bootloader',
         'info': 'Reading Device Info',
         'erase': 'Erasing Flash',
         'program': 'Programming',
         'verify': 'Verifying CRC',
         'reboot': 'Rebooting',
      };

      if (stage) {
         var label = stageLabels[data.stage] || data.stage || '';
         stage.textContent = label + (data.percent ? ' (' + data.percent + '%)' : '');
      }

      if (data.message) appendLog(data.message);
   }

   // ───── SocketIO Listeners ─────
   function setupSocketListeners() {
      if (!socket) return;

      socket.on('firmware_flash_progress', function(data) {
         updateProgress(data);
      });

      socket.on('firmware_flash_complete', function(data) {
         var bar = el('fwProgressBar');
         var stage = el('fwProgressStage');

         if (data.success) {
            if (bar) bar.style.background = 'var(--success-color)';
            if (stage) stage.textContent = 'Flash Complete!';
            appendLog('\n=== FLASH SUCCESSFUL ===');
            appendLog(data.message || 'Firmware flashed and verified.');
            appendLog('Please reconnect to the flight controller.');
         } else {
            if (bar) bar.style.background = 'var(--danger-color)';
            if (stage) stage.textContent = 'Flash Failed';
            appendLog('\n=== FLASH FAILED ===');
            appendLog(data.message || 'Unknown error');
         }
      });

      socket.on('firmware_download_progress', function(data) {
         if (data.total > 0) {
            appendLog('Downloading: ' + data.percent + '% (' + data.downloaded + '/' + data.total + ' bytes)');
         }
      });
   }

   // ───── Flash Method Toggle (Serial / DFU) ─────
   var currentMethod = 'serial';   // 'serial' | 'dfu'

   function setMethod(method) {
      currentMethod = method;
      var serialPanel = el('fwSerialPanel');
      var dfuPanel    = el('fwDfuPanel');
      var btnSerial   = el('fwMethodSerial');
      var btnDfu      = el('fwMethodDfu');

      if (serialPanel) serialPanel.style.display = method === 'serial' ? '' : 'none';
      if (dfuPanel)    dfuPanel.style.display    = method === 'dfu'    ? '' : 'none';

      if (btnSerial) btnSerial.classList.toggle('fw-method-active', method === 'serial');
      if (btnDfu)    btnDfu.classList.toggle('fw-method-active',    method === 'dfu');
   }

   // Expose for inline onclick handlers in HTML
   window._fwTab = window._fwTab || {};
   window._fwTab.setMethod = setMethod;

   // ───── DFU: Enter DFU Mode ─────
   var _dfuDetectTimer = null;

   function dfuSetChip(text, ok) {
      var chip = el('dfuStatusChip');
      if (!chip) return;
      chip.textContent = text;
      chip.style.background = ok === true  ? 'var(--success-color)' :
                              ok === false ? 'var(--danger-color)'  : '#555';
      chip.style.color = '#fff';
   }

   function dfuShowBoardCard(info) {
      var card = el('dfuBoardCard');
      if (!card) return;
      el('dfuBoardMfr').textContent   = info.manufacturer  || '--';
      el('dfuBoardModel').textContent = info.model && info.model !== '' ? info.model : info.usb_product || '--';
      el('dfuBoardUsb').textContent   = (info.vid_str || '') + ' : ' + (info.pid_str || '');
      el('dfuBoardDesc').textContent  = [info.usb_manufacturer, info.usb_product].filter(Boolean).join(' / ') || '--';
      card.style.display = '';
   }

   function dfuDetectBoard() {
      dfuSetChip('Scanning...', null);
      fetch('/api/firmware/dfu/detect')
         .then(function(r) { return r.json(); })
         .then(function(data) {
            if (data.status === 'found') {
               dfuSetChip('DFU DETECTED', true);
               dfuShowBoardCard(data.device);
               stopDfuPoll();
            } else {
               dfuSetChip('Not detected', false);
               var card = el('dfuBoardCard');
               if (card) card.style.display = 'none';
            }
         })
         .catch(function() {
            dfuSetChip('Scan error', false);
         });
   }

   function startDfuPoll() {
      dfuDetectBoard();
      if (!_dfuDetectTimer) {
         _dfuDetectTimer = setInterval(dfuDetectBoard, 2000);
      }
   }

   function stopDfuPoll() {
      if (_dfuDetectTimer) {
         clearInterval(_dfuDetectTimer);
         _dfuDetectTimer = null;
      }
   }

   function dfuEnter() {
      var btn = el('dfuEnterBtn');
      if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Triggering...'; }
      dfuSetChip('Entering DFU...', null);

      fetch('/api/firmware/dfu/enter', { method: 'POST' })
         .then(function(r) { return r.json(); })
         .then(function(data) {
            if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-sign-in-alt"></i> Enter DFU Mode'; }
            if (data.status === 'success') {
               appendLog('DFU trigger: ' + data.message + ' (method: ' + data.method + ')');
               dfuSetChip('Waiting...', null);
               startDfuPoll();  // poll every 2s until device appears
            } else if (data.status === 'manual') {
               appendLog('⚠ ' + data.message);
               dfuSetChip('Manual required', false);
               startDfuPoll();  // still poll in case user presses BOOT button
            } else {
               appendLog('ERROR: ' + (data.message || 'DFU entry failed'));
               dfuSetChip('Failed', false);
            }
         })
         .catch(function(err) {
            if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fas fa-sign-in-alt"></i> Enter DFU Mode'; }
            appendLog('ERROR: ' + err.message);
            dfuSetChip('Error', false);
         });
   }

   // ───── DFU: Flash Local .bin ─────
   function dfuFlashLocalFile() {
      var fileInput = el('dfuLocalFile');
      if (!fileInput || !fileInput.files || !fileInput.files.length) return;

      if (!confirm('Flash via DFU?\n\nBoard: ' + (el('dfuBoardMfr') ? el('dfuBoardMfr').textContent : '?') +
                   ' ' + (el('dfuBoardModel') ? el('dfuBoardModel').textContent : '') +
                   '\n\nThis will erase and reflash the flight controller.\n\nProceed?')) return;

      showProgress();
      appendLog('Uploading .bin file for DFU flash...');

      var formData = new FormData();
      formData.append('file', fileInput.files[0]);

      fetch('/api/firmware/dfu/flash', { method: 'POST', body: formData })
         .then(function(r) { return r.json(); })
         .then(function(data) {
            if (data.status === 'success') {
               appendLog('DFU flash started...');
            } else {
               appendLog('ERROR: ' + (data.message || 'DFU flash failed to start'));
            }
         })
         .catch(function(err) { appendLog('ERROR: ' + err.message); });
   }

   // ───── DFU: Download .bin from server and flash ─────
   function dfuDownloadAndFlash(apjUrl) {
      if (!confirm('Download firmware and flash via DFU?\n\nThe board must already be in DFU mode.\n\nProceed?')) return;

      showProgress();
      appendLog('Downloading .bin from server...');

      fetch('/api/firmware/dfu/download', {
         method: 'POST',
         headers: { 'Content-Type': 'application/json' },
         body: JSON.stringify({ url: apjUrl }),
      })
         .then(function(r) { return r.json(); })
         .then(function(data) {
            if (data.status === 'success') {
               appendLog('Download complete: ' + data.filename + ' (' + data.size + ' bytes)');
               // Start flash
               fetch('/api/firmware/dfu/flash', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ path: data.path }),
               })
                  .then(function(r) { return r.json(); })
                  .then(function(d) {
                     if (d.status === 'success') appendLog('DFU flash started...');
                     else appendLog('ERROR: ' + (d.message || 'DFU flash failed'));
                  });
            } else {
               appendLog('ERROR: ' + (data.message || 'Download failed'));
            }
         })
         .catch(function(err) { appendLog('ERROR: ' + err.message); });
   }

   // ───── Update manifest render to add DFU Flash button ─────
   var _origRenderManifest = renderManifest;
   // Extend the flash button in renderManifest to show a DFU option
   // (done by patching the click handler below in init)

   // ───── Extended stage labels for DFU ─────
   var DFU_STAGE_LABELS = {
      'dfu_detect': 'Detecting DFU Device',
      'dfu_init':   'Initialising DFU',
      'erase':      'Erasing Flash',
      'program':    'Programming',
      'verify':     'Verifying',
      'reboot':     'Rebooting',
   };

   var _origUpdateProgress = updateProgress;
   function updateProgress(data) {
      // Merge DFU stage labels into existing map
      var bar   = el('fwProgressBar');
      var stage = el('fwProgressStage');
      var card  = el('fwProgressCard');

      if (card) card.style.display = '';
      if (bar && data.percent >= 0) bar.style.width = (data.percent || 0) + '%';

      var allLabels = {
         'init':       'Initializing',
         'reboot':     'Rebooting to Bootloader',
         'parse':      'Parsing Firmware',
         'connect':    'Connecting to Bootloader',
         'info':       'Reading Device Info',
         'erase':      'Erasing Flash',
         'program':    'Programming',
         'verify':     'Verifying CRC',
         'dfu_detect': 'Detecting DFU Device',
         'dfu_init':   'Initialising DFU Interface',
      };

      if (stage) {
         var lbl = allLabels[data.stage] || data.stage || '';
         stage.textContent = lbl + (data.percent >= 0 ? ' (' + data.percent + '%)' : '');
      }
      if (data.message) appendLog(data.message);
   }

   // ───── Init ─────
   function init() {
      // Resolve socket (may not exist at IIFE parse time)
      if (!socket && window._app) socket = window._app.socket;
      if (!socket && window._app && window._app.socket) socket = window._app.socket;

      // Serial: file input → enable flash button
      var fileInput = el('fwLocalFile');
      var flashBtn  = el('fwFlashLocalBtn');
      if (fileInput && flashBtn) {
         fileInput.addEventListener('change', function() {
            flashBtn.disabled = !fileInput.files || !fileInput.files.length;
         });
         flashBtn.addEventListener('click', flashLocalFile);
      }

      // DFU: file input → enable DFU flash button
      var dfuFileInput = el('dfuLocalFile');
      var dfuFlashBtn  = el('dfuFlashLocalBtn');
      if (dfuFileInput && dfuFlashBtn) {
         dfuFileInput.addEventListener('change', function() {
            dfuFlashBtn.disabled = !dfuFileInput.files || !dfuFileInput.files.length;
         });
         dfuFlashBtn.addEventListener('click', dfuFlashLocalFile);
      }

      // DFU: Enter DFU button
      var enterBtn = el('dfuEnterBtn');
      if (enterBtn) enterBtn.addEventListener('click', dfuEnter);

      // DFU: Detect board button
      var detectBtn = el('dfuDetectBtn');
      if (detectBtn) detectBtn.addEventListener('click', dfuDetectBoard);

      // Refresh manifest button
      var refreshBtn = el('fwRefreshManifest');
      if (refreshBtn) refreshBtn.addEventListener('click', refreshManifest);

      // Vehicle type filter
      var vehicleFilter = el('fwVehicleFilter');
      if (vehicleFilter) vehicleFilter.addEventListener('change', renderManifest);

      // Socket listeners (re-resolve socket first)
      setupSocketListeners();

      // Fix socket reference if resolved late
      setTimeout(function() {
         if (!socket && window._app) {
            socket = window._app.socket;
            setupSocketListeners();
         }
      }, 500);

      // Refresh firmware info when tab becomes visible
      document.querySelectorAll('.menu-item').forEach(function(item) {
         item.addEventListener('click', function() {
            if (this.getAttribute('data-tab') === 'firmware') {
               refreshCurrentFirmware();
               // Stop DFU poll when leaving the tab would be handled by setMethod
            }
         });
      });
   }

   // Run init when DOM is ready
   if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', init);
   } else {
      init();
   }

   // Expose dfuDownloadAndFlash for manifest button use
   window._fwTab.dfuDownloadAndFlash = dfuDownloadAndFlash;

})();
