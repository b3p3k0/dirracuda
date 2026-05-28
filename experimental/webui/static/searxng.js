var token = document.querySelector('meta[name="csrf-token"]').content;
var trackedTasks = {};
var terminalSeen = {};
var pollTimer = null;
var TERMINAL = {done: 1, failed: 1, cancelled: 1};

function escHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function _taskId(task) {
  if (!task || typeof task !== 'object') return '';
  return String(task.task_id || task.job_id || '');
}

function setStatus(msg, cls) {
  var el = document.getElementById('submit-status');
  el.textContent = msg;
  el.className = cls ? cls : '';
}

function upsertRow(task) {
  var taskId = _taskId(task);
  if (!taskId) return;
  var tbody = document.getElementById('queue-body');
  var existing = trackedTasks[taskId];
  if (!existing) {
    var placeholder = tbody.querySelector('[colspan]');
    if (placeholder) placeholder.parentNode.remove();
    var tr = document.createElement('tr');
    tr.dataset.taskId = taskId;
    tr.innerHTML =
      '<td class="cell-id"></td>' +
      '<td class="cell-source"></td>' +
      '<td class="cell-state"></td>' +
      '<td class="cell-progress"></td>' +
      '<td class="cell-action"></td>';
    var cancelBtn = document.createElement('button');
    cancelBtn.textContent = 'Cancel';
    cancelBtn.type = 'button';
    cancelBtn.addEventListener('click', function() { cancelTask(taskId, tr); });
    tr.querySelector('.cell-action').appendChild(cancelBtn);
    tbody.appendChild(tr);
    trackedTasks[taskId] = tr;
    existing = tr;
  }
  var source = task.source || (task.metadata && task.metadata.source) || task.kind || '';
  var label = task.label || source;
  existing.querySelector('.cell-id').textContent = taskId.slice(0, 8);
  existing.querySelector('.cell-source').textContent = label;
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
  if (snapshot.active && _taskId(snapshot.active)) tasks.push(snapshot.active);
  if (Array.isArray(snapshot.queued)) {
    snapshot.queued.forEach(function(task) {
      if (task && _taskId(task)) tasks.push(task);
    });
  }
  return tasks;
}

async function hydrateQueueFromServer() {
  try {
    var resp = await fetch('/api/jobs');
    if (!resp.ok) return;
    var payload = await resp.json();
    var tasks = _tasksFromQueueSnapshot(payload);
    if (!tasks.length) return;
    tasks.forEach(function(task) { upsertRow(task); });
    startPolling();
  } catch (_err) {}
}

function cancelTask(taskId, tr) {
  fetch('/api/jobs/' + encodeURIComponent(taskId) + '/cancel', {
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
    fetch('/api/jobs/' + encodeURIComponent(id))
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(data) {
        if (!data) return;
        var wasTerminal = !!terminalSeen[id];
        upsertRow(data);
        if (!wasTerminal && TERMINAL[data.status]) {
          terminalSeen[id] = true;
        }
      })
      .catch(function() {});
  });
  pollTimer = setTimeout(pollAll, 3000);
}

function startPolling() {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = setTimeout(pollAll, 3000);
}

function applyPrefs() {
  if (!window.DirracudaPrefs || !window.DirracudaPrefs.isAvailable()) return;
  var saved = window.DirracudaPrefs.readSection('searxng');
  if (saved.instance_url) document.getElementById('instance-url').value = saved.instance_url;
  if (Number.isInteger(saved.max_results) && saved.max_results >= 1 && saved.max_results <= 500) {
    document.getElementById('max-results').value = String(saved.max_results);
  }
  if (saved.bulk_probe !== undefined) {
    document.getElementById('bulk-probe').checked = !!saved.bulk_probe;
  }
  if (saved.probe_workers) {
    var sel = document.getElementById('probe-workers');
    for (var i = 0; i < sel.options.length; i++) {
      if (sel.options[i].value === String(saved.probe_workers)) {
        sel.selectedIndex = i;
        break;
      }
    }
  }
  _syncProbeWorkersRow();
}

