// ===== calibration.js — Field Calibration handlers + 3D Quad Model =====

(function() {
   'use strict';

   // ───── Calibration button handlers ─────
   function initCalButtons() {
      document.querySelectorAll('.cal-btn').forEach(function(btn) {
         btn.addEventListener('click', function() {
            var calType = this.getAttribute('data-cal');
            var statusEl = document.querySelector('.cal-status[data-cal="' + calType + '"]');
            var self = this;
            self.disabled = true;
            self.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Calibrating...';
            statusEl.textContent = 'Calibrating...';
            statusEl.style.color = 'var(--primary-color)';

            fetch('/api/calibrate', {
               method: 'POST',
               headers: {'Content-Type': 'application/json'},
               body: JSON.stringify({type: calType})
            })
            .then(function(r) { return r.json(); })
            .then(function(data) {
               self.disabled = false;
               self.innerHTML = '<i class="fas fa-play"></i> Start ' + calType.charAt(0).toUpperCase() + calType.slice(1) + ' Cal';
               if (data.status === 'success') {
                  statusEl.textContent = 'Success';
                  statusEl.style.color = 'var(--success-color)';
               } else {
                  statusEl.textContent = data.message || 'Failed';
                  statusEl.style.color = 'var(--danger-color)';
               }
            })
            .catch(function(e) {
               self.disabled = false;
               self.innerHTML = '<i class="fas fa-play"></i> Start ' + calType.charAt(0).toUpperCase() + calType.slice(1) + ' Cal';
               statusEl.textContent = 'Error: ' + e.message;
               statusEl.style.color = 'var(--danger-color)';
            });
         });
      });
   }

   // ───── 3D Quad Model (Three.js) ─────
   var scene, camera, renderer, quadGroup;
   var propellers = [];
   var animFrameId = null;
   var threeInitialized = false;

   // Attitude targets and displayed (lerped) values
   var target3d = { roll: 0, pitch: 0, yaw: 0 };
   var display3d = { roll: 0, pitch: 0, yaw: 0 };

   function lerp(a, b, t) { return a + (b - a) * t; }
   function lerpAngle(a, b, t) {
      var diff = b - a;
      while (diff > Math.PI) diff -= Math.PI * 2;
      while (diff < -Math.PI) diff += Math.PI * 2;
      return a + diff * t;
   }

   function isCalTabVisible() {
      var tab = document.getElementById('calibration-tab');
      return tab && tab.style.display !== 'none';
   }

   function buildQuadModel() {
      quadGroup = new THREE.Group();

      // Materials — brighter colors so they're visible against dark bg
      var bodyMat = new THREE.MeshPhongMaterial({ color: 0x4a5568, shininess: 80 });
      var armMat = new THREE.MeshPhongMaterial({ color: 0x8899aa, shininess: 60 });
      var motorMat = new THREE.MeshPhongMaterial({ color: 0x667788, shininess: 40 });
      var propMat = new THREE.MeshPhongMaterial({ color: 0x818cf8, transparent: true, opacity: 0.45, side: THREE.DoubleSide });
      var frontMat = new THREE.MeshPhongMaterial({ color: 0xef4444, emissive: 0x551111 });

      // Center body
      var bodyGeo = new THREE.BoxGeometry(1.0, 0.3, 1.0);
      var body = new THREE.Mesh(bodyGeo, bodyMat);
      quadGroup.add(body);

      // Front indicator (red arrow on top)
      var frontGeo = new THREE.ConeGeometry(0.18, 0.5, 4);
      var front = new THREE.Mesh(frontGeo, frontMat);
      front.rotation.x = -Math.PI / 2;
      front.position.set(0, 0.2, -0.7);
      quadGroup.add(front);

      // X-frame arms — two diagonal flat boxes
      var armLength = 3.2;
      var armGeo = new THREE.BoxGeometry(armLength, 0.1, 0.14);

      var arm1 = new THREE.Mesh(armGeo, armMat);
      arm1.rotation.y = Math.PI / 4;
      quadGroup.add(arm1);

      var arm2 = new THREE.Mesh(armGeo, armMat);
      arm2.rotation.y = -Math.PI / 4;
      quadGroup.add(arm2);

      // Motor positions (X-frame corners)
      var d = armLength / 2 * 0.707;
      var motorPositions = [
         { x:  d, z: -d },  // front-right
         { x: -d, z: -d },  // front-left
         { x: -d, z:  d },  // rear-left
         { x:  d, z:  d },  // rear-right
      ];

      for (var i = 0; i < 4; i++) {
         var mp = motorPositions[i];

         // Motor housing
         var motorGeo = new THREE.CylinderGeometry(0.22, 0.28, 0.28, 12);
         var motor = new THREE.Mesh(motorGeo, motorMat);
         motor.position.set(mp.x, 0.15, mp.z);
         quadGroup.add(motor);

         // Propeller disc
         var propGeo = new THREE.CylinderGeometry(0.6, 0.6, 0.03, 20);
         var prop = new THREE.Mesh(propGeo, propMat);
         prop.position.set(mp.x, 0.32, mp.z);
         quadGroup.add(prop);
         propellers.push(prop);
      }

      return quadGroup;
   }

   // Lazy init — only called when calibration tab is first shown
   function init3D() {
      if (threeInitialized) return;
      if (typeof THREE === 'undefined') {
         console.warn('Three.js not loaded, skipping 3D quad');
         return;
      }

      var container = document.getElementById('cal3dContainer');
      if (!container) return;

      // Make sure container has real dimensions (tab must be visible)
      var w = container.clientWidth;
      var h = container.clientHeight;
      if (w < 10 || h < 10) {
         console.warn('cal3dContainer has no dimensions yet, deferring init3D');
         return;
      }

      threeInitialized = true;

      // Scene
      scene = new THREE.Scene();
      scene.background = new THREE.Color(0x0d0d1a);

      // Camera — 3/4 view from above-front
      camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 100);
      camera.position.set(3.5, 3.5, 4.5);
      camera.lookAt(0, 0, 0);

      // Renderer
      renderer = new THREE.WebGLRenderer({ antialias: true });
      renderer.setSize(w, h);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      container.appendChild(renderer.domElement);

      // Lighting — strong enough to see the model
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

      // Build and add quad
      var quad = buildQuadModel();
      scene.add(quad);

      // Handle window resize
      window.addEventListener('resize', function() {
         if (!renderer || !isCalTabVisible()) return;
         var rw = container.clientWidth;
         var rh = container.clientHeight;
         if (rw < 10 || rh < 10) return;
         camera.aspect = rw / rh;
         camera.updateProjectionMatrix();
         renderer.setSize(rw, rh);
      });

      // Initial render
      renderer.render(scene, camera);
      console.log('3D quad model initialized:', w + 'x' + h);
   }

   function resizeRenderer() {
      if (!renderer) return;
      var container = document.getElementById('cal3dContainer');
      if (!container) return;
      var w = container.clientWidth;
      var h = container.clientHeight;
      if (w < 10 || h < 10) return;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
   }

   function animate3D() {
      if (!isCalTabVisible()) {
         animFrameId = null;
         return;
      }

      animFrameId = requestAnimationFrame(animate3D);

      var f = 0.12;

      display3d.roll = lerp(display3d.roll, target3d.roll, f);
      display3d.pitch = lerp(display3d.pitch, target3d.pitch, f);
      display3d.yaw = lerpAngle(display3d.yaw, target3d.yaw, f);

      if (quadGroup) {
         quadGroup.rotation.set(0, 0, 0);
         quadGroup.rotation.order = 'YXZ';
         quadGroup.rotation.y = -display3d.yaw;
         quadGroup.rotation.x = display3d.pitch;
         quadGroup.rotation.z = -display3d.roll;
      }

      // Spin propellers
      for (var i = 0; i < propellers.length; i++) {
         propellers[i].rotation.y += (i % 2 === 0 ? 0.3 : -0.3);
      }

      // Update attitude readout
      var rollDeg = (display3d.roll * 180 / Math.PI).toFixed(1);
      var pitchDeg = (display3d.pitch * 180 / Math.PI).toFixed(1);
      var yawDeg = (display3d.yaw * 180 / Math.PI).toFixed(1);
      var rollEl = document.getElementById('cal3dRoll');
      var pitchEl = document.getElementById('cal3dPitch');
      var yawEl = document.getElementById('cal3dYaw');
      if (rollEl) rollEl.textContent = rollDeg;
      if (pitchEl) pitchEl.textContent = pitchDeg;
      if (yawEl) yawEl.textContent = yawDeg;

      renderer.render(scene, camera);
   }

   function startAnimation() {
      if (!animFrameId && renderer) {
         animFrameId = requestAnimationFrame(animate3D);
      }
   }

   function onSystemStatus(data) {
      if (data.attitude_roll !== undefined) target3d.roll = data.attitude_roll * Math.PI / 180;
      if (data.attitude_pitch !== undefined) target3d.pitch = data.attitude_pitch * Math.PI / 180;
      if (data.attitude_yaw !== undefined) target3d.yaw = data.attitude_yaw * Math.PI / 180;

      if (isCalTabVisible()) startAnimation();
   }

   // Called when user clicks the calibration tab
   function onCalTabShown() {
      if (!threeInitialized) {
         // Defer slightly so the tab's display:block has taken effect
         setTimeout(function() {
            init3D();
            startAnimation();
         }, 100);
      } else {
         resizeRenderer();
         setTimeout(startAnimation, 50);
      }
   }

   // ───── Init ─────
   function init() {
      initCalButtons();

      // Register system status hook for attitude data
      if (window._app && window._app.systemStatusHooks) {
         window._app.systemStatusHooks.push(onSystemStatus);
      }

      // Listen for tab switches — lazy-init 3D on first calibration tab visit
      document.querySelectorAll('.menu-item[data-tab]').forEach(function(item) {
         item.addEventListener('click', function() {
            if (item.getAttribute('data-tab') === 'calibration') {
               onCalTabShown();
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
