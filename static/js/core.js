// ===== core.js — Socket, connection, dashboard, shared utils =====

function toggleConnFields() {
   const isSerial = document.querySelector('input[name="connType"][value="serial"]').checked;
   document.getElementById('serialFields').style.display = isSerial ? '' : 'none';
   document.getElementById('ipFields').style.display = isSerial ? 'none' : '';
}

// Variables for browser-to-server latency measurement (WebSocket ping/pong)
let pingInterval = null;
let lastPingTime = 0;

// Shared application state
window._app = {
   socket: null,
   isConnected: false,
   logLoaded: false,
   copilotActive: false,
   systemStatusHooks: [],

   addMessage: function(msg, isUser) {
      const chatMessages = document.getElementById('chatMessages');
      if (!chatMessages) return;

      const messageDiv = document.createElement('div');
      messageDiv.className = 'message ' + (isUser ? 'user-message' : 'ai-message');

      const messageBubble = document.createElement('div');
      messageBubble.className = 'message-bubble';
      messageBubble.innerHTML = msg.text;

      const messageTime = document.createElement('div');
      messageTime.className = 'message-time';
      messageTime.textContent = msg.time;

      messageDiv.appendChild(messageBubble);
      messageDiv.appendChild(messageTime);

      chatMessages.appendChild(messageDiv);
      chatMessages.scrollTop = chatMessages.scrollHeight;
   },

   getCurrentTime: function() {
      const now = new Date();
      let hours = now.getHours();
      let minutes = now.getMinutes();
      const ampm = hours >= 12 ? 'PM' : 'AM';

      hours = hours % 12;
      hours = hours ? hours : 12;
      minutes = minutes < 10 ? '0' + minutes : minutes;

      return hours + ':' + minutes + ' ' + ampm;
   },

   formatResponse: function(obj) {
      let formattedText = '';

      for (const key in obj) {
         if (Object.prototype.hasOwnProperty.call(obj, key)) {
            const value = obj[key];

            if (value === null || value === undefined || key === 'message' || key === 'intent') {
               continue;
            }

            formattedText += '<strong>' + key + ':</strong> ';

            if (Array.isArray(value)) {
               formattedText += '<br>';
               value.forEach(function(item) {
                  if (typeof item === 'object' && item !== null) {
                     formattedText += '<div style="margin-left: 15px; margin-bottom:5px;">';
                     for (const itemKey in item) {
                        if (Object.prototype.hasOwnProperty.call(item, itemKey)) {
                           formattedText += '<strong>' + itemKey + ':</strong> ' + item[itemKey] + '<br>';
                        }
                     }
                     formattedText += '</div>';
                  } else {
                     formattedText += '- ' + item + '<br>';
                  }
               });
            } else if (typeof value === 'object') {
               formattedText += '<br><div style="margin-left: 15px;">';
               for (const nestedKey in value) {
                  if (Object.prototype.hasOwnProperty.call(value, nestedKey)) {
                     formattedText += '<strong>' + nestedKey + ':</strong> ' + value[nestedKey] + '<br>';
                  }
               }
               formattedText += '</div>';
            } else {
               formattedText += value + '<br>';
            }
         }
      }

      return formattedText;
   },

   updateConnectionStatus: function(connected) {
      window._app.isConnected = connected;

      const connectionIndicator = document.getElementById('connectionIndicator');
      const connectionText = document.getElementById('connectionText');

      if (connectionIndicator) {
         connectionIndicator.style.backgroundColor = connected ? 'var(--success-color)' : 'var(--danger-color)';
      }

      if (connectionText) {
         connectionText.textContent = connected ? 'Connected' : 'Disconnected';
      }

      // Toggle visibility of connect and disconnect buttons
      const connectButton = document.getElementById('connectButton');
      const disconnectButton = document.getElementById('disconnectButton');

      if (connectButton) {
         connectButton.style.display = connected ? 'none' : 'flex';
      }

      if (disconnectButton) {
         disconnectButton.style.display = connected ? 'flex' : 'none';
      }

      // Show/hide header params and latency indicators
      const headerParamsText = document.getElementById('headerParamsText');
      const headerLatencyText = document.getElementById('headerLatencyText');

      if (headerParamsText) {
         headerParamsText.style.display = connected ? 'inline' : 'none';
      }

      if (headerLatencyText) {
         headerLatencyText.style.display = connected ? 'inline' : 'none';
      }

      // Start/stop browser-to-server latency measurement
      if (connected) {
         startLatencyMeasurement();
      } else if (pingInterval) {
         clearInterval(pingInterval);
         pingInterval = null;
      }
   }
};