function persistPrefs() {
  if (!window.DirracudaPrefs || !window.DirracudaPrefs.isAvailable()) return;
  var maxResults = parseInt(document.getElementById('max-results').value, 10);
  window.DirracudaPrefs.writeSection('searxng', {
    instance_url: document.getElementById('instance-url').value,
    max_results: (maxResults >= 1 && maxResults <= 500) ? maxResults : 50,
    bulk_probe: document.getElementById('bulk-probe').checked,
    probe_workers: parseInt(document.getElementById('probe-workers').value, 10) || 3
  });
}

function _syncProbeWorkersRow() {
  var row = document.getElementById('probe-workers-row');
  if (!row) return;
  if (document.getElementById('bulk-probe').checked) {
    row.classList.remove('hidden');
  } else {
    row.classList.add('hidden');
  }
}

document.getElementById('bulk-probe').addEventListener('change', function() {
  _syncProbeWorkersRow();
  persistPrefs();
});

document.getElementById('instance-url').addEventListener('change', persistPrefs);
document.getElementById('max-results').addEventListener('change', persistPrefs);
document.getElementById('probe-workers').addEventListener('change', persistPrefs);

document.getElementById('searxng-form').addEventListener('submit', async function(e) {
  e.preventDefault();
  var btn = document.getElementById('run-btn');
  btn.disabled = true;
  setStatus('');

  var instanceUrl = (document.getElementById('instance-url').value || '').trim();
  var query = (document.getElementById('query').value || '').trim();
  var maxResults = parseInt(document.getElementById('max-results').value, 10);
  var bulkProbe = document.getElementById('bulk-probe').checked;
  var probeWorkers = parseInt(document.getElementById('probe-workers').value, 10) || 3;

  if (!instanceUrl || !/^https?:\/\/.+/.test(instanceUrl)) {
    setStatus('Instance URL must start with http:// or https://.', 'status-warn');
    btn.disabled = false;
    return;
  }
  if (!query) {
    setStatus('Query is required.', 'status-warn');
    btn.disabled = false;
    return;
  }
  if (!(maxResults >= 1 && maxResults <= 500)) {
    setStatus('Max results must be between 1 and 500.', 'status-warn');
    btn.disabled = false;
    return;
  }

  setStatus('Checking instance…', 'status-neutral');
  try {
    var pfResp = await fetch('/api/searxng/preflight', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token},
      body: JSON.stringify({instance_url: instanceUrl})
    });
    var pfData = await pfResp.json();
    if (!pfData.ok) {
      setStatus('Instance check failed: ' + (pfData.message || 'unknown'), 'status-error');
      btn.disabled = false;
      return;
    }
  } catch (_err) {
    setStatus('Instance check failed: network error.', 'status-error');
    btn.disabled = false;
    return;
  }

  setStatus('Queueing discovery job…', 'status-neutral');
  try {
    var runBody = {
      instance_url: instanceUrl,
      query: query,
      max_results: maxResults,
      bulk_probe_enabled: bulkProbe,
      probe_worker_count: bulkProbe ? probeWorkers : null
    };
    var runResp = await fetch('/api/searxng/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token},
      body: JSON.stringify(runBody)
    });
    var runData = await runResp.json();
    if (runResp.status !== 202) {
      var msg = (runData && runData.detail) ? JSON.stringify(runData.detail) : (runData && runData.error) || 'Request failed.';
      setStatus('Run failed: ' + msg, 'status-error');
      btn.disabled = false;
      return;
    }
    upsertRow({
      job_id: runData.job_id,
      status: runData.status,
      source: 'searxng',
      kind: 'run',
      label: 'SearXNG run: ' + query.slice(0, 60),
      progress_message: ''
    });
    startPolling();
    setStatus('Discovery job queued.', 'status-ok');
    persistPrefs();
  } catch (_err) {
    setStatus('Run failed: network error.', 'status-error');
  } finally {
    btn.disabled = false;
  }
});

applyPrefs();
hydrateQueueFromServer();
