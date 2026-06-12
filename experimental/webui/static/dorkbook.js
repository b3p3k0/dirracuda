var token = document.querySelector('meta[name="csrf-token"]').content;
var activeProtocol = "SMB";
var searchTimer = null;

function escHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function showStatus(msg, isError) {
  var el = document.getElementById('dorkbook-status');
  el.textContent = msg;
  el.className = isError ? 'status-error' : 'status-ok';
}

function loadEntries(protocol) {
  var search = document.getElementById('dorkbook-search').value || '';
  var url = '/api/dorkbook/entries?protocol=' + encodeURIComponent(protocol) +
    (search ? '&search=' + encodeURIComponent(search) : '');
  fetch(url, {credentials: 'same-origin'})
    .then(function(r) { return r.json(); })
    .then(function(data) { renderEntries(data.entries || []); })
    .catch(function(err) { showStatus('Failed to load entries: ' + err, true); });
}

function renderEntries(entries) {
  var tbody = document.getElementById('dorkbook-tbody');
  tbody.innerHTML = '';
  if (!entries.length) {
    var tr = document.createElement('tr');
    var td = document.createElement('td');
    td.colSpan = 4;
    td.textContent = 'No recipes. Use Add below to create one.';
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }
  entries.forEach(function(row) {
    var tr = document.createElement('tr');
    if (row.row_kind === 'builtin') {
      tr.classList.add('is-builtin');
    }
    var tdNick = document.createElement('td');
    tdNick.textContent = row.nickname || '';
    if (row.row_kind === 'builtin') {
      tdNick.style.fontStyle = 'italic';
    }
    var tdQuery = document.createElement('td');
    tdQuery.textContent = row.query || '';
    var tdNotes = document.createElement('td');
    tdNotes.textContent = row.notes || '';
    var tdActions = document.createElement('td');

    var applyBtn = document.createElement('button');
    applyBtn.type = 'button';
    applyBtn.className = 'btn btn-secondary btn-sm';
    applyBtn.textContent = 'Apply to Config';
    applyBtn.setAttribute('data-entry-id', row.entry_id);
    tdActions.appendChild(applyBtn);

    if (row.row_kind !== 'builtin') {
      var delBtn = document.createElement('button');
      delBtn.type = 'button';
      delBtn.className = 'btn btn-danger btn-sm';
      delBtn.textContent = 'Delete';
      delBtn.style.marginLeft = '0.4rem';
      delBtn.setAttribute('data-entry-id', row.entry_id);
      tdActions.appendChild(delBtn);
      delBtn.addEventListener('click', function() {
        handleDelete(Number(this.getAttribute('data-entry-id')));
      });
    }

    applyBtn.addEventListener('click', function() {
      handleApply(Number(this.getAttribute('data-entry-id')));
    });

    tr.appendChild(tdNick);
    tr.appendChild(tdQuery);
    tr.appendChild(tdNotes);
    tr.appendChild(tdActions);
    tbody.appendChild(tr);
  });
}

function switchTab(protocol) {
  activeProtocol = protocol;
  document.getElementById('add-protocol').value = protocol;
  var tabs = document.querySelectorAll('#dorkbook-tabs .tab-btn');
  tabs.forEach(function(btn) {
    var active = btn.getAttribute('data-protocol') === protocol;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  loadEntries(protocol);
}

function handleAdd(event) {
  event.preventDefault();
  var protocol = document.getElementById('add-protocol').value;
  var nickname = document.getElementById('add-nickname').value || '';
  var query = document.getElementById('add-query').value || '';
  var notes = document.getElementById('add-notes').value || '';
  if (!query.trim()) {
    showStatus('Query is required.', true);
    return;
  }
  fetch('/api/dorkbook/entries', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token},
    body: JSON.stringify({protocol: protocol, nickname: nickname, query: query, notes: notes})
  })
    .then(function(r) {
      if (r.status === 409) {
        return r.json().then(function(d) { throw new Error(d.error || 'duplicate entry'); });
      }
      if (!r.ok) {
        return r.json().then(function(d) { throw new Error(d.error || 'add failed'); });
      }
      return r.json();
    })
    .then(function() {
      document.getElementById('dorkbook-add-form').reset();
      document.getElementById('add-protocol').value = activeProtocol;
      showStatus('Entry added.', false);
      loadEntries(activeProtocol);
    })
    .catch(function(err) { showStatus('Add failed: ' + err.message, true); });
}

function handleApply(entryId) {
  fetch('/api/dorkbook/prefill', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token},
    body: JSON.stringify({entry_id: entryId})
  })
    .then(function(r) {
      if (!r.ok) {
        return r.json().then(function(d) { throw new Error(d.error || 'apply failed'); });
      }
      return r.json();
    })
    .then(function(data) {
      showStatus('Applied ' + (data.protocol || '') + ' query to discovery config.', false);
    })
    .catch(function(err) { showStatus('Apply failed: ' + err.message, true); });
}

function handleDelete(entryId) {
  fetch('/api/dorkbook/entries/' + entryId, {
    method: 'DELETE',
    credentials: 'same-origin',
    headers: {'X-CSRF-Token': token}
  })
    .then(function(r) {
      if (!r.ok) {
        return r.json().then(function(d) { throw new Error(d.error || 'delete failed'); });
      }
      return r.json();
    })
    .then(function() {
      showStatus('Entry deleted.', false);
      loadEntries(activeProtocol);
    })
    .catch(function(err) { showStatus('Delete failed: ' + err.message, true); });
}

document.addEventListener('DOMContentLoaded', function() {
  document.querySelectorAll('#dorkbook-tabs .tab-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      switchTab(this.getAttribute('data-protocol'));
    });
  });

  var searchInput = document.getElementById('dorkbook-search');
  searchInput.addEventListener('input', function() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function() { loadEntries(activeProtocol); }, 250);
  });

  document.getElementById('dorkbook-add-form').addEventListener('submit', handleAdd);

  loadEntries(activeProtocol);
});
