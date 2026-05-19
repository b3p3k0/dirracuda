(function () {
  'use strict';

  var form = document.getElementById('change-pw-form');
  var btn = document.getElementById('change-pw-btn');
  var statusEl = document.getElementById('account-status');

  function setStatus(msg, cls) {
    statusEl.textContent = msg;
    statusEl.className = cls || '';
    statusEl.style.display = msg ? '' : 'none';
  }

  if (!form) return;

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    var token = document.querySelector('meta[name="csrf-token"]').content;
    var currentPw = document.getElementById('current-pw').value;
    var newPw = document.getElementById('new-pw').value;

    if (!currentPw || !newPw) {
      setStatus('Both fields are required.', 'status-error');
      return;
    }

    btn.disabled = true;
    setStatus('Saving…', 'status-info');

    try {
      var resp = await fetch('/api/auth/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token },
        body: JSON.stringify({ current_password: currentPw, new_password: newPw })
      });
      var data = await resp.json();
      if (resp.ok) {
        setStatus('Password changed.', 'status-ok');
        form.reset();
      } else {
        setStatus('Error: ' + (data.error || resp.status), 'status-error');
      }
    } catch (_) {
      setStatus('Network error. Please try again.', 'status-error');
    }

    btn.disabled = false;
  });
})();
