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

      // PBR materials
      var bodyMat  = new THREE.MeshStandardMaterial({ color: 0x2d3748, metalness: 0.7, roughness: 0.35 });
      var armMat   = new THREE.MeshStandardMaterial({ color: 0x718096, metalness: 0.6, roughness: 0.45 });
      var motorMat = new THREE.MeshStandardMaterial({ color: 0x4a5568, metalness: 0.8, roughness: 0.2 });
      var propMat  = new THREE.MeshStandardMaterial({ color: 0x818cf8, metalness: 0.1, roughness: 0.8, transparent: true, opacity: 0.55, side: THREE.DoubleSide });
      var frontMat = new THREE.MeshStandardMaterial({ color: 0xef4444, metalness: 0.3, roughness: 0.5, emissive: 0x660000, emissiveIntensity: 0.4 });
      var legMat   = new THREE.MeshStandardMaterial({ color: 0x1a202c, metalness: 0.5, roughness: 0.6 });

      // Center body — slightly flatter top plate
      var bodyGeo = new THREE.BoxGeometry(1.1, 0.22, 1.1);
      var body = new THREE.Mesh(bodyGeo, bodyMat);
      quadGroup.add(body);

      // Top stack plate
      var topGeo = new THREE.BoxGeometry(0.7, 0.08, 0.7);
      var top = new THREE.Mesh(topGeo, armMat);
      top.position.y = 0.15;
      quadGroup.add(top);

      // Front indicator (red arrow on top)
      var frontGeo = new THREE.ConeGeometry(0.14, 0.42, 4);
      var front = new THREE.Mesh(frontGeo, frontMat);
      front.rotation.x = -Math.PI / 2;
      front.position.set(0, 0.18, -0.68);
      quadGroup.add(front);

      // X-frame arms — two diagonal flat boxes
      var armLength = 3.2;
      var armGeo = new THREE.BoxGeometry(armLength, 0.09, 0.12);

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

         // Motor bell
         var motorGeo = new THREE.CylinderGeometry(0.2, 0.24, 0.24, 16);
         var motor = new THREE.Mesh(motorGeo, motorMat);
         motor.position.set(mp.x, 0.12, mp.z);
         quadGroup.add(motor);

         // Prop — two crossed blades
         var bladeGeo = new THREE.BoxGeometry(1.2, 0.025, 0.18);
         var blade1 = new THREE.Mesh(bladeGeo, propMat);
         blade1.position.set(mp.x, 0.28, mp.z);
         quadGroup.add(blade1);
         var blade2 = new THREE.Mesh(bladeGeo, propMat);
         blade2.position.set(mp.x, 0.28, mp.z);
         blade2.rotation.y = Math.PI / 2;
         quadGroup.add(blade2);
         blade1.userData.blade2 = blade2;
         propellers.push(blade1);
      }

      // Landing gear — 4 skids
      var skidCrossGeo = new THREE.BoxGeometry(0.08, 0.08, 2.4);
      var skidFrontCross = new THREE.Mesh(skidCrossGeo, legMat);
      skidFrontCross.position.set(0.55, -0.42, 0);
      quadGroup.add(skidFrontCross);
      var skidRearCross = new THREE.Mesh(skidCrossGeo, legMat);
      skidRearCross.position.set(-0.55, -0.42, 0);
      quadGroup.add(skidRearCross);

      var legGeo = new THREE.BoxGeometry(0.07, 0.4, 0.07);
      [{ x:  0.55, z:  0.9 }, { x:  0.55, z: -0.9 },
       { x: -0.55, z:  0.9 }, { x: -0.55, z: -0.9 }].forEach(function(lp) {
         var leg = new THREE.Mesh(legGeo, legMat);
         leg.position.set(lp.x, -0.22, lp.z);
         quadGroup.add(leg);
      });

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
      renderer.outputEncoding = THREE.sRGBEncoding;
      renderer.toneMapping = 4; // ACESFilmic
      renderer.toneMappingExposure = 1.2;
      container.appendChild(renderer.domElement);

      // 3-point PBR lighting
      var keyLight = new THREE.DirectionalLight(0xfff5e0, 2.0);
      keyLight.position.set(5, 8, 5);
      scene.add(keyLight);
      var fillLight = new THREE.DirectionalLight(0xc0d8ff, 0.6);
      fillLight.position.set(-4, 3, -3);
      scene.add(fillLight);
      var rimLight = new THREE.DirectionalLight(0x8888ff, 0.4);
      rimLight.position.set(0, -2, -6);
      scene.add(rimLight);
      var hemi = new THREE.HemisphereLight(0x334466, 0x111122, 0.6);
      scene.add(hemi);

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

      // Spin propellers (blade1 + paired blade2)
      for (var i = 0; i < propellers.length; i++) {
         var delta = i % 2 === 0 ? 0.3 : -0.3;
         propellers[i].rotation.y += delta;
         if (propellers[i].userData.blade2) {
            propellers[i].userData.blade2.rotation.y += delta;
         }
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

   // ───── MAGFit Motor Interference Wizard ──────────────────────────────
   var MF = {
      armed:    false,
      data:     null,     // last API response
      axis:     'x',      // x | y | z
      chart:    null,
   };

   function magfitInit() {
      // Populate current COMPASS_MOT values from flatParams
      var p = window._app.flatParams || {};
      var elX = document.getElementById('mfCurX');
      var elY = document.getElementById('mfCurY');
      var elZ = document.getElementById('mfCurZ');
      if (elX) elX.textContent = p['COMPASS_MOT_X'] !== undefined ? p['COMPASS_MOT_X'] : '--';
      if (elY) elY.textContent = p['COMPASS_MOT_Y'] !== undefined ? p['COMPASS_MOT_Y'] : '--';
      if (elZ) elZ.textContent = p['COMPASS_MOT_Z'] !== undefined ? p['COMPASS_MOT_Z'] : '--';

      // Analyse button
      var runBtn = document.getElementById('magfitRunBtn');
      if (runBtn) {
         runBtn.addEventListener('click', magfitRun);
      }

      // Axis buttons
      document.querySelectorAll('.magfit-axis-btn').forEach(function(btn) {
         btn.addEventListener('click', function() {
            document.querySelectorAll('.magfit-axis-btn').forEach(function(b) { b.classList.remove('active'); });
            btn.classList.add('active');
            MF.axis = btn.dataset.mfaxis;
            if (MF.data) magfitDrawChart(MF.data);
         });
      });

      // Apply button
      var applyBtn = document.getElementById('magfitApplyBtn');
      if (applyBtn) {
         applyBtn.addEventListener('click', function() {
            if (MF.armed) {
               alert('Disarm the vehicle before writing compass parameters.');
               return;
            }
            if (!MF.data) return;
            var params = {
               COMPASS_MOT_X: MF.data.k_x,
               COMPASS_MOT_Y: MF.data.k_y,
               COMPASS_MOT_Z: MF.data.k_z,
            };
            if (!confirm(
               'Apply computed COMPASS_MOT values to the FC?\n\n' +
               'X = ' + MF.data.k_x + '\n' +
               'Y = ' + MF.data.k_y + '\n' +
               'Z = ' + MF.data.k_z
            )) return;

            fetch('/api/parameters', {
               method: 'POST',
               headers: { 'Content-Type': 'application/json' },
               body: JSON.stringify(params),
            })
            .then(function(r) { return r.json(); })
            .then(function(result) {
               if (result.status === 'success') {
                  // Refresh current param display
                  if (document.getElementById('mfCurX')) document.getElementById('mfCurX').textContent = MF.data.k_x;
                  if (document.getElementById('mfCurY')) document.getElementById('mfCurY').textContent = MF.data.k_y;
                  if (document.getElementById('mfCurZ')) document.getElementById('mfCurZ').textContent = MF.data.k_z;
                  alert('COMPASS_MOT parameters applied successfully.');
               } else {
                  alert('Failed: ' + (result.message || 'Unknown error'));
               }
            })
            .catch(function(e) { alert('Error: ' + e.message); });
         });
      }

      // Clear button
      var clearBtn = document.getElementById('magfitClearBtn');
      if (clearBtn) {
         clearBtn.addEventListener('click', function() {
            MF.data = null;
            document.getElementById('magfitResults').style.display = 'none';
            document.getElementById('magfitApplyBtn').style.display = 'none';
            document.getElementById('magfitClearBtn').style.display = 'none';
            document.getElementById('magfitRunStatus').textContent = '';
            document.getElementById('magfitSrcBadge').style.display = 'none';
            if (MF.chart) { MF.chart.destroy(); MF.chart = null; }
         });
      }
   }

   function magfitRun() {
      var runBtn    = document.getElementById('magfitRunBtn');
      var statusEl  = document.getElementById('magfitRunStatus');
      if (runBtn) {
         runBtn.disabled = true;
         runBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Analysing…';
      }
      if (statusEl) statusEl.textContent = '';

      fetch('/api/magfit')
         .then(function(r) { return r.json(); })
         .then(function(result) {
            if (runBtn) {
               runBtn.disabled = false;
               runBtn.innerHTML = '<i class="fas fa-chart-scatter"></i> Analyse Log';
            }
            if (result.status !== 'success') {
               if (statusEl) statusEl.textContent = result.message || 'Analysis failed.';
               return;
            }
            MF.data = result;
            magfitShowResults(result);
         })
         .catch(function(e) {
            if (runBtn) {
               runBtn.disabled = false;
               runBtn.innerHTML = '<i class="fas fa-chart-scatter"></i> Analyse Log';
            }
            if (statusEl) statusEl.textContent = 'Request error: ' + e.message;
         });
   }

   function magfitShowResults(d) {
      // Source badge
      var badge = document.getElementById('magfitSrcBadge');
      if (badge) {
         badge.textContent = d.use_current ? 'Current (A)' : 'Throttle';
         badge.style.display = 'inline';
      }

      // Ind label in chart header
      var lbl = document.getElementById('magfitIndLabel');
      if (lbl) lbl.textContent = d.ind_label;

      // Result grid
      var grid = document.getElementById('magfitResultGrid');
      if (grid) {
         var qClass = 'quality-' + d.quality;
         var r2Avg  = ((d.r2_x + d.r2_y + d.r2_z) / 3).toFixed(2);
         var qualEmoji = d.quality === 'good' ? '✓ Good' : (d.quality === 'fair' ? '~ Fair' : '✗ Poor');
         grid.innerHTML =
            magfitCell('COMPASS_MOT_X', d.k_x, 'R²=' + d.r2_x, qClass) +
            magfitCell('COMPASS_MOT_Y', d.k_y, 'R²=' + d.r2_y, qClass) +
            magfitCell('COMPASS_MOT_Z', d.k_z, 'R²=' + d.r2_z, qClass) +
            magfitCell('Fit Quality', qualEmoji, 'n=' + d.sample_count + '  R²avg=' + r2Avg, qClass);
      }

      // Show results panel and buttons
      document.getElementById('magfitResults').style.display = 'block';
      document.getElementById('magfitApplyBtn').style.display = 'inline-flex';
      document.getElementById('magfitClearBtn').style.display = 'inline-flex';
      magfitUpdateArmedState();

      // Draw chart
      magfitDrawChart(d);
   }

   function magfitCell(label, value, sub, qClass) {
      return '<div class="magfit-result-cell ' + qClass + '">' +
         '<span class="magfit-result-label">' + label + '</span>' +
         '<span class="magfit-result-value">' + value + '</span>' +
         '<span class="magfit-result-sub">' + sub + '</span>' +
         '</div>';
   }

   function magfitDrawChart(d) {
      if (MF.chart) { MF.chart.destroy(); MF.chart = null; }
      var canvas = document.getElementById('magfitCanvas');
      if (!canvas || !d.scatter) return;

      var ax     = MF.axis;           // 'x' | 'y' | 'z'
      var rawKey  = 'raw_'  + ax;
      var corrKey = 'corr_' + ax;
      var k       = d['k_' + ax];

      // Scatter datasets
      var rawPts  = d.scatter.map(function(s) { return { x: s.ind, y: s[rawKey]  }; });
      var corrPts = d.scatter.map(function(s) { return { x: s.ind, y: s[corrKey] }; });

      // Fitted line: y = k*x + c  — two points spanning ind range
      var indVals = d.scatter.map(function(s) { return s.ind; });
      var indMin  = Math.min.apply(null, indVals);
      var indMax  = Math.max.apply(null, indVals);
      // Recompute intercept from mean: c = mean(raw) - k * mean(ind)
      var meanInd = indVals.reduce(function(a, b) { return a + b; }, 0) / indVals.length;
      var meanRaw = d.scatter.map(function(s) { return s[rawKey]; }).reduce(function(a, b) { return a + b; }, 0) / d.scatter.length;
      var c_fit   = meanRaw - k * meanInd;
      var fitLine = [
         { x: indMin, y: k * indMin + c_fit },
         { x: indMax, y: k * indMax + c_fit },
      ];

      var indLabelStr = d.ind_label;

      MF.chart = new Chart(canvas.getContext('2d'), {
         type: 'scatter',
         data: {
            datasets: [
               {
                  label: 'Raw ΔMag ' + ax.toUpperCase(),
                  data: rawPts,
                  backgroundColor: 'rgba(231,76,60,0.35)',
                  pointRadius: 2,
                  pointHoverRadius: 4,
               },
               {
                  label: 'Corrected ΔMag ' + ax.toUpperCase(),
                  data: corrPts,
                  backgroundColor: 'rgba(46,204,113,0.45)',
                  pointRadius: 2,
                  pointHoverRadius: 4,
               },
               {
                  label: 'Fit (k=' + k.toFixed(2) + ')',
                  data: fitLine,
                  type: 'line',
                  borderColor: 'rgba(180,180,180,0.7)',
                  borderWidth: 1.5,
                  borderDash: [5, 4],
                  pointRadius: 0,
                  fill: false,
               },
            ],
         },
         options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
               legend: { labels: { color: '#ccc', font: { size: 11 }, boxWidth: 14 } },
               tooltip: {
                  callbacks: {
                     label: function(item) {
                        return item.dataset.label + ': (' +
                           item.parsed.x.toFixed(2) + ', ' +
                           item.parsed.y.toFixed(1) + ')';
                     }
                  }
               },
            },
            scales: {
               x: {
                  type: 'linear',
                  ticks: { color: '#888', font: { size: 10 } },
                  grid: { color: 'rgba(255,255,255,0.05)' },
                  title: { display: true, text: indLabelStr, color: '#888', font: { size: 11 } },
               },
               y: {
                  ticks: { color: '#888', font: { size: 10 } },
                  grid: { color: 'rgba(255,255,255,0.08)' },
                  title: { display: true, text: 'Earth-frame Mag ' + ax.toUpperCase() + ' (mGauss)', color: '#888', font: { size: 11 } },
               },
            },
         },
      });
   }

   function magfitUpdateArmedState() {
      var applyBtn  = document.getElementById('magfitApplyBtn');
      var armedWarn = document.getElementById('magfitArmedWarn');
      if (!applyBtn) return;
      if (MF.armed) {
         applyBtn.disabled = true;
         if (armedWarn) armedWarn.style.display = 'flex';
      } else {
         applyBtn.disabled = false;
         if (armedWarn) armedWarn.style.display = 'none';
      }
   }

   // ───── Init ─────
   function init() {
      initCalButtons();
      magfitInit();

      // Register system status hook for attitude data + armed state
      if (window._app && window._app.systemStatusHooks) {
         window._app.systemStatusHooks.push(function(data) {
            onSystemStatus(data);
            // Track armed state for MAGFit Apply guard
            if (data.armed !== undefined) {
               MF.armed = data.armed;
               if (MF.data) magfitUpdateArmedState();
            }
         });
      }

      // Listen for tab switches — lazy-init 3D on first calibration tab visit
      document.querySelectorAll('.menu-item[data-tab]').forEach(function(item) {
         item.addEventListener('click', function() {
            if (item.getAttribute('data-tab') === 'calibration') {
               onCalTabShown();
               // Refresh current COMPASS_MOT values each time tab is opened
               magfitInit();
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
