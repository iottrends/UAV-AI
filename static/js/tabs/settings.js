// ===== settings.js — Settings tab: API key management =====

(function() {
   'use strict';

   var providers = ['gemini', 'openai', 'claude'];

   function loadKeyStatus() {
      fetch('/api/settings/keys')
         .then(function(r) { return r.json(); })
         .then(function(data) {
            if (data.status !== 'success') return;
            providers.forEach(function(p) {
               var info = data.keys[p];
               var statusEl = document.querySelector('.api-key-status[data-provider="' + p + '"]');
               var previewEl = document.querySelector('.api-key-preview[data-provider="' + p + '"]');
               var deleteBtn = document.querySelector('.api-key-delete[data-provider="' + p + '"]');

               if (info && info.configured) {
                  if (statusEl) {
                     statusEl.textContent = 'Configured';
                     statusEl.style.background = '#d1fae5';
                     statusEl.style.color = '#065f46';
                  }
                  if (previewEl) {
                     previewEl.textContent = info.masked;
                     previewEl.style.display = 'block';
                  }
                  if (deleteBtn) deleteBtn.style.display = 'inline-block';
               } else {
                  if (statusEl) {
                     statusEl.textContent = 'Not configured';
                     statusEl.style.background = '#f0f0f0';
                     statusEl.style.color = 'var(--text-muted)';
                  }
                  if (previewEl) previewEl.style.display = 'none';
                  if (deleteBtn) deleteBtn.style.display = 'none';
               }
            });
         })
         .catch(function(e) {
            console.error('Failed to load key status:', e);
         });
   }

   function init() {
      // Save buttons
      document.querySelectorAll('.api-key-save').forEach(function(btn) {
         btn.addEventListener('click', function() {
            var provider = btn.getAttribute('data-provider');
            var input = document.querySelector('.api-key-input[data-provider="' + provider + '"]');
            var key = input ? input.value.trim() : '';
            if (!key) return;

            if (window._app && window._app.socket) {
               window._app.socket.emit('set_api_key', { provider: provider, key: key });
               input.value = '';
               btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
               btn.disabled = true;
            }
         });
      });

      // Delete buttons
      document.querySelectorAll('.api-key-delete').forEach(function(btn) {
         btn.addEventListener('click', function() {
            var provider = btn.getAttribute('data-provider');
            btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
            btn.disabled = true;

            fetch('/api/settings/keys', {
               method: 'DELETE',
               headers: { 'Content-Type': 'application/json' },
               body: JSON.stringify({ provider: provider })
            })
            .then(function(r) { return r.json(); })
            .then(function(data) {
               btn.innerHTML = '<i class="fas fa-trash"></i>';
               btn.disabled = false;
               if (data.status === 'success') {
                  loadKeyStatus();
               }
            })
            .catch(function() {
               btn.innerHTML = '<i class="fas fa-trash"></i>';
               btn.disabled = false;
            });
         });
      });

      // Listen for api_key_result from socket (save confirmation)
      if (window._app && window._app.socket) {
         window._app.socket.on('api_key_result', function(data) {
            // Re-enable all save buttons
            document.querySelectorAll('.api-key-save').forEach(function(b) {
               b.innerHTML = '<i class="fas fa-save"></i> Save';
               b.disabled = false;
            });
            if (data.success) {
               loadKeyStatus();
            }
         });
      }

      // Load status when settings tab becomes visible
      document.querySelectorAll('.menu-item[data-tab]').forEach(function(item) {
         item.addEventListener('click', function() {
            if (item.getAttribute('data-tab') === 'settings') {
               loadKeyStatus();
            }
         });
      });

      // Initial load
      loadKeyStatus();
   }

   if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', init);
   } else {
      init();
   }
})();
