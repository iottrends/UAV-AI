// ===== motors.js — Motor Testing tab =====

window._motorTab = {
   enabled: false,
   armed: false,
   lastEsc: [{},{},{},{}],
   testTimestamps: {1:0, 2:0, 3:0, 4:0}
};

// ── Rolling motor output chart ────────────────────────────────────────────
(function() {
   var motorChart = null;
   var MAX_POINTS = 120;   // ~30 s at 4 Hz
   var chartLabels = [];
   var M_COLORS = ['#f1453d', '#2b98f0', '#50ae55', '#fdc02f'];
   var datasets = M_COLORS.map(function(c, i) {
      return {
         label: 'M' + (i + 1),
         data: [],
         borderColor: c,
         backgroundColor: c.replace(')', ',0.06)').replace('rgb', 'rgba'),
         tension: 0.3, pointRadius: 0, borderWidth: 1.5
      };
   });

   function initChart() {
      if (motorChart) return;
      var canvas = document.getElementById('motorRollingChart');
      if (!canvas || typeof Chart === 'undefined') return;
      motorChart = new Chart(canvas.getContext('2d'), {
         type: 'line',
         data: { labels: chartLabels, datasets: datasets },
         options: {
            animation: false, responsive: true, maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
               legend: { labels: { color: '#6070a0', font: { family: 'monospace', size: 10 }, boxWidth: 12 } },
               tooltip: { backgroundColor: '#0a0a1e', titleColor: '#8090c0', bodyColor: '#6070a0' }
            },
            scales: {
               x: { display: false },
               y: {
                  min: 900, max: 2100,
                  grid: { color: '#0f0f28' },
                  ticks: { color: '#4a5580', font: { family: 'monospace', size: 10 }, stepSize: 200 }
               }
            }
         }
      });
   }

   function pushChart(servoOutputs) {
      if (!motorChart) { initChart(); if (!motorChart) return; }
      chartLabels.push('');
      for (var i = 0; i < 4; i++) {
         datasets[i].data.push(servoOutputs[i] || 1000);
      }
      if (chartLabels.length > MAX_POINTS) {
         chartLabels.shift();
         for (var j = 0; j < 4; j++) datasets[j].data.shift();
      }
      motorChart.update('none');
   }

   // Hook into tab visibility for lazy init
   document.querySelectorAll('.menu-item[data-tab]').forEach(function(item) {
      item.addEventListener('click', function() {
         if (item.getAttribute('data-tab') === 'motors') {
            setTimeout(initChart, 80);
         }
      });
   });

   // Expose push function for onSystemStatus
   window._motorTab._pushChart = pushChart;
})();

