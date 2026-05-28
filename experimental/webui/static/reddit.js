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

function _syncModeFields() {
  var mode = document.getElementById('mode').value;
  var queryRow = document.getElementById('query-row');
  var usernameRow = document.getElementById('username-row');
  if (mode === 'search') {
    queryRow.classList.remove('hidden');
    usernameRow.classList.add('hidden');
  } else if (mode === 'user') {
    queryRow.classList.add('hidden');
    usernameRow.classList.remove('hidden');
  } else {
    queryRow.classList.add('hidden');
    usernameRow.classList.add('hidden');
  }
}

function _syncSortFields() {
  var sort = document.getElementById('sort').value;
  var topWindowRow = document.getElementById('top-window-row');
  if (sort === 'top') {
    topWindowRow.classList.remove('hidden');
  } else {
    topWindowRow.classList.add('hidden');
  }
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

function applyPrefs() {
  if (window.DirracudaPrefs && window.DirracudaPrefs.isAvailable()) {
    var saved = window.DirracudaPrefs.readSection('reddit');
    if (saved.mode) {
      var modeEl = document.getElementById('mode');
      for (var i = 0; i < modeEl.options.length; i++) {
        if (modeEl.options[i].value === saved.mode) { modeEl.selectedIndex = i; break; }
      }
    }
    if (saved.sort) {
      var sortEl = document.getElementById('sort');
      for (var j = 0; j < sortEl.options.length; j++) {
        if (sortEl.options[j].value === saved.sort) { sortEl.selectedIndex = j; break; }
      }
    }
    if (Number.isInteger(saved.max_posts) && saved.max_posts >= 1 && saved.max_posts <= 200) {
      document.getElementById('max-posts').value = String(saved.max_posts);
    }
    if (saved.bulk_probe !== undefined) {
      document.getElementById('bulk-probe').checked = !!saved.bulk_probe;
    }
    if (saved.probe_workers) {
      var sel = document.getElementById('probe-workers');
      for (var k = 0; k < sel.options.length; k++) {
        if (sel.options[k].value === String(saved.probe_workers)) { sel.selectedIndex = k; break; }
      }
    }
  }
  _syncModeFields();
  _syncSortFields();
  _syncProbeWorkersRow();
}

function persistPrefs() {
  if (!window.DirracudaPrefs || !window.DirracudaPrefs.isAvailable()) return;
  var maxPosts = parseInt(document.getElementById('max-posts').value, 10);
  window.DirracudaPrefs.writeSection('reddit', {
    mode: document.getElementById('mode').value,
    sort: document.getElementById('sort').value,
    max_posts: (maxPosts >= 1 && maxPosts <= 200) ? maxPosts : 100,
    bulk_probe: document.getElementById('bulk-probe').checked,
    probe_workers: parseInt(document.getElementById('probe-workers').value, 10) || 3
  });
}

document.getElementById('mode').addEventListener('change', function() {
  _syncModeFields();
  persistPrefs();
});

document.getElementById('sort').addEventListener('change', function() {
  _syncSortFields();
  persistPrefs();
});

document.getElementById('bulk-probe').addEventListener('change', function() {
  _syncProbeWorkersRow();
  persistPrefs();
});

document.getElementById('max-posts').addEventListener('change', persistPrefs);
document.getElementById('probe-workers').addEventListener('change', persistPrefs);

document.getElementById('reddit-form').addEventListener('submit', async function(e) {
  e.preventDefault();
  var btn = document.getElementById('run-btn');
  btn.disabled = true;
  setStatus('');

  var mode = document.getElementById('mode').value;
  var sort = document.getElementById('sort').value;
  var topWindow = document.getElementById('top-window').value;
  var query = (document.getElementById('query').value || '').trim();
  var username = (document.getElementById('username').value || '').trim();
  var maxPosts = parseInt(document.getElementById('max-posts').value, 10);
  var maxPages = parseInt(document.getElementById('max-pages').value, 10);
  var parseBody = document.getElementById('parse-body').checked;
  var includeNsfw = document.getElementById('include-nsfw').checked;
  var replaceCache = document.getElementById('replace-cache').checked;
  var bulkProbe = document.getElementById('bulk-probe').checked;
  var probeWorkers = parseInt(document.getElementById('probe-workers').value, 10) || 3;

  if (mode === 'search' && !query) {
    setStatus('Query is required for search mode.', 'status-warn');
    btn.disabled = false;
    return;
  }
  if (mode === 'user' && !username) {
    setStatus('Username is required for user mode.', 'status-warn');
    btn.disabled = false;
    return;
  }
  if (!(maxPosts >= 1 && maxPosts <= 200)) {
    setStatus('Max posts must be between 1 and 200.', 'status-warn');
    btn.disabled = false;
    return;
  }
  if (!(maxPages >= 1 && maxPages <= 3)) {
    setStatus('Max pages must be between 1 and 3.', 'status-warn');
    btn.disabled = false;
    return;
  }

  setStatus('Queueing discovery job…', 'status-neutral');
  try {
    var runBody = {
      mode: mode,
      sort: sort,
      top_window: topWindow,
      query: query,
      username: username,
      max_posts: maxPosts,
      max_pages: maxPages,
      parse_body: parseBody,
      include_nsfw: includeNsfw,
      replace_cache: replaceCache,
      bulk_probe_enabled: bulkProbe,
      probe_worker_count: bulkProbe ? probeWorkers : null
    };
    var runResp = await fetch('/api/reddit/run', {
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
      source: 'reddit',
      kind: 'run',
      label: 'Reddit ' + mode + ' run (' + sort + ')',
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
