// ===== drone-view.js — Live 3D quadcopter visualization =====

(function() {
    'use strict';

    // Copter flight mode map (ArduCopter custom_mode values)
    var COPTER_MODES = {
        0: 'STABILIZE', 1: 'ACRO', 2: 'ALT_HOLD', 3: 'AUTO',
        4: 'GUIDED', 5: 'LOITER', 6: 'RTL', 7: 'CIRCLE',
        9: 'LAND', 11: 'DRIFT', 13: 'SPORT', 14: 'FLIP',
        15: 'AUTOTUNE', 16: 'POSHOLD', 17: 'BRAKE', 18: 'THROW',
        19: 'AVOID_ADSB', 20: 'GUIDED_NOGPS', 21: 'SMART_RTL',
        22: 'FLOWHOLD', 23: 'FOLLOW', 24: 'ZIGZAG', 25: 'SYSTEMID',
        26: 'AUTOROTATE', 27: 'AUTO_RTL'
    };

    // ===== Configurable OSD =====
    var OSD_FIELDS = [
        { key: 'bat_volt',   label: 'BAT',      unit: 'V',    path: function(d) { return d.battery ? d.battery.voltage.toFixed(2) : null; } },
        { key: 'bat_pct',    label: 'BAT',      unit: '%',    path: function(d) { return d.battery ? d.battery.percentage : null; } },
        { key: 'gps_fix',    label: 'GPS',      unit: '',     path: function(d) { return d.gps ? (['No GPS','No Fix','2D','3D','DGPS','RTK F','RTK'][d.gps.fix_type] || d.gps.fix_type) : null; } },
        { key: 'gps_sats',   label: 'SATS',     unit: '',     path: function(d) { return d.gps ? d.gps.satellites_visible : null; } },
        { key: 'gps_lat',    label: 'LAT',      unit: '',     path: function(d) { return d.gps && d.gps.lat !== undefined ? d.gps.lat.toFixed(7) : null; } },
        { key: 'gps_lon',    label: 'LON',      unit: '',     path: function(d) { return d.gps && d.gps.lon !== undefined ? d.gps.lon.toFixed(7) : null; } },
        { key: 'alt',        label: 'ALT',      unit: 'm',    path: function(d) { return d.altitude !== undefined ? d.altitude.toFixed(1) : null; } },
        { key: 'gndspd',     label: 'GND SPD',  unit: 'm/s',  path: function(d) { return d.groundspeed !== undefined ? d.groundspeed.toFixed(1) : null; } },
        { key: 'airspd',     label: 'AIR SPD',  unit: 'm/s',  path: function(d) { return d.airspeed !== undefined ? d.airspeed.toFixed(1) : null; } },
        { key: 'vspd',       label: 'V/S',      unit: 'm/s',  path: function(d) { return d.climb !== undefined ? d.climb.toFixed(1) : null; } },
        { key: 'hdg',        label: 'HDG',      unit: '\u00B0',    path: function(d) { return d.heading !== undefined ? d.heading : null; } },
        { key: 'mode',       label: 'MODE',     unit: '',     path: function(d) { return d.current_mode !== undefined ? (COPTER_MODES[d.current_mode] || d.current_mode) : null; } },
        { key: 'armed',      label: 'STATE',    unit: '',     path: function(d) { return d.armed !== undefined ? (d.armed ? 'ARMED' : 'DISARMED') : null; } },
        { key: 'roll',       label: 'ROLL',     unit: '\u00B0',    path: function(d) { return d.attitude_roll !== undefined ? d.attitude_roll.toFixed(1) : null; } },
        { key: 'pitch',      label: 'PITCH',    unit: '\u00B0',    path: function(d) { return d.attitude_pitch !== undefined ? d.attitude_pitch.toFixed(1) : null; } },
        { key: 'yaw',        label: 'YAW',      unit: '\u00B0',    path: function(d) { return d.attitude_yaw !== undefined ? d.attitude_yaw.toFixed(1) : null; } },
        { key: 'rc_rssi',    label: 'RSSI',     unit: '',     path: function(d) { return d.rc_rssi !== undefined ? d.rc_rssi : null; } },
        { key: 'latency',    label: 'LINK',     unit: 'ms',   path: function(d) { return d.latency !== undefined ? d.latency : null; } },
        { key: 'pkt_rate',   label: 'RX',       unit: 'pkt/s', path: function(d) { return d.link_stats ? d.link_stats.pkt_rate : null; } },
        { key: 'link_spd',   label: 'BW',       unit: 'B/s',  path: function(d) { return d.link_stats ? Math.round(d.link_stats.byte_rate) : null; } },
    ];

    var OSD_STORAGE_KEY = 'uav-ai-osd-fields';
    var osdEnabled = loadOsdFields();
    var lastStatusData = null;

    function loadOsdFields() {
        try {
            var stored = localStorage.getItem(OSD_STORAGE_KEY);
            if (stored) {
                var arr = JSON.parse(stored);
                if (Array.isArray(arr)) return new Set(arr);
            }
        } catch (e) {}
        return new Set();
    }

    function saveOsdFields() {
        var arr = [];
        osdEnabled.forEach(function(k) { arr.push(k); });
        localStorage.setItem(OSD_STORAGE_KEY, JSON.stringify(arr));
    }

    // Persistent OSD element cache
    var osdElementCache = {};

    function updateOsd() {
        var container = getEl('dvOsdContainer');
        if (!container || !lastStatusData) return;

        var wantedKeys = [];
        for (var i = 0; i < OSD_FIELDS.length; i++) {
            if (osdEnabled.has(OSD_FIELDS[i].key)) wantedKeys.push(OSD_FIELDS[i].key);
        }

        for (var cachedKey in osdElementCache) {
            if (wantedKeys.indexOf(cachedKey) === -1) {
                var old = osdElementCache[cachedKey].el;
                if (old.parentNode) old.parentNode.removeChild(old);
                delete osdElementCache[cachedKey];
            }
        }

        for (var j = 0; j < wantedKeys.length; j++) {
            var key = wantedKeys[j];
            var field = null;
            for (var k = 0; k < OSD_FIELDS.length; k++) {
                if (OSD_FIELDS[k].key === key) { field = OSD_FIELDS[k]; break; }
            }
            if (!field) continue;

            var val = field.path(lastStatusData);
            if (val === null) val = '--';
            var text = val + (field.unit ? ' ' + field.unit : '');

            if (!osdElementCache[key]) {
                var div = document.createElement('div');
                div.className = 'drone-osd-item';
                var lbl = document.createElement('span');
                lbl.className = 'osd-label';
                lbl.textContent = field.label;
                var valSpan = document.createElement('span');
                valSpan.className = 'osd-value';
                valSpan.textContent = text;
                div.appendChild(lbl);
                div.appendChild(valSpan);
                container.appendChild(div);
                osdElementCache[key] = { el: div, valSpan: valSpan };
            } else {
                osdElementCache[key].valSpan.textContent = text;
            }
        }
    }

    function clearOsdCache() {
        var container = getEl('dvOsdContainer');
        if (container) container.innerHTML = '';
        osdElementCache = {};
    }

    function openOsdConfig() {
        var modal = document.getElementById('osdConfigModal');
        var list = document.getElementById('osdFieldList');
        if (!modal || !list) return;

        var html = '';
        for (var i = 0; i < OSD_FIELDS.length; i++) {
            var field = OSD_FIELDS[i];
            var checked = osdEnabled.has(field.key) ? ' checked' : '';
            var preview = '';
            if (lastStatusData) {
                var val = field.path(lastStatusData);
                if (val !== null) preview = ' <span style="color:#22c55e;font-family:monospace;font-size:0.8rem;">' + val + (field.unit ? ' ' + field.unit : '') + '</span>';
            }
            html += '<label class="osd-field-row"><input type="checkbox" data-osd-key="' + field.key + '"' + checked + '> ' + field.label + (field.unit ? ' (' + field.unit + ')' : '') + preview + '</label>';
        }
        list.innerHTML = html;

        var checkboxes = list.querySelectorAll('input[type="checkbox"]');
        checkboxes.forEach(function(cb) {
            cb.addEventListener('change', function() {
                var key = cb.getAttribute('data-osd-key');
                if (cb.checked) {
                    osdEnabled.add(key);
                } else {
                    osdEnabled.delete(key);
                }
                saveOsdFields();
                updateOsd();
            });
        });

        modal.style.display = 'flex';
    }

    // ===== Telemetry targets & display (lerped) =====
    var target = {
        roll: 0, pitch: 0, yaw: 0,
        altitude: 0, climb: 0, heading: 0,
        armed: false, mode: 0,
        motors: [0, 0, 0, 0]
    };

    var display = {
        roll: 0, pitch: 0, yaw: 0,
        altitude: 0, climb: 0, heading: 0,
        motors: [0, 0, 0, 0]
    };

    // ===== Three.js state =====
    var scene, camera, renderer, quadGroup;
    var propellers = [];
    var threeInitialized = false;
    var animFrameId = null;

    var OSD_MIN_INTERVAL_MS = 200;
    var lastOsdUpdateTs = 0;

    var els = {};
    function getEl(id) {
        if (!els[id]) els[id] = document.getElementById(id);
        return els[id];
    }

    function lerp(a, b, t) { return a + (b - a) * t; }
    function lerpAngle(a, b, t) {
        var diff = b - a;
        while (diff > 180) diff -= 360;
        while (diff < -180) diff += 360;
        return a + diff * t;
    }

    function motorColor(pct) {
        if (pct < 30) return '#22c55e';
        if (pct < 70) return '#eab308';
        return '#ef4444';
    }

    function isTabVisible() {
        var tab = getEl('drone-view-tab');
        return tab && tab.style.display !== 'none';
    }

    // ===== Build 3D quad model =====
    function buildQuadModel() {
        quadGroup = new THREE.Group();

        var bodyMat = new THREE.MeshPhongMaterial({ color: 0x4a5568, shininess: 80 });
        var armMat = new THREE.MeshPhongMaterial({ color: 0x8899aa, shininess: 60 });
        var motorMat = new THREE.MeshPhongMaterial({ color: 0x667788, shininess: 40 });
        var propMat = new THREE.MeshPhongMaterial({ color: 0x818cf8, transparent: true, opacity: 0.45, side: THREE.DoubleSide });
        var frontMat = new THREE.MeshPhongMaterial({ color: 0xef4444, emissive: 0x551111 });

        // Center body
        var bodyGeo = new THREE.BoxGeometry(1.0, 0.3, 1.0);
        var body = new THREE.Mesh(bodyGeo, bodyMat);
        quadGroup.add(body);

        // Front indicator
        var frontGeo = new THREE.ConeGeometry(0.18, 0.5, 4);
        var front = new THREE.Mesh(frontGeo, frontMat);
        front.rotation.x = -Math.PI / 2;
        front.position.set(0, 0.2, -0.7);
        quadGroup.add(front);

        // X-frame arms
        var armLength = 3.2;
        var armGeo = new THREE.BoxGeometry(armLength, 0.1, 0.14);

        var arm1 = new THREE.Mesh(armGeo, armMat);
        arm1.rotation.y = Math.PI / 4;
        quadGroup.add(arm1);

        var arm2 = new THREE.Mesh(armGeo, armMat);
        arm2.rotation.y = -Math.PI / 4;
        quadGroup.add(arm2);

        // Motor positions
        var d = armLength / 2 * 0.707;
        var motorPositions = [
            { x:  d, z: -d },
            { x: -d, z: -d },
            { x: -d, z:  d },
            { x:  d, z:  d },
        ];

        for (var i = 0; i < 4; i++) {
            var mp = motorPositions[i];

            var motorGeo = new THREE.CylinderGeometry(0.22, 0.28, 0.28, 12);
            var motor = new THREE.Mesh(motorGeo, motorMat);
            motor.position.set(mp.x, 0.15, mp.z);
            quadGroup.add(motor);

            var propGeo = new THREE.CylinderGeometry(0.6, 0.6, 0.03, 20);
            var prop = new THREE.Mesh(propGeo, propMat);
            prop.position.set(mp.x, 0.32, mp.z);
            quadGroup.add(prop);
            propellers.push(prop);
        }

        return quadGroup;
    }

    // ===== Lazy 3D init (called on first tab show) =====
    function init3D() {
        if (threeInitialized) return;
        if (typeof THREE === 'undefined') {
            console.warn('Three.js not loaded, skipping 3D drone view');
            return;
        }

        var container = document.getElementById('dv3dContainer');
        if (!container) return;

        var w = container.clientWidth;
        var h = container.clientHeight;
        if (w < 10 || h < 10) {
            console.warn('dv3dContainer has no dimensions yet, deferring');
            return;
        }

        threeInitialized = true;

        scene = new THREE.Scene();
        scene.background = new THREE.Color(0x0d0d1a);

        camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 100);
        camera.position.set(3.5, 3.5, 4.5);
        camera.lookAt(0, 0, 0);

        renderer = new THREE.WebGLRenderer({ antialias: true });
        renderer.setSize(w, h);
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        container.appendChild(renderer.domElement);

        // Lighting
        var ambient = new THREE.AmbientLight(0x667799, 0.8);
        scene.add(ambient);

        var dirLight = new THREE.DirectionalLight(0xffffff, 1.0);
        dirLight.position.set(5, 8, 5);
        scene.add(dirLight);

        var fillLight = new THREE.DirectionalLight(0x8888ff, 0.4);
        fillLight.position.set(-3, 4, -3);
        scene.add(fillLight);

        // Ground grid
        var gridHelper = new THREE.GridHelper(12, 12, 0x333355, 0x222244);
        gridHelper.position.y = -1.5;
        scene.add(gridHelper);

        // Build quad
        var quad = buildQuadModel();
        scene.add(quad);

        // Resize handler
        window.addEventListener('resize', function() {
            if (!renderer || !isTabVisible()) return;
            var rw = container.clientWidth;
            var rh = container.clientHeight;
            if (rw < 10 || rh < 10) return;
            camera.aspect = rw / rh;
            camera.updateProjectionMatrix();
            renderer.setSize(rw, rh);
        });

        renderer.render(scene, camera);
        console.log('3D drone view initialized:', w + 'x' + h);
    }

    function resizeRenderer() {
        if (!renderer) return;
        var container = document.getElementById('dv3dContainer');
        if (!container) return;
        var w = container.clientWidth;
        var h = container.clientHeight;
        if (w < 10 || h < 10) return;
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        renderer.setSize(w, h);
    }

    // ===== Update HUD DOM elements =====
    function updateDOM() {
        var modeName = COPTER_MODES[target.mode] || 'MODE ' + target.mode;
        var modeBadge = getEl('dvModeBadge');
        if (modeBadge) modeBadge.textContent = modeName;

        var armedBadge = getEl('dvArmedBadge');
        if (armedBadge) {
            armedBadge.textContent = target.armed ? 'ARMED' : 'DISARMED';
            armedBadge.className = 'drone-hud-badge drone-armed-badge ' + (target.armed ? 'armed' : 'disarmed');
        }

        var hdg = getEl('dvHeading');
        if (hdg) hdg.textContent = Math.round(display.heading);

        var alt = getEl('dvAltitude');
        if (alt) alt.textContent = display.altitude.toFixed(1);

        var clm = getEl('dvClimb');
        if (clm) clm.textContent = display.climb.toFixed(1);

        var r = getEl('dvRoll');
        if (r) r.textContent = display.roll.toFixed(1);
        var p = getEl('dvPitch');
        if (p) p.textContent = display.pitch.toFixed(1);
        var y = getEl('dvYaw');
        if (y) y.textContent = display.yaw.toFixed(1);

        for (var i = 0; i < 4; i++) {
            var pct = Math.round(display.motors[i]);
            var fill = getEl('dvMotor' + (i + 1));
            var label = getEl('dvMotor' + (i + 1) + 'Pct');
            if (fill) {
                fill.style.width = pct + '%';
                fill.style.backgroundColor = motorColor(pct);
            }
            if (label) label.textContent = pct + '%';
        }
    }

    // ===== Animation loop =====
    function animationLoop() {
        if (!isTabVisible()) {
            animFrameId = null;
            return;
        }

        animFrameId = requestAnimationFrame(animationLoop);

        var f = 0.15;

        display.roll = lerp(display.roll, target.roll, f);
        display.pitch = lerp(display.pitch, target.pitch, f);
        display.yaw = lerpAngle(display.yaw, target.yaw, f);
        display.altitude = lerp(display.altitude, target.altitude, f);
        display.climb = lerp(display.climb, target.climb, f);
        display.heading = lerpAngle(display.heading, target.heading, f);

        for (var i = 0; i < 4; i++) {
            display.motors[i] = lerp(display.motors[i], target.motors[i], f);
        }

        // Update 3D model attitude
        if (quadGroup) {
            var rollRad = display.roll * Math.PI / 180;
            var pitchRad = display.pitch * Math.PI / 180;
            var yawRad = display.yaw * Math.PI / 180;

            quadGroup.rotation.set(0, 0, 0);
            quadGroup.rotation.order = 'YXZ';
            quadGroup.rotation.y = -yawRad;
            quadGroup.rotation.x = pitchRad;
            quadGroup.rotation.z = -rollRad;
        }

        // Spin propellers
        for (var p = 0; p < propellers.length; p++) {
            propellers[p].rotation.y += (p % 2 === 0 ? 0.3 : -0.3);
        }

        if (renderer) renderer.render(scene, camera);
        updateDOM();
    }

    function startAnimation() {
        if (!animFrameId && threeInitialized) {
            animFrameId = requestAnimationFrame(animationLoop);
        }
    }

    function onSystemStatus(data) {
        if (data.attitude_roll !== undefined) target.roll = data.attitude_roll;
        if (data.attitude_pitch !== undefined) target.pitch = data.attitude_pitch;
        if (data.attitude_yaw !== undefined) target.yaw = data.attitude_yaw;
        if (data.altitude !== undefined) target.altitude = data.altitude;
        if (data.climb !== undefined) target.climb = data.climb;
        if (data.heading !== undefined) target.heading = data.heading;
        if (data.armed !== undefined) target.armed = data.armed;
        if (data.current_mode !== undefined) target.mode = data.current_mode;

        if (data.servo_outputs && data.servo_outputs.length >= 4) {
            for (var i = 0; i < 4; i++) {
                var pwm = data.servo_outputs[i] || 1000;
                target.motors[i] = Math.max(0, Math.min(100, (pwm - 1000) / 10));
            }
        }

        lastStatusData = data;
        if (isTabVisible()) {
            var now = Date.now();
            if (now - lastOsdUpdateTs > OSD_MIN_INTERVAL_MS) {
                updateOsd();
                lastOsdUpdateTs = now;
            }
            startAnimation();
        }
    }

    // Called when user clicks the drone-view tab
    function onTabShown() {
        if (!threeInitialized) {
            setTimeout(function() {
                init3D();
                startAnimation();
            }, 100);
        } else {
            resizeRenderer();
            setTimeout(startAnimation, 50);
        }
    }

    // ===== Init =====
    function init() {
        // Register system status hook
        if (window._app && window._app.systemStatusHooks) {
            window._app.systemStatusHooks.push(onSystemStatus);
        }

        // OSD config button
        var configBtn = document.getElementById('dvOsdConfigBtn');
        if (configBtn) {
            configBtn.addEventListener('click', openOsdConfig);
        }

        // OSD config modal close
        var closeBtn = document.getElementById('osdConfigClose');
        if (closeBtn) {
            closeBtn.addEventListener('click', function() {
                var modal = document.getElementById('osdConfigModal');
                if (modal) modal.style.display = 'none';
            });
        }

        // OSD config modal clear all
        var clearBtn = document.getElementById('osdConfigClear');
        if (clearBtn) {
            clearBtn.addEventListener('click', function() {
                osdEnabled.clear();
                saveOsdFields();
                clearOsdCache();
                var cbs = document.querySelectorAll('#osdFieldList input[type="checkbox"]');
                cbs.forEach(function(cb) { cb.checked = false; });
            });
        }

        // Close modal on backdrop click
        var modal = document.getElementById('osdConfigModal');
        if (modal) {
            modal.addEventListener('click', function(e) {
                if (e.target === modal) modal.style.display = 'none';
            });
        }

        // Lazy-init 3D on first tab visit
        var menuItems = document.querySelectorAll('.menu-item[data-tab]');
        menuItems.forEach(function(item) {
            item.addEventListener('click', function() {
                if (item.getAttribute('data-tab') === 'drone-view') {
                    onTabShown();
                }
            });
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
