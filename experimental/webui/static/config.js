var token = document.querySelector('meta[name="csrf-token"]').content;

document.getElementById('remote-enabled').addEventListener('change', function() {
  document.getElementById('remote-warn').classList.toggle('hidden', !this.checked);
});

function parseCidrs(value) {
  if (!value || !value.trim()) return [];
  return value.split(',').map(function(s) { return s.trim(); }).filter(Boolean);
}

function setStatus(msg, cls) {
  var el = document.getElementById('cfg-status');
  el.textContent = msg;
  el.className = cls || '';
  el.style.display = msg ? '' : 'none';
}

function setPrefStatus(msg, cls) {
  var el = document.getElementById('prefs-storage-status');
  el.textContent = msg;
  el.className = cls || '';
  el.style.display = msg ? '' : 'none';
}

function refreshPrefStatusSummary() {
  if (!window.DirracudaPrefs || !window.DirracudaPrefs.isAvailable()) {
    setPrefStatus('Preference storage unavailable in this browser context.', 'status-warn');
    return;
  }
  var consent = window.DirracudaPrefs.getConsent();
  if (consent === 'allow') {
    setPrefStatus('Preference storage is enabled in this browser.', 'status-ok');
    return;
  }
  if (consent === 'deny') {
    setPrefStatus('Preference storage is disabled in this browser.', 'status-neutral');
    return;
  }
  setPrefStatus('Preference storage has not been decided yet.', 'status-info');
}

document.getElementById('prefs-enable-btn').addEventListener('click', function() {
  if (!window.DirracudaPrefs || !window.DirracudaPrefs.isAvailable()) {
    setPrefStatus('Preference storage unavailable in this browser context.', 'status-warn');
    return;
  }
  if (window.DirracudaPrefs.setConsent('allow')) {
    setPrefStatus('Preference storage enabled in this browser.', 'status-ok');
  } else {
    setPrefStatus('Failed to update browser preference storage setting.', 'status-error');
  }
});

document.getElementById('prefs-disable-btn').addEventListener('click', function() {
  if (!window.DirracudaPrefs || !window.DirracudaPrefs.isAvailable()) {
    setPrefStatus('Preference storage unavailable in this browser context.', 'status-warn');
    return;
  }
  if (window.DirracudaPrefs.setConsent('deny')) {
    setPrefStatus('Preference storage disabled in this browser.', 'status-neutral');
  } else {
    setPrefStatus('Failed to update browser preference storage setting.', 'status-error');
  }
});

document.getElementById('prefs-clear-btn').addEventListener('click', function() {
  if (!window.DirracudaPrefs || !window.DirracudaPrefs.isAvailable()) {
    setPrefStatus('Preference storage unavailable in this browser context.', 'status-warn');
    return;
  }
  if (window.DirracudaPrefs.clearData()) {
    setPrefStatus('Saved browser preferences cleared.', 'status-ok');
  } else {
    setPrefStatus('Failed to clear saved browser preferences.', 'status-error');
  }
});

document.getElementById('cfg-form').addEventListener('submit', async function(e) {
  e.preventDefault();
  var btn = document.getElementById('save-btn');
  btn.disabled = true;
  setStatus('Saving...');

  var payload = {
    bind_address: document.getElementById('bind-addr').value.trim(),
    port: parseInt(document.getElementById('port').value, 10),
    remote_enabled: document.getElementById('remote-enabled').checked,
    tls_enabled: document.getElementById('tls-enabled').checked,
    tls_allow_insecure_remote: document.getElementById('tls-insecure').checked,
    tls_cert: document.getElementById('tls-cert').value.trim(),
    tls_key: document.getElementById('tls-key').value.trim(),
    allowed_cidrs: parseCidrs(document.getElementById('allowlist').value),
    session_timeout_idle_min: parseInt(document.getElementById('idle-timeout').value, 10),
    session_timeout_absolute_hr: parseInt(document.getElementById('abs-timeout').value, 10),
    auth_lockout_threshold: parseInt(document.getElementById('lockout-threshold').value, 10),
    auth_lockout_window_sec: parseInt(document.getElementById('lockout-window').value, 10),
    auth_lockout_base_duration_sec: parseInt(document.getElementById('lockout-base').value, 10),
    auth_lockout_max_duration_sec: parseInt(document.getElementById('lockout-max').value, 10)
  };

  try {
    var resp = await fetch('/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token},
      body: JSON.stringify(payload)
    });
    var data = await resp.json();
    if (resp.ok) {
      setStatus('Saved. ' + (data.note || 'Changes take effect on restart.'), 'status-ok');
    } else {
      setStatus('Error: ' + (data.error || resp.status), 'status-error');
    }
  } catch (ex) {
    setStatus('Network error. Please try again.', 'status-error');
  }
  btn.disabled = false;
});

refreshPrefStatusSummary();
