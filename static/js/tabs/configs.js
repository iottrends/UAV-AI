// ===== configs.js — Golden Config Snapshots =====

document.addEventListener('DOMContentLoaded', function() {
   function loadConfigList() {
      fetch('/api/configs')
         .then(function(r) { return r.json(); })
         .then(function(data) {
            var list = document.getElementById('configList');
            if (!list) return;
            if (data.status !== 'success' || !data.configs.length) {
               list.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:2rem;">No saved configs yet.</div>';
               return;
            }
            list.innerHTML = data.configs.map(function(c) {
               return '<div class="card" style="display:flex;justify-content:space-between;align-items:center;">' +
                  '<div>' +
                     '<div style="font-weight:bold;font-size:1rem;">' + c.name + '</div>' +
                     '<div style="font-size:0.8rem;color:var(--text-muted);">' + c.saved_at + ' &bull; ' + c.param_count + ' params</div>' +
                  '</div>' +
                  '<div style="display:flex;gap:0.5rem;">' +
                     '<button onclick="applyConfig(\'' + c.filename + '\')" style="background:var(--success-color);color:white;border:none;border-radius:5px;padding:0.4rem 0.8rem;cursor:pointer;font-size:0.8rem;">' +
                        '<i class="fas fa-upload"></i> Apply' +
                     '</button>' +
                     '<button onclick="deleteConfig(\'' + c.filename + '\')" style="background:var(--danger-color);color:white;border:none;border-radius:5px;padding:0.4rem 0.8rem;cursor:pointer;font-size:0.8rem;">' +
                        '<i class="fas fa-trash"></i>' +
                     '</button>' +
                  '</div>' +
               '</div>';
            }).join('');
         })
         .catch(function() {
            var list = document.getElementById('configList');
            if (list) list.innerHTML = '<div style="text-align:center;color:var(--danger-color);padding:2rem;">Failed to load configs.</div>';
         });
   }

   document.getElementById('saveConfigBtn')?.addEventListener('click', function() {
      var modal = document.getElementById('saveConfigModal');
      modal.style.display = 'flex';
      document.getElementById('configNameInput').value = '';
      document.getElementById('configNameInput').focus();
   });

   document.getElementById('cancelConfigBtn')?.addEventListener('click', function() {
      document.getElementById('saveConfigModal').style.display = 'none';
   });

   document.getElementById('confirmSaveConfigBtn')?.addEventListener('click', function() {
      var name = document.getElementById('configNameInput').value.trim();
      if (!name) { alert('Please enter a config name.'); return; }
      fetch('/api/configs', {
         method: 'POST',
         headers: {'Content-Type': 'application/json'},
         body: JSON.stringify({name: name})
      })
      .then(function(r) { return r.json(); })
      .then(function(data) {
         document.getElementById('saveConfigModal').style.display = 'none';
         if (data.status === 'success') {
            window._app.addMessage({text: '<strong>Config:</strong> ' + data.message, time: window._app.getCurrentTime()});
            loadConfigList();
         } else {
            alert(data.message || 'Failed to save config');
         }
      })
      .catch(function(e) { alert('Error saving config: ' + e.message); });
   });

   window.applyConfig = function(filename) {
      if (!confirm('Apply this config? Changed parameters will be written to the flight controller.')) return;
      var btn = event.target.closest('button');
      btn.disabled = true;
      btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Applying...';
      fetch('/api/configs/apply', {
         method: 'POST',
         headers: {'Content-Type': 'application/json'},
         body: JSON.stringify({filename: filename})
      })
      .then(function(r) { return r.json(); })
      .then(function(data) {
         btn.disabled = false;
         btn.innerHTML = '<i class="fas fa-upload"></i> Apply';
         window._app.addMessage({text: '<strong>Config:</strong> ' + data.message, time: window._app.getCurrentTime()});
         if (data.failed && data.failed.length) {
            window._app.addMessage({text: '<strong>Warning:</strong> Failed params: ' + data.failed.join(', '), time: window._app.getCurrentTime()});
         }
      })
      .catch(function(e) {
         btn.disabled = false;
         btn.innerHTML = '<i class="fas fa-upload"></i> Apply';
         alert('Error applying config: ' + e.message);
      });
   };

   window.deleteConfig = function(filename) {
      if (!confirm('Delete this config snapshot?')) return;
      fetch('/api/configs/' + filename, {method: 'DELETE'})
      .then(function(r) { return r.json(); })
      .then(function(data) {
         if (data.status === 'success') {
            loadConfigList();
         } else {
            alert(data.message || 'Failed to delete config');
         }
      })
      .catch(function(e) { alert('Error deleting config: ' + e.message); });
   };

   // Load configs when Configs tab is clicked
   document.querySelector('.menu-item[data-tab="configs"]')?.addEventListener('click', loadConfigList);
});
