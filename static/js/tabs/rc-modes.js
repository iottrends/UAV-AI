// ===== rc-modes.js — RC & Modes tab =====

window._receiverTab = {};
(function() {
   var COPTER_MODES = {
      0:'Stabilize', 1:'Acro', 2:'AltHold', 3:'Auto', 4:'Guided',
      5:'Loiter', 6:'RTL', 7:'Circle', 9:'Land', 11:'Drift',
      13:'Sport', 14:'Flip', 15:'AutoTune', 16:'PosHold', 17:'Brake',
      18:'Throw', 19:'Avoid_ADSB', 20:'Guided_NoGPS', 21:'SmartRTL',
      22:'FlowHold', 23:'Follow', 24:'ZigZag', 25:'SystemID',
      26:'Heli_Autorotate', 27:'Auto RTL'
   };
   var SAFE_MODES = new Set([0, 2, 5, 16, 17]);
   var AUTO_MODES = new Set([3, 4, 6, 7, 9, 21, 23, 27]);

   function modeName(n) { return COPTER_MODES[n] || ('Mode ' + n); }
   function modeClass(n) {
      if (AUTO_MODES.has(n)) return 'mode-auto';
      if (!SAFE_MODES.has(n)) return 'mode-danger';
      return '';
   }

   var SLOT_THRESHOLDS = [1230, 1360, 1490, 1620, 1749];
   function pwmToSlot(pwm) {
      if (pwm < SLOT_THRESHOLDS[0]) return 1;
      if (pwm < SLOT_THRESHOLDS[1]) return 2;
      if (pwm < SLOT_THRESHOLDS[2]) return 3;
      if (pwm < SLOT_THRESHOLDS[3]) return 4;
      if (pwm < SLOT_THRESHOLDS[4]) return 5;
      return 6;
   }

   var container = document.getElementById('rcBarsContainer');
   function channelLabel(ch, rcmap) {
      if (ch === rcmap.roll) return 'CH' + ch + ' Roll';
      if (ch === rcmap.pitch) return 'CH' + ch + ' Pitch';
      if (ch === rcmap.throttle) return 'CH' + ch + ' Throt';
      if (ch === rcmap.yaw) return 'CH' + ch + ' Yaw';
      return 'CH' + ch + ' AUX';
   }
   function isCenterReturn(ch, rcmap) {
      return ch === rcmap.roll || ch === rcmap.pitch || ch === rcmap.yaw;
   }

   // Create 8 bar rows
   for (var i = 1; i <= 8; i++) {
      var row = document.createElement('div');
      row.className = 'rc-bar-row';
      row.innerHTML =
         '<span class="rc-bar-label" id="rcLabel' + i + '">CH' + i + '</span>' +
         '<div class="rc-bar-track">' +
            '<div class="rc-bar-center-line"></div>' +
            '<div class="rc-bar-fill" id="rcFill' + i + '"></div>' +
         '</div>' +
         '<span class="rc-bar-value" id="rcVal' + i + '">0</span>';
      container.appendChild(row);
   }

   window._receiverTab.onSystemStatus = function(data) {
      if (!data.rc_channels) return;
      var rcmap = data.rcmap || {roll:1, pitch:2, throttle:3, yaw:4};
      var channels = data.rc_channels;
      var fltmodeCh = data.fltmode_ch || 5;

      // Update RSSI and channel count
      var rssiBadge = document.getElementById('rcRssiBadge');
      var chBadge = document.getElementById('rcChancountBadge');
      if (rssiBadge) rssiBadge.textContent = 'RSSI: ' + (data.rc_rssi || 0);
      if (chBadge) chBadge.textContent = 'CH: ' + (data.rc_chancount || 0);

      // Update protocol and UART badges
      var protoBadge = document.getElementById('rcProtocolBadge');
      var uartBadge = document.getElementById('rcUartBadge');
      if (protoBadge) protoBadge.textContent = 'Protocol: ' + (data.rc_protocol || '--');
      if (uartBadge) uartBadge.textContent = 'UART: ' + (data.rc_uart || '--');

      // Update bars
      for (var i = 1; i <= 8; i++) {
         var pwm = channels[i - 1] || 0;
         var label = document.getElementById('rcLabel' + i);
         var fill = document.getElementById('rcFill' + i);
         var val = document.getElementById('rcVal' + i);
         if (label) label.textContent = channelLabel(i, rcmap);
         if (val) val.textContent = pwm;

         if (!fill) continue;

         var typeClass;
         if (i === rcmap.throttle) typeClass = 'throttle';
         else if (isCenterReturn(i, rcmap)) typeClass = 'center-return';
         else typeClass = 'aux';

         if (!fill.classList.contains(typeClass)) {
            fill.classList.remove('throttle', 'center-return', 'aux');
            fill.classList.add(typeClass);
         }

         if (pwm === 0) {
            fill.style.width = '0%';
            fill.style.left = '0%';
            continue;
         }

         if (typeClass === 'throttle' || typeClass === 'aux') {
            var pct = Math.max(0, Math.min(100, ((pwm - 1000) / 1000) * 100));
            fill.style.left = '0%';
            fill.style.width = pct + '%';
         } else {
            var center = 50;
            var pos = ((pwm - 1000) / 1000) * 100;
            if (pos >= center) {
               fill.style.left = center + '%';
               fill.style.width = (pos - center) + '%';
            } else {
               fill.style.left = pos + '%';
               fill.style.width = (center - pos) + '%';
            }
         }
      }

      // Current flight mode badge
      var modeBadge = document.getElementById('rcCurrentModeBadge');
      var modeNum = data.current_mode !== undefined ? data.current_mode : 0;
      if (modeBadge) {
         modeBadge.textContent = modeName(modeNum) + ' (' + modeNum + ')';
         modeBadge.className = 'rc-mode-badge ' + modeClass(modeNum);
      }

      // Mode table
      var fltmodes = data.fltmodes || {};
      var modeChPwm = channels[(fltmodeCh - 1)] || 0;
      var activeSlot = modeChPwm > 0 ? pwmToSlot(modeChPwm) : 0;
      var tbody = document.getElementById('rcModeTableBody');
      if (tbody) {
         var pwmRanges = ['< 1230', '1230 \u2013 1360', '1360 \u2013 1490', '1490 \u2013 1620', '1620 \u2013 1749', '\u2265 1749'];
         var html = '';
         for (var s = 1; s <= 6; s++) {
            var mNum = fltmodes[s] !== undefined ? fltmodes[s] : (fltmodes[String(s)] !== undefined ? fltmodes[String(s)] : 0);
            var isActive = (s === activeSlot);
            html += '<tr class="' + (isActive ? 'rc-mode-active' : '') + '">';
            html += '<td>' + s + '</td>';
            html += '<td>' + modeName(mNum) + '</td>';
            html += '<td>' + pwmRanges[s - 1] + '</td>';
            html += '<td>' + (isActive ? '<span class="rc-mode-active-dot"></span>' : '') + '</td>';
            html += '</tr>';
         }
         tbody.innerHTML = html;
      }
   };

   // Register as system status hook
   window._app.systemStatusHooks.push(window._receiverTab.onSystemStatus);
})();
