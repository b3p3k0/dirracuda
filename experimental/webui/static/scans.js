var token = document.querySelector('meta[name="csrf-token"]').content;
var trackedTasks = {};
var pollTimer = null;
var TERMINAL = {done: 1, failed: 1, cancelled: 1};

function parseCountries(value) {
  if (!value) return [];
  return value.split(',').map(function(s) { return s.trim().toUpperCase(); }).filter(Boolean);
}

function parseMaxResults(value) {
  var raw = String(value || '').trim();
  if (!raw) return null;
  if (!/^\d+$/.test(raw)) return null;
  var parsed = Number(raw);
  if (!Number.isInteger(parsed)) return null;
  if (parsed < 1 || parsed > 100000) return null;
  return parsed;
}

function setStatus(msg, cls) {
  var el = document.getElementById('submit-status');
  el.textContent = msg;
  el.className = cls ? cls : '';
}

function applyScansPrefsFromStorage() {
  if (!window.DirracudaPrefs || !window.DirracudaPrefs.isAvailable()) return;
  var saved = window.DirracudaPrefs.readSection('scans');
  document.getElementById('proto-smb').checked = !!saved.proto_smb;
  document.getElementById('proto-ftp').checked = !!saved.proto_ftp;
  document.getElementById('proto-http').checked = !!saved.proto_http;
  document.getElementById('probe').checked = !!saved.probe;
  document.getElementById('rescan-all').checked = !!saved.rescan_all;
  document.getElementById('rescan-failed').checked = !!saved.rescan_failed;
  if (Number.isInteger(saved.max_results) && saved.max_results >= 1 && saved.max_results <= 100000) {
    document.getElementById('max-results').value = String(saved.max_results);
  }
}

function persistScansPrefs() {
  if (!window.DirracudaPrefs || !window.DirracudaPrefs.isAvailable()) return;
  var maxResults = parseMaxResults(document.getElementById('max-results').value);
  window.DirracudaPrefs.writeSection('scans', {
    proto_smb: document.getElementById('proto-smb').checked,
    proto_ftp: document.getElementById('proto-ftp').checked,
    proto_http: document.getElementById('proto-http').checked,
    max_results: maxResults === null ? 100 : maxResults,
    probe: document.getElementById('probe').checked,
    rescan_all: document.getElementById('rescan-all').checked,
    rescan_failed: document.getElementById('rescan-failed').checked
  });
}

function upsertRow(task) {
  var tbody = document.getElementById('queue-body');
  var existing = trackedTasks[task.task_id];
  if (!existing) {
    var placeholder = tbody.querySelector('[colspan]');
    if (placeholder) placeholder.parentNode.remove();
    var tr = document.createElement('tr');
    tr.dataset.taskId = task.task_id;
    tr.innerHTML =
      '<td class="cell-id"></td>' +
      '<td class="cell-proto"></td>' +
      '<td class="cell-state"></td>' +
      '<td class="cell-progress"></td>' +
      '<td class="cell-action"></td>';
    var cancelBtn = document.createElement('button');
    cancelBtn.textContent = 'Cancel';
    cancelBtn.type = 'button';
    cancelBtn.addEventListener('click', function() { cancelTask(task.task_id, tr); });
    tr.querySelector('.cell-action').appendChild(cancelBtn);
    tbody.appendChild(tr);
    trackedTasks[task.task_id] = tr;
    existing = tr;
  }
  existing.querySelector('.cell-id').textContent = task.task_id.slice(0, 8);
  existing.querySelector('.cell-proto').textContent = task.protocol;
  existing.querySelector('.cell-state').textContent = task.status;
  existing.querySelector('.cell-progress').textContent = task.progress_message || '';
  if (TERMINAL[task.status]) {
    var btn = existing.querySelector('.cell-action button');
    if (btn) btn.disabled = true;
  }
}

function cancelTask(taskId, tr) {
  fetch('/api/scans/' + encodeURIComponent(taskId) + '/cancel', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token},
    body: '{}'
  }).then(function(r) { return r.json(); }).then(function(data) {
    if (!data.ok && data.status) {
      tr.querySelector('.cell-state').textContent = data.status;
    }
  }).catch(function() {});
}

function pollAll() {
  var ids = Object.keys(trackedTasks);
  if (!ids.length) return;
  var active = ids.filter(function(id) {
    var tr = trackedTasks[id];
    var state = tr.querySelector('.cell-state').textContent;
    return !TERMINAL[state];
  });
  if (!active.length) return;
  active.forEach(function(id) {
    fetch('/api/scans/' + encodeURIComponent(id))
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(data) { if (data) upsertRow(data); })
      .catch(function() {});
  });
  pollTimer = setTimeout(pollAll, 3000);
}

function startPolling() {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = setTimeout(pollAll, 3000);
}

document.getElementById('proto-smb').addEventListener('change', persistScansPrefs);
document.getElementById('proto-ftp').addEventListener('change', persistScansPrefs);
document.getElementById('proto-http').addEventListener('change', persistScansPrefs);
document.getElementById('max-results').addEventListener('change', persistScansPrefs);
document.getElementById('probe').addEventListener('change', persistScansPrefs);
document.getElementById('rescan-all').addEventListener('change', persistScansPrefs);
document.getElementById('rescan-failed').addEventListener('change', persistScansPrefs);

document.getElementById('scan-form').addEventListener('submit', async function(e) {
  e.preventDefault();
  var btn = document.getElementById('queue-btn');
  btn.disabled = true;
  setStatus('');

  var protos = [];
  if (document.getElementById('proto-smb').checked) protos.push('smb');
  if (document.getElementById('proto-ftp').checked) protos.push('ftp');
  if (document.getElementById('proto-http').checked) protos.push('http');

  if (!protos.length) {
    setStatus('Select at least one protocol.', 'status-warn');
    btn.disabled = false;
    return;
  }

  var countries = parseCountries(document.getElementById('countries').value);
  var probeChecked = document.getElementById('probe').checked;
  var filters = document.getElementById('filters').value;
  var maxResults = parseMaxResults(document.getElementById('max-results').value);
  var queued = 0;
  var errs = [];

  if (maxResults === null) {
    setStatus('Max results must be an integer between 1 and 100000.', 'status-warn');
    btn.disabled = false;
    return;
  }

  for (var i = 0; i < protos.length; i++) {
    var proto = protos[i];
    var payload = {
      protocol: proto,
      countries: countries,
      max_shodan_results: maxResults,
      run_probe_after_scan: probeChecked,
      filters: filters
    };
    try {
      var resp = await fetch('/api/scans', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token},
        body: JSON.stringify(payload)
      });
      var data = await resp.json();
      if (resp.status === 202) {
        upsertRow(data);
        queued++;
      } else {
        var msg = (data && data.detail) ? JSON.stringify(data.detail) : (data && data.error) || 'Request failed.';
        errs.push(proto.toUpperCase() + ': ' + msg);
      }
    } catch (ex) {
      errs.push(proto.toUpperCase() + ': network error');
    }
  }

  if (queued) startPolling();

  if (errs.length) {
    setStatus(errs.join('; '), 'status-error');
  } else if (queued) {
    setStatus('Queued ' + queued + ' task(s).', 'status-ok');
  }
  btn.disabled = false;
});

applyScansPrefsFromStorage();