// Function to measure browser-to-server latency via WebSocket
function startLatencyMeasurement() {
   if (pingInterval) {
      clearInterval(pingInterval);
   }

   pingInterval = setInterval(function() {
      if (window._app.socket && window._app.isConnected) {
         lastPingTime = Date.now();
         window._app.socket.emit('ping');
      }
   }, 2000);
}

document.addEventListener('DOMContentLoaded', function() {
   // First-run tour card
   const firstRunCard = document.getElementById('firstRunCard');
   const hasSeenTour = window.localStorage.getItem('uav_ai_first_run_seen');

   if (firstRunCard && !hasSeenTour) {
      firstRunCard.style.display = 'block';
      const dismissBtn = document.getElementById('dismissFirstRun');
      if (dismissBtn) {
         dismissBtn.addEventListener('click', function() {
            firstRunCard.style.display = 'none';
            window.localStorage.setItem('uav_ai_first_run_seen', '1');
         });
      }
   }

   // Dashboard elements
   const healthScoreText = document.getElementById('healthScoreText');
   const healthScoreArc = document.getElementById('healthScoreArc');
   const healthIssues = document.getElementById('healthIssues');
   const readinessIndicator = document.getElementById('readinessIndicator');
   const readinessMessage = document.getElementById('readinessMessage');
   const batteryIndicator = document.getElementById('batteryIndicator');
   const batteryText = document.getElementById('batteryText');
   const batteryStatus = document.getElementById('batteryStatus');
   const gpsIndicator = document.getElementById('gpsIndicator');
   const gpsText = document.getElementById('gpsText');
   const gpsSatellites = document.getElementById('gpsSatellites');
   const motor1Output = document.getElementById('motor1Output');
   const motor1Status = document.getElementById('motor1Status');
   const motor2Output = document.getElementById('motor2Output');
   const motor2Status = document.getElementById('motor2Status');
   const motor3Output = document.getElementById('motor3Output');
   const motor3Status = document.getElementById('motor3Status');
   const motor4Output = document.getElementById('motor4Output');
   const motor4Status = document.getElementById('motor4Status');

   const subsystemTable = document.getElementById('subsystemTable');
   const subsystemTableBody = subsystemTable ? subsystemTable.querySelector('tbody') : null;

   // Initialize WebSocket connection
   initWebSocket();
   function initWebSocket() {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const host = window.location.host;
      const socketUrl = protocol + '//' + host;

      console.log('Connecting to WebSocket at ' + socketUrl);
      const socket = io(socketUrl);
      window._app.socket = socket;

      socket.on('connect', function() {
         console.log('WebSocket connected');
      });

      // Handle pong response for browser-to-server latency measurement
      socket.on('pong', function() {
         const latency = Date.now() - lastPingTime;
         const headerLatencyValue = document.getElementById('headerLatencyValue');
         if (headerLatencyValue) {
            headerLatencyValue.textContent = latency + 'ms';
         }

         if (socket) {
            socket.emit('update_latency', { latency: latency });
         }
      });

      socket.on('disconnect', function() {
         console.log('WebSocket disconnected');
         window._app.updateConnectionStatus(false);
      });

      // Listen for system status updates
      socket.on('system_status', function(data) {
         updateDashboard(data);

         // Call all registered hooks
         window._app.systemStatusHooks.forEach(function(fn) { fn(data); });

         // Update parameter progress in header
         const headerParamsValue = document.getElementById('headerParamsValue');
         if (data.params && headerParamsValue) {
            headerParamsValue.textContent = data.params.percentage.toFixed(1) + '%';
            console.log("Received parameter_progress (system_status) data:", data.params);
         }

         // Update co-pilot badge
         if (data.copilot_active !== undefined) {
            var wasActive = window._app.copilotActive;
            window._app.copilotActive = data.copilot_active;
            updateCopilotBadge(data.copilot_active);
            if (!wasActive && data.copilot_active) {
               window._app.addMessage({
                  text: '<strong>System:</strong> Co-pilot mode auto-activated (drone armed). Fast commands enabled.',
                  time: window._app.getCurrentTime()
               });
            }
         }
      });

      // Fast attitude updates (20 Hz) — routes through the same hooks so
      // drone-view and rc-modes update at full rate without waiting for the
      // heavyweight system_status (2 Hz) payload.
      socket.on('attitude', function(data) {
         window._app.systemStatusHooks.forEach(function(fn) { fn(data); });
      });

      // Listen for parameter progress updates
      socket.on('param_progress', function(data) {
         const headerParamsValue = document.getElementById('headerParamsValue');
         if (data.params && headerParamsValue) {
            headerParamsValue.textContent = data.params.percentage.toFixed(1) + '%';
            console.log("Abhinav:Received parameter_progress (param_progress) data:", data.params);
         }

         // Call latency hook for param progress
         window._app.systemStatusHooks.forEach(function(fn) { fn(data); });
      });
   }

   // Connect to the drone
   async function connectToDrone() {
      const connType = document.querySelector('input[name="connType"]:checked').value;
      let body, connLabel;

      if (connType === 'ip') {
         const ip = document.getElementById('ipAddressInput').value.trim() || '0.0.0.0';
         const udpPort = document.getElementById('udpPortInput').value.trim() || '14550';
         body = { type: 'udp', ip: ip, port: udpPort };
         connLabel = 'UDP ' + ip + ':' + udpPort;
      } else {
         const port = document.getElementById('portInput').value.trim() || 'COM3';
         const baud = document.getElementById('baudInput').value.trim() || '115200';
         body = { type: 'serial', port: port, baud: baud };
         connLabel = port + ' at ' + baud + ' baud';
      }

      try {
         console.log('Attempting to connect to drone via ' + connLabel);

         const response = await fetch('/api/connect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
         });

         const data = await response.json();

         if (data.status === 'success') {
            window._app.updateConnectionStatus(true);
            window._app.addMessage({
               text: '<strong>System:</strong> Successfully connected to drone via ' + connLabel + '. Initializing drone parameters...',
               time: window._app.getCurrentTime()
            });
         } else {
            window._app.updateConnectionStatus(false);
            window._app.addMessage({
               text: '<strong>Error:</strong> Failed to connect to drone: ' + data.message,
               time: window._app.getCurrentTime()
            });
         }
      } catch(error) {
         console.error('Connection error:', error);
         window._app.updateConnectionStatus(false);
         window._app.addMessage({
            text: '<strong>Error:</strong> Connection failed. Server may be offline or unreachable.',
            time: window._app.getCurrentTime()
         });
      }
   }

   // Disconnect from the drone
   async function disconnectFromDrone() {
      try {
         console.log('Attempting to disconnect from drone');

         const response = await fetch('/api/disconnect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
         });

         const data = await response.json();

         if (data.status === 'success') {
            window._app.updateConnectionStatus(false);
            window._app.addMessage({
               text: '<strong>System:</strong> Successfully disconnected from drone.',
               time: window._app.getCurrentTime()
            });

            if (window._app.socket) {
               window._app.socket.disconnect();
               window._app.socket = null;
            }
         } else {
            window._app.addMessage({
               text: '<strong>Error:</strong> Failed to disconnect from drone: ' + data.message,
               time: window._app.getCurrentTime()
            });
         }
      } catch(error) {
         console.error('Disconnection error:', error);
         window._app.addMessage({
            text: '<strong>Error:</strong> Disconnection failed. Server may be offline or unreachable.',
            time: window._app.getCurrentTime()
         });
      }
   }

   // Update dashboard with system status
   function updateDashboard(data) {
      // Update header params and latency values
      const headerParamsValue = document.getElementById('headerParamsValue');
      const headerLatencyValue = document.getElementById('headerLatencyValue');

      if (headerParamsValue && data.params && data.params.percentage !== undefined) {
         var clampedPct = Math.min(100, Math.max(0, data.params.percentage));
         headerParamsValue.textContent = clampedPct.toFixed(1) + '%';
      }

      if (headerLatencyValue && data.latency !== undefined) {
         headerLatencyValue.textContent = data.latency + 'ms';
      }

      if (healthScoreText) {
         healthScoreText.textContent = data.score;
      }

      if (healthScoreArc) {
         healthScoreArc.setAttribute('stroke-dasharray', data.score + ', 100');
         healthScoreArc.setAttribute('stroke', data.score < 70 ? 'var(--danger-color)' : 'var(--success-color)');
      }

      if (healthIssues) {
         healthIssues.textContent = data.critical_issues + ' CRITICAL ISSUES';
      }

      if (readinessIndicator) {
         readinessIndicator.textContent = data.readiness;
         readinessIndicator.style.backgroundColor =
            data.readiness === 'READY' ? 'var(--success-color)' :
            data.readiness === 'CAUTION' ? 'var(--warning-color)' : 'var(--danger-color)';
      }

      if (readinessMessage) {
         readinessMessage.textContent = data.critical_issues > 0 ? 'PREFLIGHT CHECK REQUIRED' : 'READY TO FLY';
      }

      if (batteryIndicator) {
         var batPct = Math.min(100, Math.max(0, data.battery.percentage));
         batteryIndicator.style.width = batPct + '%';
         batteryIndicator.style.backgroundColor =
            data.battery.status === 'CRITICAL' ? 'var(--danger-color)' :
            data.battery.status === 'WARNING' ? 'var(--warning-color)' : 'var(--success-color)';
      }

      if (batteryText) {
         batteryText.textContent = data.battery.percentage + '% / ' + data.battery.threshold + ' Threshold';
      }

      if (batteryStatus) {
         batteryStatus.textContent = data.battery.status === 'CRITICAL' ? 'CRITICAL: LAND IMMEDIATELY' : '';
      }

      if (gpsIndicator) {
         // Scale: 0 sats = 0%, 10+ sats = 100%. Min useful = 3 sats (2D fix).
         var sats = data.gps.satellites_visible || 0;
         var gpsPct = Math.min(100, Math.round((sats / 10) * 100));
         // Cap at 20% if no fix at all
         if (data.gps.fix_type < 2) gpsPct = Math.min(gpsPct, 20);
         gpsIndicator.style.width = gpsPct + '%';
         gpsIndicator.style.backgroundColor =
            data.gps.fix_type < 2 ? 'var(--danger-color)' :
            data.gps.fix_type < 3 ? 'var(--warning-color)' : 'var(--success-color)';
      }

      if (gpsText) {
         gpsText.textContent = 'Fix Type: ' + data.gps.fix_type + ' (' + (data.gps.fix_type < 2 ? 'No Fix' : data.gps.fix_type < 3 ? '2D Fix' : '3D Fix') + ')';
      }

      if (gpsSatellites) {
         gpsSatellites.textContent = data.gps.satellites_visible + ' SATELLITES VISIBLE';
      }

      // Update hardware inventory card
      if (data.hardware) {
         var hw = data.hardware;
         var hwImu     = document.getElementById('hwImu');
         var hwBaro    = document.getElementById('hwBaro');
         var hwCompass = document.getElementById('hwCompass');
         var hwGps     = document.getElementById('hwGps');
         var hwEsc     = document.getElementById('hwEsc');
         var hwCpuLoad = document.getElementById('hwCpuLoad');
         var hwCpuBar  = document.getElementById('hwCpuBar');
         var hwFreeRam = document.getElementById('hwFreeRam');
         var hwDropRate= document.getElementById('hwDropRate');
         var hwFlash   = document.getElementById('hwFlash');

         if (hwImu)     hwImu.textContent     = hw.imu     || '--';
         if (hwBaro)    hwBaro.textContent    = hw.baro    || '--';
         if (hwCompass) hwCompass.textContent = hw.compass || '--';
         if (hwGps)     hwGps.textContent     = hw.gps     || '--';
         if (hwEsc)     hwEsc.textContent     = hw.esc_protocol || '--';

         if (hw.cpu_load !== null && hw.cpu_load !== undefined) {
            var cpuPct = hw.cpu_load;
            if (hwCpuLoad) hwCpuLoad.textContent = cpuPct < 1 ? '< 1%' : cpuPct + '%';
            if (hwCpuBar) {
               // Minimum 3% bar width so it's always visible even at 0 load (e.g. SITL)
               hwCpuBar.style.width = Math.max(3, Math.min(100, cpuPct)) + '%';
               hwCpuBar.style.backgroundColor =
                  cpuPct > 80 ? 'var(--danger-color)' :
                  cpuPct > 60 ? 'var(--warning-color)' : 'var(--success-color)';
            }
         }
         if (hw.free_ram_kb !== null && hw.free_ram_kb !== undefined) {
            if (hwFreeRam) hwFreeRam.textContent = hw.free_ram_kb > 0 ? hw.free_ram_kb + ' KB' : 'N/A';
         }
         if (hw.drop_rate !== null && hw.drop_rate !== undefined) {
            if (hwDropRate) {
               hwDropRate.textContent = hw.drop_rate + '%';
               hwDropRate.style.color = hw.drop_rate > 5 ? 'var(--danger-color)' :
                                        hw.drop_rate > 1 ? 'var(--warning-color)' : '';
            }
         }
         if (hw.flash_total_mb !== null && hw.flash_total_mb !== undefined && hwFlash) {
            if (hw.flash_total_mb > 0) {
               var usedMb  = hw.flash_used_mb  || 0;
               var totalMb = hw.flash_total_mb;
               // Show in GB if ≥ 1024 MB, otherwise MB
               var fmt = function(mb) { return mb >= 1024 ? (mb / 1024).toFixed(1) + ' GB' : mb + ' MB'; };
               hwFlash.textContent = fmt(usedMb) + ' / ' + fmt(totalMb);
            } else {
               hwFlash.textContent = 'N/A';
            }
         }
      }

      // Update motors if elements exist
      if (data.motors && Array.isArray(data.motors)) {
         const motor1 = data.motors.find(function(motor) { return motor.id === 1; });
         const motor2 = data.motors.find(function(motor) { return motor.id === 2; });
         const motor3 = data.motors.find(function(motor) { return motor.id === 3; });
         const motor4 = data.motors.find(function(motor) { return motor.id === 4; });

         if (motor1 && motor1Output) motor1Output.textContent = motor1.output + '%';
         if (motor1 && motor1Status) motor1Status.textContent = motor1.status;

         if (motor2 && motor2Output) motor2Output.textContent = motor2.output + '%';
         if (motor2 && motor2Status) motor2Status.textContent = motor2.status;

         if (motor3 && motor3Output) motor3Output.textContent = motor3.output + '%';
         if (motor3 && motor3Status) motor3Status.textContent = motor3.status;

         if (motor4 && motor4Output) motor4Output.textContent = motor4.output + '%';
         if (motor4 && motor4Status) motor4Status.textContent = motor4.status;
      }

      // Update pre-flight readiness panel
      var pfBadge = document.getElementById('preflightBadge');
      var pfChecks = document.getElementById('preflightChecks');
      if (pfBadge && data.overall_readiness) {
         pfBadge.textContent = data.overall_readiness;
         pfBadge.className = 'preflight-badge ' +
            (data.overall_readiness === 'READY' ? 'pf-ready' :
             data.overall_readiness === 'CAUTION' ? 'pf-caution' : 'pf-notready');
      }
      if (pfChecks && Array.isArray(data.preflight)) {
         pfChecks.innerHTML = '';
         data.preflight.forEach(function(chk) {
            var row = document.createElement('div');
            row.className = 'pf-check';
            var dot = document.createElement('div');
            dot.className = 'pf-dot pf-' + chk.status;
            var lbl = document.createElement('span');
            lbl.className = 'pf-label';
            lbl.textContent = chk.name;
            var det = document.createElement('span');
            det.className = 'pf-detail';
            det.textContent = chk.detail || '';
            row.appendChild(dot);
            row.appendChild(lbl);
            row.appendChild(det);
            pfChecks.appendChild(row);
         });
      }

      // Update subsystem table
      if (subsystemTableBody) {
         subsystemTableBody.innerHTML = '';
         data.subsystems.forEach(function(subsystem) {
            const row = document.createElement('tr');

            const componentCell = document.createElement('td');
            componentCell.textContent = subsystem.component;

            const statusCell = document.createElement('td');
            const statusBadge = document.createElement('span');
            statusBadge.classList.add('status-badge');
            statusBadge.classList.add(
               subsystem.status === 'CRITICAL' ? 'status-critical' :
               subsystem.status === 'WARNING' ? 'status-warning' : 'status-ok'
            );
            statusBadge.textContent = subsystem.status;
            statusCell.appendChild(statusBadge);

            const detailsCell = document.createElement('td');
            detailsCell.textContent = subsystem.details;

            row.appendChild(componentCell);
            row.appendChild(statusCell);
            row.appendChild(detailsCell);

            subsystemTableBody.appendChild(row);
         });
      }
   }

   // Initialize event listeners
   const connectButton = document.getElementById('connectButton');
   const connectionModal = document.getElementById('connectionModal');
   const cancelConnect = document.getElementById('cancelConnect');
   const confirmConnect = document.getElementById('confirmConnect');

   if (connectButton) {
      connectButton.addEventListener('click', function() {
         console.log('Connect button clicked');
         if (connectionModal) {
            connectionModal.style.display = 'flex';
         }
      });
   }

   // Add event listener for disconnect button
   const disconnectButton = document.getElementById('disconnectButton');
   if (disconnectButton) {
      disconnectButton.addEventListener('click', function() {
         console.log('Disconnect button clicked');
         disconnectFromDrone();
      });
   }

   if (cancelConnect) {
      cancelConnect.addEventListener('click', function() {
         if (connectionModal) {
            connectionModal.style.display = 'none';
         }
      });
   }

   if (confirmConnect) {
      confirmConnect.addEventListener('click', function() {
         if (connectionModal) {
            connectionModal.style.display = 'none';
         }
         connectToDrone();
      });
   }

   // Tab switching functionality
   document.querySelectorAll('.menu-item').forEach(function(item) {
      item.addEventListener('click', function() {
         document.querySelectorAll('.menu-item').forEach(function(i) { i.classList.remove('active'); });
         document.querySelectorAll('.tab-content').forEach(function(t) { t.style.display = 'none'; });

         this.classList.add('active');

         const tabId = this.getAttribute('data-tab');
         const tabContent = document.getElementById(tabId + '-tab');
         if (tabContent) {
            tabContent.style.display = 'block';

            // Keep chat window visible in all tabs (unless collapsed)
            var chatEl = document.getElementById('chat-container');
            if (chatEl && !chatEl.classList.contains('collapsed')) {
               chatEl.style.display = 'flex';
            }

            // Adjust width of the active tab content to make room for chat
            var activeTab = document.getElementById(tabId + '-tab');
            if (activeTab) {
               var chatCollapsed = chatEl && chatEl.classList.contains('collapsed');
               activeTab.style.width = chatCollapsed ? '100%' : '65%';
            }
         }
      });
   });

   // Add welcome message
   window._app.addMessage({
      text: '<strong>System:</strong> Welcome to UAV-AI Assistant. Please connect to your drone to begin.',
      time: window._app.getCurrentTime()
   });
});

