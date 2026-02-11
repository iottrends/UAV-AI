// ===== drone-view.js — Live top-down quadcopter visualization =====

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

    // Target values (from telemetry)
    var target = {
        roll: 0, pitch: 0, yaw: 0,
        altitude: 0, climb: 0, heading: 0,
        armed: false, mode: 0,
        motors: [0, 0, 0, 0]  // percentages 0-100
    };

    // Displayed values (lerped for smooth animation)
    var display = {
        roll: 0, pitch: 0, yaw: 0,
        altitude: 0, climb: 0, heading: 0,
        motors: [0, 0, 0, 0]
    };

    var canvas, ctx;
    var animFrameId = null;

    // DOM element refs (cached after first use)
    var els = {};

    function getEl(id) {
        if (!els[id]) els[id] = document.getElementById(id);
        return els[id];
    }

    function lerp(current, target, factor) {
        return current + (target - current) * factor;
    }

    // Lerp angle handling wrap-around for yaw
    function lerpAngle(current, target, factor) {
        var diff = target - current;
        // Normalize to -180..180
        while (diff > 180) diff -= 360;
        while (diff < -180) diff += 360;
        return current + diff * factor;
    }

    function motorColor(pct) {
        if (pct < 30) return '#22c55e';       // green
        if (pct < 70) return '#eab308';       // yellow
        return '#ef4444';                      // red
    }

    function isTabVisible() {
        var tab = getEl('drone-view-tab');
        return tab && tab.style.display !== 'none';
    }

    function drawDrone() {
        var w = canvas.width;
        var h = canvas.height;
        var cx = w / 2;
        var cy = h / 2;

        ctx.clearRect(0, 0, w, h);
        ctx.save();

        // Apply roll rotation to the whole drawing
        ctx.translate(cx, cy);
        var rollRad = display.roll * Math.PI / 180;
        ctx.rotate(rollRad);

        // Apply pitch as vertical offset (clamped)
        var pitchOffset = Math.max(-60, Math.min(60, display.pitch * 1.5));
        ctx.translate(0, pitchOffset);

        var armLen = 120;
        var bodySize = 30;
        var motorRadius = 22;

        // Motor positions (X-frame: 45deg diagonals)
        // ArduCopter motor order: M1=front-right, M2=rear-left, M3=front-left, M4=rear-right
        var motorPos = [
            { x:  armLen * 0.707, y: -armLen * 0.707 },  // M1 front-right
            { x: -armLen * 0.707, y:  armLen * 0.707 },  // M2 rear-left
            { x: -armLen * 0.707, y: -armLen * 0.707 },  // M3 front-left
            { x:  armLen * 0.707, y:  armLen * 0.707 },  // M4 rear-right
        ];

        // Draw arms
        ctx.strokeStyle = '#4b5563';
        ctx.lineWidth = 6;
        ctx.lineCap = 'round';
        for (var i = 0; i < 4; i++) {
            ctx.beginPath();
            ctx.moveTo(0, 0);
            ctx.lineTo(motorPos[i].x, motorPos[i].y);
            ctx.stroke();
        }

        // Draw center body
        ctx.fillStyle = '#1f2937';
        ctx.strokeStyle = '#6366f1';
        ctx.lineWidth = 2;
        roundRect(ctx, -bodySize, -bodySize, bodySize * 2, bodySize * 2, 8);
        ctx.fill();
        ctx.stroke();

        // Front direction arrow
        ctx.fillStyle = '#ef4444';
        ctx.beginPath();
        ctx.moveTo(0, -bodySize - 12);
        ctx.lineTo(-8, -bodySize - 2);
        ctx.lineTo(8, -bodySize - 2);
        ctx.closePath();
        ctx.fill();

        // Draw motor circles with color based on output
        for (var m = 0; m < 4; m++) {
            var pct = display.motors[m];
            var color = motorColor(pct);

            // Outer ring
            ctx.beginPath();
            ctx.arc(motorPos[m].x, motorPos[m].y, motorRadius, 0, Math.PI * 2);
            ctx.fillStyle = '#111827';
            ctx.fill();
            ctx.strokeStyle = color;
            ctx.lineWidth = 3;
            ctx.stroke();

            // Inner fill proportional to output
            if (pct > 0) {
                ctx.beginPath();
                ctx.arc(motorPos[m].x, motorPos[m].y, motorRadius * 0.7 * (pct / 100), 0, Math.PI * 2);
                ctx.fillStyle = color;
                ctx.globalAlpha = 0.5;
                ctx.fill();
                ctx.globalAlpha = 1.0;
            }

            // Motor label
            ctx.fillStyle = '#e5e7eb';
            ctx.font = 'bold 11px sans-serif';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText('M' + (m + 1), motorPos[m].x, motorPos[m].y);
        }

        ctx.restore();
    }

    function roundRect(ctx, x, y, w, h, r) {
        ctx.beginPath();
        ctx.moveTo(x + r, y);
        ctx.lineTo(x + w - r, y);
        ctx.arcTo(x + w, y, x + w, y + r, r);
        ctx.lineTo(x + w, y + h - r);
        ctx.arcTo(x + w, y + h, x + w - r, y + h, r);
        ctx.lineTo(x + r, y + h);
        ctx.arcTo(x, y + h, x, y + h - r, r);
        ctx.lineTo(x, y + r);
        ctx.arcTo(x, y, x + r, y, r);
        ctx.closePath();
    }

    function updateDOM() {
        // Mode badge
        var modeName = COPTER_MODES[target.mode] || 'MODE ' + target.mode;
        var modeBadge = getEl('dvModeBadge');
        if (modeBadge) modeBadge.textContent = modeName;

        // Armed badge
        var armedBadge = getEl('dvArmedBadge');
        if (armedBadge) {
            armedBadge.textContent = target.armed ? 'ARMED' : 'DISARMED';
            armedBadge.className = 'drone-hud-badge drone-armed-badge ' + (target.armed ? 'armed' : 'disarmed');
        }

        // Heading
        var hdg = getEl('dvHeading');
        if (hdg) hdg.textContent = Math.round(display.heading);

        // Altitude
        var alt = getEl('dvAltitude');
        if (alt) alt.textContent = display.altitude.toFixed(1);

        // Climb
        var clm = getEl('dvClimb');
        if (clm) clm.textContent = display.climb.toFixed(1);

        // Attitude readout
        var r = getEl('dvRoll');
        if (r) r.textContent = display.roll.toFixed(1);
        var p = getEl('dvPitch');
        if (p) p.textContent = display.pitch.toFixed(1);
        var y = getEl('dvYaw');
        if (y) y.textContent = display.yaw.toFixed(1);

        // Yaw rotation on canvas wrapper (CSS transform for GPU acceleration)
        var wrapper = getEl('dvCanvasWrapper');
        if (wrapper) {
            wrapper.style.transform = 'rotate(' + display.yaw + 'deg)';
        }

        // Motor bars
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

    function animationLoop() {
        if (!isTabVisible()) {
            animFrameId = null;
            return;
        }

        var f = 0.15;  // lerp factor (~smooth at 60fps)

        display.roll = lerp(display.roll, target.roll, f);
        display.pitch = lerp(display.pitch, target.pitch, f);
        display.yaw = lerpAngle(display.yaw, target.yaw, f);
        display.altitude = lerp(display.altitude, target.altitude, f);
        display.climb = lerp(display.climb, target.climb, f);
        display.heading = lerpAngle(display.heading, target.heading, f);

        for (var i = 0; i < 4; i++) {
            display.motors[i] = lerp(display.motors[i], target.motors[i], f);
        }

        drawDrone();
        updateDOM();

        animFrameId = requestAnimationFrame(animationLoop);
    }

    function startAnimation() {
        if (!animFrameId) {
            animFrameId = requestAnimationFrame(animationLoop);
        }
    }

    function onSystemStatus(data) {
        // Update targets from system_status data
        if (data.attitude_roll !== undefined) target.roll = data.attitude_roll;
        if (data.attitude_pitch !== undefined) target.pitch = data.attitude_pitch;
        if (data.attitude_yaw !== undefined) target.yaw = data.attitude_yaw;
        if (data.altitude !== undefined) target.altitude = data.altitude;
        if (data.climb !== undefined) target.climb = data.climb;
        if (data.heading !== undefined) target.heading = data.heading;
        if (data.armed !== undefined) target.armed = data.armed;
        if (data.current_mode !== undefined) target.mode = data.current_mode;

        // Motor outputs from servo PWM values (1000-2000 -> 0-100%)
        if (data.servo_outputs && data.servo_outputs.length >= 4) {
            for (var i = 0; i < 4; i++) {
                var pwm = data.servo_outputs[i] || 1000;
                target.motors[i] = Math.max(0, Math.min(100, (pwm - 1000) / 10));
            }
        }

        // Start animation if tab is visible
        if (isTabVisible()) startAnimation();
    }

    // Initialize when DOM is ready
    function init() {
        canvas = document.getElementById('dvCanvas');
        if (!canvas) return;
        ctx = canvas.getContext('2d');

        // Register system status hook
        if (window._app && window._app.systemStatusHooks) {
            window._app.systemStatusHooks.push(onSystemStatus);
        }

        // Start animation when tab becomes visible
        // Listen for tab switches (menu-item clicks)
        var menuItems = document.querySelectorAll('.menu-item[data-tab]');
        menuItems.forEach(function(item) {
            item.addEventListener('click', function() {
                if (item.getAttribute('data-tab') === 'drone-view') {
                    setTimeout(startAnimation, 50);
                }
            });
        });
    }

    // Wait for DOM
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
