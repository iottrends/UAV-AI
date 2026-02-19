// ===== rc-modes.js — RC & Modes tab (Betaflight-style) =====

window._receiverTab = {};
(function () {
   'use strict';

   // ─── Flight mode constants ───────────────────────────────────────────────
   var COPTER_MODES = {
      0:'Stabilize', 1:'Acro', 2:'AltHold', 3:'Auto', 4:'Guided',
      5:'Loiter', 6:'RTL', 7:'Circle', 9:'Land', 11:'Drift',
      13:'Sport', 14:'Flip', 15:'AutoTune', 16:'PosHold', 17:'Brake',
      18:'Throw', 19:'Avoid_ADSB', 20:'Guided_NoGPS', 21:'SmartRTL',
      22:'FlowHold', 23:'Follow', 24:'ZigZag', 25:'SystemID',
      26:'Heli_Autorotate', 27:'Auto RTL'
   };
   var SAFE_MODES      = new Set([0, 2, 5, 16, 17]);
   var AUTO_MODES      = new Set([3, 4, 6, 7, 9, 21, 23, 27]);
   var SLOT_THRESHOLDS = [1230, 1360, 1490, 1620, 1749];

   // Betaflight 16-channel color scheme
   var CH_COLORS = [
      '#f1453d', '#673fb4', '#2b98f0', '#1fbcd2',
      '#159588', '#50ae55', '#cdda49', '#fdc02f',
      '#fc5830', '#785549', '#9e9e9e', '#617d8a',
      '#cf267d', '#7a1464', '#3a7a14', '#14407a'
   ];
   // Slot range colors
   var SLOT_COLORS = ['#f1453d','#673fb4','#2b98f0','#1fbcd2','#159588','#50ae55'];

   // Bar scale 800-2200 (Betaflight)
   var BAR_MIN = 800, BAR_MAX = 2200;
   var CENTER_PWM = 1500;
   // Center tick position as %
   var CENTER_PCT = ((CENTER_PWM - BAR_MIN) / (BAR_MAX - BAR_MIN)) * 100; // 50%

   function modeName(n)  { return COPTER_MODES[n] || ('Mode ' + n); }
   function modeClass(n) {
      if (AUTO_MODES.has(n))  return 'mode-auto';
      if (!SAFE_MODES.has(n)) return 'mode-danger';
      return '';
   }
   function pwmToSlot(pwm) {
      for (var i = 0; i < SLOT_THRESHOLDS.length; i++) {
         if (pwm < SLOT_THRESHOLDS[i]) return i + 1;
      }
      return 6;
   }

   // ─── Channel bar DOM ──────────────────────────────────────────────────────
   var barFills      = [];
   var barInnerVals  = [];
   var barOuterVals  = [];
   var lastChCount   = 0;
   var modeChanIdx   = -1;  // 0-based index of flight mode channel

   function buildChannelBars(numCh, rcmap, fltmodeCh, fltmodes) {
      var panel = document.getElementById('rcChannelsPanel');
      if (!panel) return;
      panel.innerHTML = '';
      barFills     = [];
      barInnerVals = [];
      barOuterVals = [];

      modeChanIdx = (fltmodeCh || 5) - 1;

      // Build channel name lookup
      var chNames = [];
      var mapped  = [rcmap.roll, rcmap.pitch, rcmap.yaw, rcmap.throttle];
      for (var i = 0; i < numCh; i++) {
         var ch1 = i + 1;
         if      (ch1 === rcmap.roll)     chNames.push('ROLL');
         else if (ch1 === rcmap.pitch)    chNames.push('PITCH');
         else if (ch1 === rcmap.yaw)      chNames.push('YAW');
         else if (ch1 === rcmap.throttle) chNames.push('THROTTLE');
         else {
            var priorMapped = 0;
            for (var m = 0; m < mapped.length; m++) { if (mapped[m] < ch1) priorMapped++; }
            chNames.push('AUX' + (ch1 - priorMapped));
         }
      }

      for (var j = 0; j < numCh; j++) {
         var color = CH_COLORS[j % CH_COLORS.length];
         var isModeCh = (j === modeChanIdx);

         var row = document.createElement('div');
         row.className = 'rc-ch-row';

         // Bar track with center tick + optional mode bands
         var trackHtml =
            '<div class="rc-bar-track">' +
               '<div class="rc-bar-fill" id="rcFill' + j + '" style="background:' + color + ';width:0%">' +
                  '<span class="rc-bar-inner-val" id="rcInVal' + j + '"></span>' +
               '</div>' +
               '<div class="rc-bar-center-tick" style="left:' + CENTER_PCT.toFixed(2) + '%"></div>';

         // Add mode range bands for the flight mode channel
         if (isModeCh && fltmodes) {
            var rangeBands = buildModeBands(fltmodes);
            for (var b = 0; b < rangeBands.length; b++) {
               var band = rangeBands[b];
               trackHtml += '<div class="rc-mode-band" style="left:' + band.leftPct + '%;width:' + band.widthPct + '%;background:' + band.color + '"></div>';
               trackHtml += '<div class="rc-mode-band-label" style="left:' + (band.leftPct + band.widthPct/2) + '%">' + band.name + '</div>';
            }
         }

         trackHtml += '</div>';

         row.innerHTML =
            '<span class="rc-ch-name">' + chNames[j] + '</span>' +
            trackHtml +
            '<span class="rc-ch-val" id="rcVal' + j + '">----</span>';

         panel.appendChild(row);
         barFills[j]     = document.getElementById('rcFill' + j);
         barInnerVals[j] = document.getElementById('rcInVal' + j);
         barOuterVals[j] = document.getElementById('rcVal' + j);
      }
      lastChCount = numCh;

      // Build channel map row
      buildChmapRow(rcmap);
   }

   function buildModeBands(fltmodes) {
      // SLOT_THRESHOLDS = [1230, 1360, 1490, 1620, 1749]
      // Slots 1-6 map to PWM ranges
      var thresholds = [BAR_MIN].concat(SLOT_THRESHOLDS).concat([BAR_MAX]);
      var bands = [];
      for (var s = 1; s <= 6; s++) {
         var lo = thresholds[s - 1];
         var hi = thresholds[s];
         var mn = fltmodes[s] !== undefined ? fltmodes[s]
               : (fltmodes[String(s)] !== undefined ? fltmodes[String(s)] : null);
         var leftPct  = ((lo - BAR_MIN) / (BAR_MAX - BAR_MIN)) * 100;
         var widthPct = ((hi - lo) / (BAR_MAX - BAR_MIN)) * 100;
         bands.push({
            leftPct:  leftPct,
            widthPct: widthPct,
            color: SLOT_COLORS[s - 1],
            name: mn !== null ? modeName(mn).substring(0, 6) : 'S' + s
         });
      }
      return bands;
   }

   function buildChmapRow(rcmap) {
      var el = document.getElementById('rcChmapRow');
      if (!el) return;
      var items = [
         { fn: 'ROLL',  ch: rcmap.roll },
         { fn: 'PITCH', ch: rcmap.pitch },
         { fn: 'THR',   ch: rcmap.throttle },
         { fn: 'YAW',   ch: rcmap.yaw }
      ];
      el.innerHTML = items.map(function(it) {
         return '<span class="rc-chmap-item">' + it.fn + ':<span>CH' + it.ch + '</span></span>';
      }).join('');
   }

   function updateBar(idx, pwm) {
      if (!barFills[idx]) return;
      var pct = Math.max(0, Math.min(100, ((pwm - BAR_MIN) / (BAR_MAX - BAR_MIN)) * 100));
      barFills[idx].style.width = pct + '%';

      var label = pwm ? String(pwm) : '--';
      // Show value inside fill when bar is wide enough (>28%)
      if (pct > 28) {
         if (barInnerVals[idx]) barInnerVals[idx].textContent = label;
         if (barOuterVals[idx]) barOuterVals[idx].textContent = '';
      } else {
         if (barInnerVals[idx]) barInnerVals[idx].textContent = '';
         if (barOuterVals[idx]) barOuterVals[idx].textContent = label;
      }
   }

   // ─── Dual gimbal stick boxes ──────────────────────────────────────────────
   function updateGimbal(dotId, gimbalId, xValId, yValId, xPwm, yPwm) {
      var dot    = document.getElementById(dotId);
      var gimbal = document.getElementById(gimbalId);
      if (!dot || !gimbal) return;
      var size = gimbal.offsetWidth || 175;
      var xPct = Math.max(0, Math.min(1, (xPwm  - 1000) / 1000));
      var yPct = Math.max(0, Math.min(1, 1 - (yPwm - 1000) / 1000));
      dot.style.left = (xPct * size) + 'px';
      dot.style.top  = (yPct * size) + 'px';
      // Update axis value readouts
      var xEl = document.getElementById(xValId);
      var yEl = document.getElementById(yValId);
      if (xEl) xEl.textContent = xPwm;
      if (yEl) yEl.textContent = yPwm;
   }

   // ─── Three.js drone model ─────────────────────────────────────────────────
   var rcScene, rcCamera, rcRenderer, rcQuadGroup;
   var rcProps  = [];
   var rcAnimId = null;
   var rcReady  = false;
   var rcTarget  = { roll: 0, pitch: 0, yaw: 0 };
   var rcDisplay = { roll: 0, pitch: 0, yaw: 0 };
   var MAX_TILT  = Math.PI / 4;

   function isRcTabVisible() {
      var t = document.getElementById('receiver-tab');
      return t && t.style.display !== 'none';
   }
   function lerp(a, b, t) { return a + (b - a) * t; }

   function buildRcQuad() {
      rcQuadGroup = new THREE.Group();
      var bodyMat  = new THREE.MeshPhongMaterial({ color: 0x4a5568, shininess: 80 });
      var armMat   = new THREE.MeshPhongMaterial({ color: 0x8899aa, shininess: 60 });
      var motorMat = new THREE.MeshPhongMaterial({ color: 0x667788, shininess: 40 });
      var propMat  = new THREE.MeshPhongMaterial({ color: 0x818cf8, transparent: true, opacity: 0.45, side: THREE.DoubleSide });
      var frontMat = new THREE.MeshPhongMaterial({ color: 0xef4444, emissive: 0x551111 });

      rcQuadGroup.add(new THREE.Mesh(new THREE.BoxGeometry(1.0, 0.3, 1.0), bodyMat));
      var front = new THREE.Mesh(new THREE.ConeGeometry(0.18, 0.5, 4), frontMat);
      front.rotation.x = -Math.PI / 2;
      front.position.set(0, 0.2, -0.7);
      rcQuadGroup.add(front);

      var armLen = 3.2;
      var a1 = new THREE.Mesh(new THREE.BoxGeometry(armLen, 0.1, 0.14), armMat);
      a1.rotation.y = Math.PI / 4; rcQuadGroup.add(a1);
      var a2 = new THREE.Mesh(new THREE.BoxGeometry(armLen, 0.1, 0.14), armMat);
      a2.rotation.y = -Math.PI / 4; rcQuadGroup.add(a2);

      var d = armLen / 2 * 0.707;
      var mpos = [{ x: d, z: -d }, { x: -d, z: -d }, { x: -d, z: d }, { x: d, z: d }];
      for (var i = 0; i < 4; i++) {
         var mo = new THREE.Mesh(new THREE.CylinderGeometry(0.22, 0.28, 0.28, 12), motorMat);
         mo.position.set(mpos[i].x, 0.15, mpos[i].z); rcQuadGroup.add(mo);
         var pr = new THREE.Mesh(new THREE.CylinderGeometry(0.6, 0.6, 0.03, 20), propMat);
         pr.position.set(mpos[i].x, 0.32, mpos[i].z); rcQuadGroup.add(pr);
         rcProps.push(pr);
      }
      return rcQuadGroup;
   }

   function initRcDrone() {
      if (rcReady) return;
      if (typeof THREE === 'undefined') return;
      var container = document.getElementById('rcDroneContainer');
      if (!container) return;
      var w = container.clientWidth, h = container.clientHeight;
      if (w < 10 || h < 10) return;

      rcReady = true;
      rcScene = new THREE.Scene();
      rcScene.background = new THREE.Color(0x07071a);
      rcCamera = new THREE.PerspectiveCamera(45, w / h, 0.1, 100);
      rcCamera.position.set(3.5, 3.0, 4.2);
      rcCamera.lookAt(0, 0, 0);
      rcRenderer = new THREE.WebGLRenderer({ antialias: true });
      rcRenderer.setSize(w, h);
      rcRenderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      container.appendChild(rcRenderer.domElement);

      rcScene.add(new THREE.AmbientLight(0x667799, 0.8));
      var dl = new THREE.DirectionalLight(0xffffff, 1.0);
      dl.position.set(5, 8, 5); rcScene.add(dl);
      var fl = new THREE.DirectionalLight(0x8888ff, 0.4);
      fl.position.set(-3, 4, -3); rcScene.add(fl);
      var grid = new THREE.GridHelper(10, 10, 0x222240, 0x161630);
      grid.position.y = -1.5; rcScene.add(grid);
      rcScene.add(buildRcQuad());
      rcRenderer.render(rcScene, rcCamera);
   }

   function animateRcDrone() {
      if (!isRcTabVisible()) { rcAnimId = null; return; }
      rcAnimId = requestAnimationFrame(animateRcDrone);
      rcDisplay.roll  = lerp(rcDisplay.roll,  rcTarget.roll,  0.12);
      rcDisplay.pitch = lerp(rcDisplay.pitch, rcTarget.pitch, 0.12);
      rcDisplay.yaw  += (rcTarget.yaw - rcDisplay.yaw) * 0.08;
      if (rcQuadGroup) {
         rcQuadGroup.rotation.order = 'YXZ';
         rcQuadGroup.rotation.y = -rcDisplay.yaw;
         rcQuadGroup.rotation.x =  rcDisplay.pitch;
         rcQuadGroup.rotation.z = -rcDisplay.roll;
      }
      for (var i = 0; i < rcProps.length; i++) {
         rcProps[i].rotation.y += (i % 2 === 0 ? 0.3 : -0.3);
      }
      rcRenderer.render(rcScene, rcCamera);
   }

   function startRcAnim() {
      if (!rcAnimId && rcRenderer) rcAnimId = requestAnimationFrame(animateRcDrone);
   }

   // ─── Main system-status handler ───────────────────────────────────────────
   window._receiverTab.onSystemStatus = function (data) {
      if (!data.rc_channels) return;
      var ch        = data.rc_channels;
      var rcmap     = data.rcmap || { roll: 1, pitch: 2, throttle: 3, yaw: 4 };
      var fltmodeCh = data.fltmode_ch || 5;
      var numCh     = data.rc_chancount || 8;
      var fltmodes  = data.fltmodes || {};

      // ── Signal header ──────────────────────────────────────────────────────
      var rssiRaw = data.rc_rssi || 0;
      var rssiPct = Math.round((rssiRaw / 255) * 100);
      var fill = document.getElementById('rcRssiFill');
      if (fill) {
         fill.style.width      = rssiPct + '%';
         fill.style.background = rssiPct > 60 ? '#22c55e' : rssiPct > 30 ? '#f59e0b' : '#ef4444';
      }
      var pctEl = document.getElementById('rcRssiPct');
      if (pctEl) pctEl.textContent = rssiPct + '%';
      var dot = document.getElementById('rcStatusDot');
      if (dot) dot.className = 'rc-dot' + (rssiRaw > 0 ? ' connected' : '');
      var pe = document.getElementById('rcProtocolChip');
      var ue = document.getElementById('rcUartChip');
      var ce = document.getElementById('rcChCountChip');
      if (pe) pe.textContent = data.rc_protocol || '--';
      if (ue) ue.textContent = data.rc_uart     || '--';
      if (ce) ce.textContent = 'CH ' + numCh;

      // ── Rebuild channel bars when count changes ────────────────────────────
      if (numCh !== lastChCount) buildChannelBars(numCh, rcmap, fltmodeCh, fltmodes);

      // ── Update all channel bars ────────────────────────────────────────────
      for (var i = 0; i < numCh; i++) updateBar(i, ch[i] || 0);

      // ── Gimbal stick boxes ─────────────────────────────────────────────────
      var rollPwm  = ch[rcmap.roll     - 1] || 1500;
      var pitchPwm = ch[rcmap.pitch    - 1] || 1500;
      var yawPwm   = ch[rcmap.yaw      - 1] || 1500;
      var thrPwm   = ch[rcmap.throttle - 1] || 1000;

      // Left gimbal: X=Yaw, Y=Throttle
      updateGimbal('rcDotLeft', 'rcGimbalLeft', 'rcGimbalLeftX', 'rcGimbalLeftY', yawPwm, thrPwm);
      // Right gimbal: X=Roll, Y=Pitch
      updateGimbal('rcDotRight', 'rcGimbalRight', 'rcGimbalRightX', 'rcGimbalRightY', rollPwm, pitchPwm);

      // ── 3D drone model ─────────────────────────────────────────────────────
      rcTarget.roll  = ((rollPwm  - 1500) / 500) * MAX_TILT;
      rcTarget.pitch = ((pitchPwm - 1500) / 500) * MAX_TILT;
      rcTarget.yaw  += ((yawPwm   - 1500) / 500) * 0.03;
      if (isRcTabVisible()) startRcAnim();

      // ── Current mode badge ─────────────────────────────────────────────────
      var modeNum = data.current_mode !== undefined ? data.current_mode : 0;
      var badge = document.getElementById('rcCurrentModeBadge');
      if (badge) {
         badge.textContent = modeName(modeNum);
         badge.className   = 'rc-mode-active-badge ' + modeClass(modeNum);
      }

      // ── Flight mode slots ──────────────────────────────────────────────────
      var modeChPwm  = ch[fltmodeCh - 1] || 0;
      var activeSlot = modeChPwm > 0 ? pwmToSlot(modeChPwm) : 0;
      var slotsEl    = document.getElementById('rcModeSlots');
      if (slotsEl) {
         var html = '';
         for (var s = 1; s <= 6; s++) {
            var mn = fltmodes[s] !== undefined ? fltmodes[s]
                   : (fltmodes[String(s)] !== undefined ? fltmodes[String(s)] : 0);
            var isActive = (s === activeSlot);
            html += '<div class="rc-mode-slot' + (isActive ? ' slot-active' : '') + '">';
            html += '<div class="rc-mode-slot-num">' + s + '</div>';
            html += '<div class="rc-mode-slot-bar"><div class="rc-mode-slot-fill" style="width:' + (isActive ? 100 : 0) + '%"></div></div>';
            html += '<div class="rc-mode-slot-name">' + modeName(mn) + '</div>';
            html += '</div>';
         }
         slotsEl.innerHTML = html;
      }
   };

   // ─── Tab shown — lazy-init 3D ─────────────────────────────────────────────
   function onRcTabShown() {
      if (!rcReady) {
         setTimeout(function () { initRcDrone(); startRcAnim(); }, 100);
      } else {
         var c = document.getElementById('rcDroneContainer');
         if (rcRenderer && c) {
            var w = c.clientWidth, h = c.clientHeight;
            if (w > 10 && h > 10) {
               rcCamera.aspect = w / h;
               rcCamera.updateProjectionMatrix();
               rcRenderer.setSize(w, h);
            }
         }
         setTimeout(startRcAnim, 50);
      }
   }

   function init() {
      if (window._app && window._app.systemStatusHooks) {
         window._app.systemStatusHooks.push(window._receiverTab.onSystemStatus);
      }
      document.querySelectorAll('.menu-item[data-tab]').forEach(function (item) {
         item.addEventListener('click', function () {
            if (item.getAttribute('data-tab') === 'receiver') onRcTabShown();
         });
      });
   }

   if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', init);
   } else {
      init();
   }
})();