// Co-pilot badge update
function updateCopilotBadge(active) {
   var badge = document.getElementById('copilotBadge');
   if (!badge) return;
   badge.style.display = 'inline-block';
   if (active) {
      badge.classList.add('active');
   } else {
      badge.classList.remove('active');
   }
}

// Co-pilot badge click handler
document.addEventListener('DOMContentLoaded', function() {
   var badge = document.getElementById('copilotBadge');
   if (badge) {
      badge.addEventListener('click', function(e) {
         if (!window._app.socket) return;
         if (e.shiftKey) {
            // Shift+click: reset to auto mode
            window._app.socket.emit('copilot_toggle', { enabled: null });
            window._app.addMessage({
               text: '<strong>System:</strong> Co-pilot mode set to AUTO (follows armed state).',
               time: window._app.getCurrentTime()
            });
         } else {
            var newState = !window._app.copilotActive;
            window._app.socket.emit('copilot_toggle', { enabled: newState });
            window._app.copilotActive = newState;
            updateCopilotBadge(newState);
            window._app.addMessage({
               text: '<strong>System:</strong> Co-pilot mode manually ' + (newState ? 'enabled' : 'disabled') + '.',
               time: window._app.getCurrentTime()
            });
         }
      });
   }
});

// Panel toggle handlers
document.addEventListener('DOMContentLoaded', function() {
   var toggleSidebar = document.getElementById('toggleSidebar');
   var toggleChat = document.getElementById('toggleChat');
   var sidebar = document.querySelector('.sidebar');
   var chatContainer = document.getElementById('chat-container');

   if (toggleSidebar && sidebar) {
      toggleSidebar.addEventListener('click', function() {
         sidebar.classList.toggle('collapsed');
      });
   }

   if (toggleChat && chatContainer) {
      toggleChat.addEventListener('click', function() {
         chatContainer.classList.toggle('collapsed');
         // When chat is hidden, expand tab content to full width
         var activeTabs = document.querySelectorAll('.tab-content');
         activeTabs.forEach(function(tab) {
            if (tab.style.display !== 'none') {
               tab.style.width = chatContainer.classList.contains('collapsed') ? '100%' : '65%';
            }
         });
      });
   }
});

