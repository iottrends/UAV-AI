// ===== calibration.js — Field Calibration handlers =====

document.addEventListener('DOMContentLoaded', function() {
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
});
