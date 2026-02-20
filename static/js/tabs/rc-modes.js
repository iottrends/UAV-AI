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
   var FS_THR_ENABLE_OPTIONS = { 0: 'Disabled', 1: 'Enabled (Always RTL/Land)', 2: 'Enabled (Continue mission if possible)' };
   var FS_GCS_ENABLE_OPTIONS = { 0: 'Disabled', 1: 'Enabled', 2: 'Enabled while in Auto' };
   var BATT_FS_ACTION_OPTIONS = { 0: 'None', 1: 'Land', 2: 'RTL', 3: 'SmartRTL', 4: 'Terminate' };
   var AUX_FUNCTION_OPTIONS = {
      0: 'Disabled',
      7: 'Return to Launch (RTL)',
      9: 'Camera Trigger',
      13: 'Beeper',
      15: 'AutoTune',
      30: 'Emergency Stop',
      32: 'Motor Interlock',
      41: 'Arm/Disarm',
      82: 'Smart RTL'
   };
   var AUX_ACTIVE_PWM = 1800;

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
   var latestRcChannels = [];

   var configLoaded = {
      rc_mapping: false,
      flight_modes: false,
      failsafe: false,
      aux_functions: false
   };
   var configState = {
      rc_mapping: { original: {}, edited: {}, preview: null },
      flight_modes: { original: {}, edited: {}, preview: null },
      failsafe: { original: {}, edited: {}, preview: null },
      aux_functions: { original: {}, edited: {}, preview: null }
   };

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
      // PBR materials
      var bodyMat  = new THREE.MeshStandardMaterial({ color: 0x2d3748, metalness: 0.7, roughness: 0.35 });
      var armMat   = new THREE.MeshStandardMaterial({ color: 0x718096, metalness: 0.6, roughness: 0.45 });
      var motorMat = new THREE.MeshStandardMaterial({ color: 0x4a5568, metalness: 0.8, roughness: 0.2 });
      var propMat  = new THREE.MeshStandardMaterial({ color: 0x818cf8, metalness: 0.1, roughness: 0.8, transparent: true, opacity: 0.55, side: THREE.DoubleSide });
      var frontMat = new THREE.MeshStandardMaterial({ color: 0xef4444, metalness: 0.3, roughness: 0.5, emissive: 0x660000, emissiveIntensity: 0.4 });
      var legMat   = new THREE.MeshStandardMaterial({ color: 0x1a202c, metalness: 0.5, roughness: 0.6 });

      // Body
      rcQuadGroup.add(new THREE.Mesh(new THREE.BoxGeometry(1.1, 0.22, 1.1), bodyMat));
      // Top stack plate
      var topPlate = new THREE.Mesh(new THREE.BoxGeometry(0.7, 0.08, 0.7), armMat);
      topPlate.position.y = 0.15; rcQuadGroup.add(topPlate);
      // Front indicator
      var front = new THREE.Mesh(new THREE.ConeGeometry(0.14, 0.42, 4), frontMat);
      front.rotation.x = -Math.PI / 2;
      front.position.set(0, 0.18, -0.68);
      rcQuadGroup.add(front);

      var armLen = 3.2;
      var a1 = new THREE.Mesh(new THREE.BoxGeometry(armLen, 0.09, 0.12), armMat);
      a1.rotation.y = Math.PI / 4; rcQuadGroup.add(a1);
      var a2 = new THREE.Mesh(new THREE.BoxGeometry(armLen, 0.09, 0.12), armMat);
      a2.rotation.y = -Math.PI / 4; rcQuadGroup.add(a2);

      var d = armLen / 2 * 0.707;
      var mpos = [{ x: d, z: -d }, { x: -d, z: -d }, { x: -d, z: d }, { x: d, z: d }];
      for (var i = 0; i < 4; i++) {
         var mo = new THREE.Mesh(new THREE.CylinderGeometry(0.2, 0.24, 0.24, 16), motorMat);
         mo.position.set(mpos[i].x, 0.12, mpos[i].z); rcQuadGroup.add(mo);
         // Crossed blade props
         var bGeo = new THREE.BoxGeometry(1.2, 0.025, 0.18);
         var b1 = new THREE.Mesh(bGeo, propMat);
         b1.position.set(mpos[i].x, 0.28, mpos[i].z); rcQuadGroup.add(b1);
         var b2 = new THREE.Mesh(bGeo, propMat);
         b2.position.set(mpos[i].x, 0.28, mpos[i].z);
         b2.rotation.y = Math.PI / 2; rcQuadGroup.add(b2);
         b1.userData.blade2 = b2;
         rcProps.push(b1);
      }
      // Landing gear
      var skidGeo = new THREE.BoxGeometry(0.08, 0.08, 2.4);
      var s1 = new THREE.Mesh(skidGeo, legMat); s1.position.set(0.55, -0.42, 0); rcQuadGroup.add(s1);
      var s2 = new THREE.Mesh(skidGeo, legMat); s2.position.set(-0.55, -0.42, 0); rcQuadGroup.add(s2);
      var legGeo = new THREE.BoxGeometry(0.07, 0.4, 0.07);
      [{ x:  0.55, z:  0.9 }, { x:  0.55, z: -0.9 },
       { x: -0.55, z:  0.9 }, { x: -0.55, z: -0.9 }].forEach(function(lp) {
         var leg = new THREE.Mesh(legGeo, legMat);
         leg.position.set(lp.x, -0.22, lp.z); rcQuadGroup.add(leg);
      });
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
      rcRenderer.outputEncoding = THREE.sRGBEncoding;
      rcRenderer.toneMapping = 4; // ACESFilmic
      rcRenderer.toneMappingExposure = 1.2;
      container.appendChild(rcRenderer.domElement);

      // 3-point PBR lighting
      var keyLight = new THREE.DirectionalLight(0xfff5e0, 2.0);
      keyLight.position.set(5, 8, 5); rcScene.add(keyLight);
      var fillLight = new THREE.DirectionalLight(0xc0d8ff, 0.6);
      fillLight.position.set(-4, 3, -3); rcScene.add(fillLight);
      var rimLight = new THREE.DirectionalLight(0x8888ff, 0.4);
      rimLight.position.set(0, -2, -6); rcScene.add(rimLight);
      rcScene.add(new THREE.HemisphereLight(0x334466, 0x111122, 0.6));
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
         var delta = i % 2 === 0 ? 0.3 : -0.3;
         rcProps[i].rotation.y += delta;
         if (rcProps[i].userData.blade2) rcProps[i].userData.blade2.rotation.y += delta;
      }
      rcRenderer.render(rcScene, rcCamera);
   }

   function startRcAnim() {
      if (!rcAnimId && rcRenderer) rcAnimId = requestAnimationFrame(animateRcDrone);
   }

   function setConfigStatus(domain, text, cls) {
      var idMap = {
         rc_mapping: 'rcMapStatus',
         flight_modes: 'fltModesStatus',
         failsafe: 'failsafeStatus',
         aux_functions: 'auxStatus'
      };
      var el = document.getElementById(idMap[domain]);
      if (!el) return;
      el.className = 'rc-config-status';
      if (cls) el.classList.add(cls);
      el.textContent = text || '';
   }

   function renderDiff(domain, diffRows) {
      var idMap = {
         rc_mapping: 'rcMapDiff',
         flight_modes: 'fltModesDiff',
         failsafe: 'failsafeDiff',
         aux_functions: 'auxDiff'
      };
      var el = document.getElementById(idMap[domain]);
      if (!el) return;
      if (!diffRows || !diffRows.length) {
         el.innerHTML = '<div class="rc-config-diff-empty">No pending changes.</div>';
         return;
      }
      var html = '';
      for (var i = 0; i < diffRows.length; i++) {
         var d = diffRows[i];
         html += '<div class="rc-config-diff-row"><span>' + d.param + '</span><span>' + d.old + ' -> ' + d.new + '</span></div>';
      }
      el.innerHTML = html;
   }

   function deepClone(obj) {
      return JSON.parse(JSON.stringify(obj || {}));
   }

   function toNum(v, fallback) {
      var n = Number(v);
      return Number.isFinite(n) ? n : fallback;
   }

   function readEditedDomainFromForm(domain) {
      var prefixMap = {
         rc_mapping: 'rcMapInput_',
         flight_modes: 'fltModesInput_',
         failsafe: 'failsafeInput_',
         aux_functions: 'auxInput_'
      };
      var prefix = prefixMap[domain];
      var current = deepClone(configState[domain].edited);
      Object.keys(current).forEach(function(param) {
         var input = document.getElementById(prefix + param);
         if (!input) return;
         current[param] = toNum(input.value, current[param]);
      });
      configState[domain].edited = current;
      return current;
   }

   function getDomainChanges(domain) {
      var state = configState[domain];
      var edited = state.edited || {};
      var original = state.original || {};
      var changes = {};
      Object.keys(edited).forEach(function(param) {
         if (Number(original[param]) !== Number(edited[param])) {
            changes[param] = Number(edited[param]);
         }
      });
      return changes;
   }

   async function loadDomain(domain) {
      try {
         setConfigStatus(domain, 'Loading...', '');
         var res = await fetch('/api/config/domains/' + domain);
         var data = await res.json();
         if (!res.ok || data.status !== 'success') {
            throw new Error(data.message || ('HTTP ' + res.status));
         }
         if (domain === 'aux_functions' && data.metadata) {
            if (data.metadata.function_catalog && Object.keys(data.metadata.function_catalog).length) {
               AUX_FUNCTION_OPTIONS = data.metadata.function_catalog;
            }
            if (data.metadata.activation_pwm) {
               AUX_ACTIVE_PWM = Number(data.metadata.activation_pwm) || 1800;
            }
         }
         configState[domain].original = deepClone(data.params || {});
         configState[domain].edited = deepClone(data.params || {});
         configState[domain].preview = null;
         renderDomain(domain);
         renderDiff(domain, []);
         setConfigStatus(domain, 'Loaded.', 'ok');
         configLoaded[domain] = true;
      } catch (err) {
         console.error('Failed loading domain ' + domain + ':', err);
         setConfigStatus(domain, 'Load error: ' + err.message, 'err');
      }
   }

   function validateRcMapVisuals() {
      var mapping = readEditedDomainFromForm('rc_mapping');
      var values = ['RCMAP_ROLL', 'RCMAP_PITCH', 'RCMAP_THROTTLE', 'RCMAP_YAW'].map(function(k) { return Number(mapping[k]); });
      var counts = {};
      values.forEach(function(v) { counts[v] = (counts[v] || 0) + 1; });
      ['RCMAP_ROLL', 'RCMAP_PITCH', 'RCMAP_THROTTLE', 'RCMAP_YAW'].forEach(function(param) {
         var input = document.getElementById('rcMapInput_' + param);
         if (!input) return;
         if ((counts[Number(mapping[param])] || 0) > 1) input.classList.add('rc-invalid');
         else input.classList.remove('rc-invalid');
      });
      if (Object.keys(counts).some(function(k) { return counts[k] > 1; })) {
         setConfigStatus('rc_mapping', 'Duplicate channels detected. Fix red fields before preview.', 'warn');
         return false;
      }
      return true;
   }

   function optionsFromMap(mapObj, current) {
      var keys = Object.keys(mapObj).map(function(k) { return Number(k); }).sort(function(a, b) { return a - b; });
      var html = '';
      for (var i = 0; i < keys.length; i++) {
         var key = keys[i];
         html += '<option value="' + key + '"' + (Number(current) === key ? ' selected' : '') + '>' + key + ' - ' + mapObj[key] + '</option>';
      }
      return html;
   }

   function auxFunctionName(id) {
      return AUX_FUNCTION_OPTIONS[id] || ('Custom Function ' + id);
   }

   function auxOptionHtml(current, query) {
      var q = (query || '').toLowerCase();
      var keys = Object.keys(AUX_FUNCTION_OPTIONS).map(function(k) { return Number(k); }).sort(function(a, b) { return a - b; });
      var selectedValue = Number(current);
      var html = '';
      var hasSelected = false;
      for (var i = 0; i < keys.length; i++) {
         var key = keys[i];
         var label = AUX_FUNCTION_OPTIONS[key];
         var text = (key + ' - ' + label);
         if (q && text.toLowerCase().indexOf(q) === -1 && key !== selectedValue) continue;
         if (key === selectedValue) hasSelected = true;
         html += '<option value="' + key + '"' + (key === selectedValue ? ' selected' : '') + '>' + text + '</option>';
      }
      if (!hasSelected) {
         html = '<option value="' + selectedValue + '" selected>' + selectedValue + ' - ' + auxFunctionName(selectedValue) + '</option>' + html;
      }
      return html;
   }

   function updateAuxLiveIndicators() {
      var edited = configState.aux_functions.edited || {};
      for (var ch = 7; ch <= 12; ch++) {
         var pwm = Number(latestRcChannels[ch - 1] || 0);
         var pct = Math.max(0, Math.min(100, ((pwm - BAR_MIN) / (BAR_MAX - BAR_MIN)) * 100));
         var optionVal = Number(edited['RC' + ch + '_OPTION'] || 0);
         var isActive = optionVal > 0 && pwm >= AUX_ACTIVE_PWM;
         var fill = document.getElementById('auxPwmFill_' + ch);
         var value = document.getElementById('auxPwmVal_' + ch);
         var chip = document.getElementById('auxActive_' + ch);
         if (fill) {
            fill.style.width = pct + '%';
            fill.style.backgroundColor = isActive ? '#22c55e' : '#2b98f0';
         }
         if (value) value.textContent = pwm ? String(pwm) : '--';
         if (chip) {
            chip.textContent = isActive ? 'ACTIVE' : 'INACTIVE';
            chip.className = 'aux-active-chip' + (isActive ? ' active' : '');
         }
      }
   }

   function channelOptions(current) {
      var html = '';
      for (var ch = 1; ch <= 16; ch++) {
         html += '<option value="' + ch + '"' + (Number(current) === ch ? ' selected' : '') + '>CH' + ch + '</option>';
      }
      return html;
   }

   function renderDomain(domain) {
      var state = configState[domain];
      var edited = state.edited || {};
      if (domain === 'rc_mapping') {
         var rcContainer = document.getElementById('rcMapForm');
         if (!rcContainer) return;
         rcContainer.innerHTML =
            '<div class="rc-config-field"><label>ROLL</label><select id="rcMapInput_RCMAP_ROLL">' + channelOptions(edited.RCMAP_ROLL) + '</select></div>' +
            '<div class="rc-config-field"><label>PITCH</label><select id="rcMapInput_RCMAP_PITCH">' + channelOptions(edited.RCMAP_PITCH) + '</select></div>' +
            '<div class="rc-config-field"><label>THROTTLE</label><select id="rcMapInput_RCMAP_THROTTLE">' + channelOptions(edited.RCMAP_THROTTLE) + '</select></div>' +
            '<div class="rc-config-field"><label>YAW</label><select id="rcMapInput_RCMAP_YAW">' + channelOptions(edited.RCMAP_YAW) + '</select></div>';
         ['RCMAP_ROLL', 'RCMAP_PITCH', 'RCMAP_THROTTLE', 'RCMAP_YAW'].forEach(function(param) {
            var node = document.getElementById('rcMapInput_' + param);
            if (node) node.addEventListener('change', function() { validateRcMapVisuals(); });
         });
         validateRcMapVisuals();
      } else if (domain === 'flight_modes') {
         var fmContainer = document.getElementById('fltModesForm');
         if (!fmContainer) return;
         var html = '';
         html += '<div class="rc-config-field"><label>Mode Channel (FLTMODE_CH)</label><select id="fltModesInput_FLTMODE_CH">' + channelOptions(edited.FLTMODE_CH) + '</select></div>';
         html += '<div class="rc-config-field"><label>Slot 1 (FLTMODE1)</label><select id="fltModesInput_FLTMODE1">' + optionsFromMap(COPTER_MODES, edited.FLTMODE1) + '</select></div>';
         html += '<div class="rc-config-field"><label>Slot 2 (FLTMODE2)</label><select id="fltModesInput_FLTMODE2">' + optionsFromMap(COPTER_MODES, edited.FLTMODE2) + '</select></div>';
         html += '<div class="rc-config-field"><label>Slot 3 (FLTMODE3)</label><select id="fltModesInput_FLTMODE3">' + optionsFromMap(COPTER_MODES, edited.FLTMODE3) + '</select></div>';
         html += '<div class="rc-config-field"><label>Slot 4 (FLTMODE4)</label><select id="fltModesInput_FLTMODE4">' + optionsFromMap(COPTER_MODES, edited.FLTMODE4) + '</select></div>';
         html += '<div class="rc-config-field"><label>Slot 5 (FLTMODE5)</label><select id="fltModesInput_FLTMODE5">' + optionsFromMap(COPTER_MODES, edited.FLTMODE5) + '</select></div>';
         html += '<div class="rc-config-field"><label>Slot 6 (FLTMODE6)</label><select id="fltModesInput_FLTMODE6">' + optionsFromMap(COPTER_MODES, edited.FLTMODE6) + '</select></div>';
         fmContainer.innerHTML = html;
      } else if (domain === 'failsafe') {
         var fsContainer = document.getElementById('failsafeForm');
         if (!fsContainer) return;
         fsContainer.innerHTML =
            '<div class="rc-config-field"><label>Throttle Failsafe (FS_THR_ENABLE)</label><select id="failsafeInput_FS_THR_ENABLE">' + optionsFromMap(FS_THR_ENABLE_OPTIONS, edited.FS_THR_ENABLE) + '</select></div>' +
            '<div class="rc-config-field"><label>Throttle PWM Threshold (FS_THR_VALUE)</label><input id="failsafeInput_FS_THR_VALUE" type="number" min="800" max="2200" value="' + Number(edited.FS_THR_VALUE || 975) + '"><button id="failsafeAutoSetBtn" class="rc-inline-action">Auto-set from current</button></div>' +
            '<div class="rc-config-field"><label>GCS Failsafe (FS_GCS_ENABLE)</label><select id="failsafeInput_FS_GCS_ENABLE">' + optionsFromMap(FS_GCS_ENABLE_OPTIONS, edited.FS_GCS_ENABLE) + '</select></div>' +
            '<div class="rc-config-field"><label>Failsafe Options (FS_OPTIONS)</label><input id="failsafeInput_FS_OPTIONS" type="number" min="0" max="65535" value="' + Number(edited.FS_OPTIONS || 0) + '"></div>' +
            '<div class="rc-config-field"><label>Battery Low Action (BATT_FS_LOW_ACT)</label><select id="failsafeInput_BATT_FS_LOW_ACT">' + optionsFromMap(BATT_FS_ACTION_OPTIONS, edited.BATT_FS_LOW_ACT) + '</select></div>' +
            '<div class="rc-config-field"><label>Battery Critical Action (BATT_FS_CRT_ACT)</label><select id="failsafeInput_BATT_FS_CRT_ACT">' + optionsFromMap(BATT_FS_ACTION_OPTIONS, edited.BATT_FS_CRT_ACT) + '</select></div>';
         var autoSetBtn = document.getElementById('failsafeAutoSetBtn');
         if (autoSetBtn) {
            autoSetBtn.addEventListener('click', function(ev) {
               ev.preventDefault();
               var rcMap = configState.rc_mapping.edited || {};
               var throttleCh = Number(rcMap.RCMAP_THROTTLE || 3) - 1;
               var thrPwm = latestRcChannels[throttleCh] || 0;
               if (!thrPwm) {
                  setConfigStatus('failsafe', 'No RC input yet. Move throttle stick and retry auto-set.', 'warn');
                  return;
               }
               var suggested = Math.max(800, Math.min(2200, Math.round(thrPwm - 20)));
               var input = document.getElementById('failsafeInput_FS_THR_VALUE');
               if (input) input.value = suggested;
               readEditedDomainFromForm('failsafe');
               setConfigStatus('failsafe', 'FS_THR_VALUE set to ' + suggested + ' (throttle CH' + (throttleCh + 1) + ' - 20).', 'ok');
            });
         }
      } else if (domain === 'aux_functions') {
         var auxContainer = document.getElementById('auxFunctionsForm');
         if (!auxContainer) return;
         var searchVal = '';
         var searchInput = document.getElementById('auxFuncSearch');
         if (searchInput) searchVal = searchInput.value || '';
         var htmlAux = '<div class="aux-search-row"><input id="auxFuncSearch" placeholder="Search functions (e.g. gps, rtl, arm)..." value="' + searchVal.replace(/"/g, '&quot;') + '"></div>';
         for (var ch = 7; ch <= 12; ch++) {
            var key = 'RC' + ch + '_OPTION';
            var current = Number(edited[key] || 0);
            htmlAux +=
               '<div class="aux-row">' +
                  '<div class="aux-channel-label">CH' + ch + '</div>' +
                  '<div class="aux-live-wrap">' +
                     '<div class="aux-live-track"><div class="aux-live-fill" id="auxPwmFill_' + ch + '"></div></div>' +
                     '<div class="aux-live-meta"><span id="auxPwmVal_' + ch + '">--</span><span id="auxActive_' + ch + '" class="aux-active-chip">INACTIVE</span></div>' +
                  '</div>' +
                  '<div class="rc-config-field" style="margin:0;"><select id="auxInput_' + key + '">' + auxOptionHtml(current, searchVal) + '</select></div>' +
               '</div>';
         }
         auxContainer.innerHTML = htmlAux;
         var searchEl = document.getElementById('auxFuncSearch');
         if (searchEl) {
            searchEl.addEventListener('input', function() {
               renderDomain('aux_functions');
            });
         }
         for (var auxCh = 7; auxCh <= 12; auxCh++) {
            (function(chNum) {
               var paramKey = 'RC' + chNum + '_OPTION';
               var select = document.getElementById('auxInput_' + paramKey);
               if (select) {
                  select.addEventListener('change', function() {
                     configState.aux_functions.edited[paramKey] = Number(select.value);
                     updateAuxLiveIndicators();
                  });
               }
            })(auxCh);
         }
         updateAuxLiveIndicators();
      }
   }

   async function previewDomain(domain) {
      if (domain === 'rc_mapping' && !validateRcMapVisuals()) return;
      readEditedDomainFromForm(domain);
      var changes = getDomainChanges(domain);
      if (!Object.keys(changes).length) {
         configState[domain].preview = null;
         renderDiff(domain, []);
         setConfigStatus(domain, 'No changes to preview.', '');
         return;
      }
      try {
         setConfigStatus(domain, 'Previewing ' + Object.keys(changes).length + ' parameter(s)...', '');
         var res = await fetch('/api/config/domains/' + domain + '/preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ changes: changes, verify_mode: 'strict' })
         });
         var data = await res.json();
         if (!res.ok || data.status !== 'success') {
            var details = (data.invalid || []).map(function(x) { return x.param + ': ' + x.reason; }).join('; ');
            throw new Error(details || data.message || ('HTTP ' + res.status));
         }
         var changedRows = (data.diff || []).filter(function(d) { return d.changed; });
         configState[domain].preview = { changes: changes, diff: changedRows };
         renderDiff(domain, changedRows);
         setConfigStatus(domain, 'Preview ready (' + changedRows.length + ' changes).', 'ok');
      } catch (err) {
         console.error('Preview failed for ' + domain + ':', err);
         setConfigStatus(domain, 'Preview error: ' + err.message, 'err');
      }
   }

   async function applyDomain(domain) {
      if (!configState[domain].preview || !configState[domain].preview.diff || !configState[domain].preview.diff.length) {
         setConfigStatus(domain, 'Run preview before apply.', 'warn');
         return;
      }
      try {
         setConfigStatus(domain, 'Applying and verifying...', '');
         var res = await fetch('/api/config/domains/' + domain + '/apply', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
               changes: configState[domain].preview.changes,
               verify_timeout_ms: 5000,
               tolerance: 0.0001
            })
         });
         var data = await res.json();
         if (res.status === 200 && data.status === 'success') {
            setConfigStatus(domain, 'Apply success. Verified: ' + data.verified + '.', 'ok');
            await loadDomain(domain);
         } else if (res.status === 207 || data.status === 'partial') {
            var failed = (data.failed || []).map(function(x) { return x.param; });
            var mismatched = (data.mismatched || []).map(function(x) { return x.param; });
            setConfigStatus(
               domain,
               'Partial apply. Verified=' + data.verified + ', failed=' + failed.length + ', mismatched=' + mismatched.length + '.',
               'warn'
            );
         } else {
            throw new Error(data.message || ('HTTP ' + res.status));
         }
      } catch (err) {
         console.error('Apply failed for ' + domain + ':', err);
         setConfigStatus(domain, 'Apply error: ' + err.message, 'err');
      }
   }

   function wireConfigButtons() {
      var wiring = [
         ['rcMapRefreshBtn', function() { loadDomain('rc_mapping'); }],
         ['rcMapPreviewBtn', function() { previewDomain('rc_mapping'); }],
         ['rcMapApplyBtn', function() { applyDomain('rc_mapping'); }],
         ['fltModesRefreshBtn', function() { loadDomain('flight_modes'); }],
         ['fltModesPreviewBtn', function() { previewDomain('flight_modes'); }],
         ['fltModesApplyBtn', function() { applyDomain('flight_modes'); }],
         ['failsafeRefreshBtn', function() { loadDomain('failsafe'); }],
         ['failsafePreviewBtn', function() { previewDomain('failsafe'); }],
         ['failsafeApplyBtn', function() { applyDomain('failsafe'); }],
         ['auxRefreshBtn', function() { loadDomain('aux_functions'); }],
         ['auxPreviewBtn', function() { previewDomain('aux_functions'); }],
         ['auxApplyBtn', function() { applyDomain('aux_functions'); }]
      ];
      for (var i = 0; i < wiring.length; i++) {
         var btn = document.getElementById(wiring[i][0]);
         if (btn) btn.addEventListener('click', wiring[i][1]);
      }
   }

   function ensureConfigLoaded() {
      if (!configLoaded.rc_mapping) loadDomain('rc_mapping');
      if (!configLoaded.flight_modes) loadDomain('flight_modes');
      if (!configLoaded.failsafe) loadDomain('failsafe');
      if (!configLoaded.aux_functions) loadDomain('aux_functions');
   }

   // ─── Main system-status handler ───────────────────────────────────────────
   window._receiverTab.onSystemStatus = function (data) {
      if (!data.rc_channels) return;
      var ch        = data.rc_channels;
      latestRcChannels = ch.slice();
      updateAuxLiveIndicators();
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
      ensureConfigLoaded();
   }

   function init() {
      if (window._app && window._app.systemStatusHooks) {
         window._app.systemStatusHooks.push(window._receiverTab.onSystemStatus);
      }
      wireConfigButtons();
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