(function() {
   var MT = window._motorTab;
   var enableToggle = document.getElementById('motorEnableToggle');
   var enableLabel = document.getElementById('motorEnableLabel');
   var armedWarning = document.getElementById('motorArmedWarning');

   function setMotorControlsEnabled(enabled) {
      for (var m = 1; m <= 4; m++) {
         document.getElementById('motorSlider' + m).disabled = !enabled;
         document.getElementById('motorDur' + m).disabled = !enabled;
         document.getElementById('motorTestBtn' + m).disabled = !enabled;
         document.getElementById('motorStopBtn' + m).disabled = !enabled;
      }
   }

   window._motorTab.updateSafety = function() {
      var canTest = MT.enabled && !MT.armed;
      setMotorControlsEnabled(canTest);
      armedWarning.style.display = MT.armed ? 'block' : 'none';
      if (MT.armed) {
         enableLabel.textContent = 'ARMED — motor test blocked';
         enableLabel.style.color = 'var(--danger-color)';
      } else if (MT.enabled) {
         enableLabel.textContent = 'Motor Test ENABLED';
         enableLabel.style.color = 'var(--success-color)';
      } else {
         enableLabel.textContent = 'Motor Test DISABLED';
         enableLabel.style.color = 'var(--danger-color)';
      }
   };

   enableToggle.addEventListener('change', function() {
      MT.enabled = this.checked;
      MT.updateSafety();
   });

   // Slider value display
   for (var m = 1; m <= 4; m++) {
      (function(motor) {
         var slider = document.getElementById('motorSlider' + motor);
         var valDisplay = document.getElementById('motorSliderVal' + motor);
         slider.addEventListener('input', function() {
            valDisplay.textContent = this.value + '%';
         });
      })(m);
   }

   // Test and stop button handlers
   for (var m2 = 1; m2 <= 4; m2++) {
      (function(motor) {
         document.getElementById('motorTestBtn' + motor).addEventListener('click', function() {
            var throttle = parseInt(document.getElementById('motorSlider' + motor).value);
            var duration = parseInt(document.getElementById('motorDur' + motor).value) || 3;
            sendMotorTest(motor, throttle, duration);
         });
         document.getElementById('motorStopBtn' + motor).addEventListener('click', function() {
            sendMotorTest(motor, 0, 1);
         });
      })(m2);
   }

   async function sendMotorTest(motor, throttle, duration) {
      var respEl = document.getElementById('motorResp' + motor);
      respEl.textContent = 'Sending...';
      respEl.className = 'motor-response-status';
      try {
         var res = await fetch('/api/motor_test', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({motor: motor, throttle: throttle, duration: duration})
         });
         var data = await res.json();
         if (data.status === 'success') {
            respEl.textContent = 'CMD SENT';
            respEl.className = 'motor-response-status';
            if (throttle > 0) {
               MT.testTimestamps[motor] = Date.now();
               setTimeout(function() {
                  var esc = MT.lastEsc[motor - 1];
                  var diagEl = document.getElementById('motorDiag' + motor);
                  var isActive = esc && (esc.rpm > 0 || (esc.servo_raw !== undefined && esc.servo_raw > 1050));
                  if (isActive) {
                     respEl.textContent = 'RESPONDING';
                     respEl.className = 'motor-response-status responding';
                     if (diagEl) diagEl.setAttribute('fill', 'var(--success-color)');
                  } else {
                     respEl.textContent = 'NO RESPONSE';
                     respEl.className = 'motor-response-status no-response';
                     if (diagEl) diagEl.setAttribute('fill', 'var(--danger-color)');
                  }
               }, 2500);
            }
         } else {
            respEl.textContent = data.message || 'FAILED';
            respEl.className = 'motor-response-status no-response';
         }
      } catch(e) {
         respEl.textContent = 'ERROR';
         respEl.className = 'motor-response-status no-response';
      }
   }

   // Called from updateDashboard via system status hook
   window._motorTab.onSystemStatus = function(data) {
      // Update armed state
      if (data.armed !== undefined) {
         MT.armed = data.armed;
         MT.updateSafety();
      }
      // Update ESC protocol badge
      var protoBadge = document.getElementById('escProtocolBadge');
      if (protoBadge && data.esc_protocol) {
         protoBadge.textContent = data.esc_protocol;
      }
      // Update ESC telemetry table and diagram
      if (data.esc_telemetry && Array.isArray(data.esc_telemetry)) {
         MT.lastEsc = data.esc_telemetry;
         var isFallback = data.esc_telemetry.length > 0 && data.esc_telemetry[0].servo_raw !== undefined;
         for (var i = 0; i < data.esc_telemetry.length; i++) {
            var esc = data.esc_telemetry[i];
            var m = i + 1;
            var rpmEl = document.getElementById('escRpm' + m);
            var tempEl = document.getElementById('escTemp' + m);
            var voltEl = document.getElementById('escVolt' + m);
            var currEl = document.getElementById('escCurr' + m);
            var statEl = document.getElementById('escStatus' + m);
            if (rpmEl) {
               if (isFallback) {
                  rpmEl.textContent = (esc.servo_raw || 1000) + ' \u00B5s';
               } else {
                  rpmEl.textContent = esc.rpm || 0;
               }
            }
            if (tempEl) tempEl.textContent = esc.temperature || (isFallback ? 'N/A' : 0);
            if (voltEl) voltEl.textContent = esc.voltage ? esc.voltage.toFixed(2) : (isFallback ? 'N/A' : '0.00');
            if (currEl) currEl.textContent = esc.current ? esc.current.toFixed(2) : (isFallback ? 'N/A' : '0.00');
            if (statEl) {
               var isActive = isFallback ? (esc.servo_raw > 1050) : (esc.rpm > 0);
               if (isActive) {
                  statEl.innerHTML = '<span class="status-badge status-ok">ACTIVE</span>';
               } else {
                  statEl.innerHTML = '<span class="status-badge" style="background:#555;">IDLE</span>';
               }
            }
            var diagEl = document.getElementById('motorDiag' + m);
            if (diagEl) {
               var isActive2 = isFallback ? (esc.servo_raw > 1050) : (esc.rpm > 0);
               diagEl.setAttribute('fill', isActive2 ? 'var(--success-color)' : '#444');
            }
         }
      }
      // Push servo outputs to rolling chart
      if (data.servo_outputs && window._motorTab._pushChart) {
         window._motorTab._pushChart(data.servo_outputs);
      }
   };

   // Register as system status hook
   window._app.systemStatusHooks.push(window._motorTab.onSystemStatus);
})();