// Function to fetch firmware information
function fetchFirmwareInfo() {
   fetch('/api/firmware')
      .then(function(response) { return response.json(); })
      .then(function(data) {
         var fwEl    = document.getElementById('firmwareVersion');
         var badgeEl = document.getElementById('hwFwBadge');
         if (data.status === 'success') {
            var firmware = data.firmware;
            var ver = firmware.firmware_version || '--';
            if (fwEl) fwEl.textContent = ver;
            // Show a STABLE / DEV / BETA badge based on version string
            if (badgeEl) {
               var vl = ver.toLowerCase();
               if (vl.includes('stable') || (!vl.includes('dev') && !vl.includes('beta') && ver !== '--')) {
                  badgeEl.textContent = 'STABLE';
                  badgeEl.className = 'badge badge-success';
               } else if (vl.includes('dev')) {
                  badgeEl.textContent = 'DEV';
                  badgeEl.className = 'badge badge-warning';
               } else if (vl.includes('beta')) {
                  badgeEl.textContent = 'BETA';
                  badgeEl.className = 'badge badge-info';
               }
               badgeEl.style.display = ver !== '--' ? 'inline-block' : 'none';
            }
         } else {
            if (fwEl) fwEl.textContent = 'Not connected';
            if (badgeEl) badgeEl.style.display = 'none';
         }
      })
      .catch(function(error) {
         console.error('Error fetching firmware info:', error);
         var fwEl = document.getElementById('firmwareVersion');
         if (fwEl) fwEl.textContent = '--';
      });
}

// Fetch firmware info every 5 seconds
setInterval(fetchFirmwareInfo, 5000);

// Call it once on load
fetchFirmwareInfo();
