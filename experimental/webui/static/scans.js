var token = document.querySelector('meta[name="csrf-token"]').content;
var trackedTasks = {};
var pollTimer = null;
var pendingPreflightPlan = null;
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

function escHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function setStatus(msg, cls) {
  var el = document.getElementById('submit-status');
  el.textContent = msg;
  el.className = cls ? cls : '';
}

function _preflightPanel() {
  return document.getElementById('preflight-panel');
}

function _preflightSummary() {
  return document.getElementById('preflight-summary');
}

function _preflightStartBtn() {
  return document.getElementById('preflight-start-btn');
}

function _preflightCancelBtn() {
  return document.getElementById('preflight-cancel-btn');
}

function _renderPreflightSummary(preflight, plan) {
  var summary = _preflightSummary();
  if (!summary) return;

  var protocols = (plan.protos || []).map(function(proto) { return proto.toUpperCase(); }).join(', ');
  var lines = [];
  lines.push('<strong>Selected protocols:</strong> ' + escHtml(protocols || '(none)'));
  lines.push('<strong>Max results:</strong> ' + escHtml(String(plan.maxResults)));

  var total = Number(preflight.estimated_query_credits_total || 0);
  lines.push('<strong>Estimated total query credits:</strong> ~' + escHtml(String(total)));

  var byProto = preflight.estimated_query_credits_by_protocol || {};
  var protoLines = [];
  Object.keys(byProto).forEach(function(proto) {
    protoLines.push(proto.toUpperCase() + ': ~' + String(byProto[proto]));
  });
  if (protoLines.length) {
    lines.push('<strong>By protocol:</strong> ' + escHtml(protoLines.join(' | ')));
  }

  var balance = preflight.balance || {};
  if (balance.state === 'ok') {
    lines.push('<strong>Live balance:</strong> ' + escHtml(String(balance.query_credits)) + ' credits');
    if (preflight.estimated_post_scan_balance != null) {
      lines.push(
        '<strong>Estimated post-scan balance:</strong> ' +
          escHtml(String(preflight.estimated_post_scan_balance)) +
          ' credits'
      );
    }
  } else if (balance.state === 'no_key') {
    lines.push('<strong>Live balance:</strong> not available (Shodan API key not configured)');
    lines.push(
      '<a href="' + escHtml(preflight.dashboard_url || 'https://developer.shodan.io/dashboard') +
      '" target="_blank" rel="noopener noreferrer">Check balance on Shodan dashboard</a>'
    );
  } else {
    lines.push('<strong>Live balance:</strong> unavailable (' + escHtml(String(balance.reason || 'unknown')) + ')');
    lines.push(
      '<a href="' + escHtml(preflight.dashboard_url || 'https://developer.shodan.io/dashboard') +
      '" target="_blank" rel="noopener noreferrer">Check balance on Shodan dashboard</a>'
    );
  }

  summary.innerHTML = lines.join('<br>');
}

function _showPreflightPanel(preflight, plan) {
  var panel = _preflightPanel();
  var startBtn = _preflightStartBtn();
  var cancelBtn = _preflightCancelBtn();
  if (!panel || !startBtn || !cancelBtn) return;

  pendingPreflightPlan = plan;
  _renderPreflightSummary(preflight, plan);
  panel.classList.remove('hidden');
  startBtn.disabled = false;
  cancelBtn.disabled = false;
}

function _resetPreflightPanel() {
  var panel = _preflightPanel();
  var startBtn = _preflightStartBtn();
  var cancelBtn = _preflightCancelBtn();
  var summary = _preflightSummary();

  pendingPreflightPlan = null;
  if (panel) panel.classList.add('hidden');
  if (startBtn) startBtn.disabled = true;
  if (cancelBtn) cancelBtn.disabled = true;
  if (summary) summary.textContent = 'Run preflight to see cost and balance estimates.';
}

