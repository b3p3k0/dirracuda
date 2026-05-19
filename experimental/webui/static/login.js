document.getElementById('lf').addEventListener('submit', async function(e) {
  e.preventDefault();
  var err = document.getElementById('err');
  function hideError() {
    err.hidden = true;
    err.classList.add('hidden');
  }
  function showError(message) {
    err.textContent = message;
    err.hidden = false;
    err.classList.remove('hidden');
  }

  hideError();
  var btn = this.querySelector('button[type="submit"]');
  btn.disabled = true;
  try {
    var r = await fetch('/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        username: document.getElementById('u').value,
        password: document.getElementById('p').value
      })
    });
    if (r.ok) {
      window.location.href = '/dashboard';
    } else {
      var b = await r.json().catch(function() { return {}; });
      showError(b.error || 'Login failed.');
      btn.disabled = false;
    }
  } catch (ex) {
    showError('Network error. Please try again.');
    btn.disabled = false;
  }
});