function _invalidatePendingPreflight() {
  if (!pendingPreflightPlan) return;
  _resetPreflightPanel();
  setStatus('Scan options changed. Run preflight again before starting scan.', 'status-neutral');
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

function _tasksFromQueueSnapshot(snapshot) {
  var tasks = [];
  if (!snapshot || typeof snapshot !== 'object') return tasks;
  if (snapshot.active && snapshot.active.task_id) {
    tasks.push(snapshot.active);
  }
  if (Array.isArray(snapshot.queued)) {
    snapshot.queued.forEach(function(task) {
      if (task && task.task_id) tasks.push(task);
    });
  }
  return tasks;
}

async function hydrateQueueFromServer() {
  try {
    var resp = await fetch('/api/scans');
    if (!resp.ok) return;
    var payload = await resp.json();
    var tasks = _tasksFromQueueSnapshot(payload);
    if (!tasks.length) return;
    tasks.forEach(function(task) { upsertRow(task); });
    startPolling();
  } catch (_err) {}
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

function _collectScanPlan() {
  var protos = [];
  if (document.getElementById('proto-smb').checked) protos.push('smb');
  if (document.getElementById('proto-ftp').checked) protos.push('ftp');
  if (document.getElementById('proto-http').checked) protos.push('http');
  if (!protos.length) {
    return {error: 'Select at least one protocol.'};
  }

  var maxResults = parseMaxResults(document.getElementById('max-results').value);
  if (maxResults === null) {
    return {error: 'Max results must be an integer between 1 and 100000.'};
  }

  return {
    protos: protos,
    countries: parseCountries(document.getElementById('countries').value),
    probeChecked: document.getElementById('probe').checked,
    filters: document.getElementById('filters').value,
    maxResults: maxResults
  };
}

async function _requestPreflight(plan) {
  var resp = await fetch('/api/scans/preflight', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token},
    body: JSON.stringify({
      protocols: plan.protos,
      max_shodan_results: plan.maxResults
    })
  });

  var data = {};
  try {
    data = await resp.json();
  } catch (_err) {
    data = {};
  }
  return {ok: resp.ok, status: resp.status, data: data};
}

async function _queueTasksForPlan(plan) {
  var queued = 0;
  var errs = [];

  for (var i = 0; i < plan.protos.length; i++) {
    var proto = plan.protos[i];
    var payload = {
      protocol: proto,
      countries: plan.countries,
      max_shodan_results: plan.maxResults,
      run_probe_after_scan: plan.probeChecked,
      filters: plan.filters
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
    } catch (_ex) {
      errs.push(proto.toUpperCase() + ': network error');
    }
  }

  return {queued: queued, errs: errs};
}

document.getElementById('proto-smb').addEventListener('change', function() {
  persistScansPrefs();
  _invalidatePendingPreflight();
});
document.getElementById('proto-ftp').addEventListener('change', function() {
  persistScansPrefs();
  _invalidatePendingPreflight();
});
document.getElementById('proto-http').addEventListener('change', function() {
  persistScansPrefs();
  _invalidatePendingPreflight();
});
document.getElementById('max-results').addEventListener('change', function() {
  persistScansPrefs();
  _invalidatePendingPreflight();
});
document.getElementById('probe').addEventListener('change', function() {
  persistScansPrefs();
  _invalidatePendingPreflight();
});
document.getElementById('rescan-all').addEventListener('change', function() {
  persistScansPrefs();
  _invalidatePendingPreflight();
});
document.getElementById('rescan-failed').addEventListener('change', function() {
  persistScansPrefs();
  _invalidatePendingPreflight();
});
document.getElementById('countries').addEventListener('input', _invalidatePendingPreflight);
document.getElementById('filters').addEventListener('input', _invalidatePendingPreflight);

document.getElementById('scan-form').addEventListener('submit', async function(e) {
  e.preventDefault();
  var btn = document.getElementById('queue-btn');
  btn.disabled = true;
  setStatus('');

  var plan = _collectScanPlan();
  if (plan.error) {
    setStatus(plan.error, 'status-warn');
    btn.disabled = false;
    return;
  }

  try {
    var preflight = await _requestPreflight(plan);
    if (!preflight.ok) {
      _resetPreflightPanel();
      setStatus(
        'Preflight failed: ' + (preflight.data.error || preflight.status),
        'status-error'
      );
      return;
    }
    _showPreflightPanel(preflight.data || {}, plan);
    setStatus('Preflight ready. Review details, then click Start Scan.', 'status-ok');
  } catch (_err) {
    _resetPreflightPanel();
    setStatus('Preflight failed: network error.', 'status-error');
  } finally {
    btn.disabled = false;
  }
});

document.getElementById('preflight-start-btn').addEventListener('click', async function() {
  if (!pendingPreflightPlan) {
    setStatus('Run preflight before starting scan.', 'status-warn');
    return;
  }

  var startBtn = _preflightStartBtn();
  var cancelBtn = _preflightCancelBtn();
  var queueBtn = document.getElementById('queue-btn');
  startBtn.disabled = true;
  cancelBtn.disabled = true;
  queueBtn.disabled = true;
  setStatus('Queueing selected protocol scans...', 'status-neutral');

  var plan = pendingPreflightPlan;
  var result = await _queueTasksForPlan(plan);
  if (result.queued) startPolling();

  if (result.errs.length) {
    setStatus(result.errs.join('; '), 'status-error');
  } else if (result.queued) {
    setStatus('Queued ' + result.queued + ' task(s).', 'status-ok');
  }

  _resetPreflightPanel();
  queueBtn.disabled = false;
});

document.getElementById('preflight-cancel-btn').addEventListener('click', function() {
  _resetPreflightPanel();
  setStatus('Preflight cancelled. Review scan options and run preflight again.', 'status-neutral');
});

applyScansPrefsFromStorage();
_resetPreflightPanel();
hydrateQueueFromServer();
