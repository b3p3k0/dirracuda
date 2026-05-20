var currentProto = 'all';
var currentPage = 1;
var pageSize = 50;
var totalPages = 1;
var openDetailRowKey = null;
var detailCache = {};
var selectedRowKeys = new Set();
var token = document.querySelector('meta[name="csrf-token"]').content;

function setResults(msg, cls) {
  var el = document.getElementById('results-status');
  el.textContent = msg;
  el.className = cls || '';
}

function escHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function updatePagerLabel(totalCount, page, pages) {
  document.getElementById('page-label').textContent =
    'Total: ' + totalCount + '   Page ' + page + ' / ' + pages;
}

function updatePagerButtons() {
  document.getElementById('first-btn').disabled = currentPage <= 1;
  document.getElementById('prev-btn').disabled = currentPage <= 1;
  document.getElementById('next-btn').disabled = currentPage >= totalPages;
  document.getElementById('last-btn').disabled = currentPage >= totalPages;
}

function applyResultsPrefsFromStorage() {
  if (!window.DirracudaPrefs || !window.DirracudaPrefs.isAvailable()) return;
  var saved = window.DirracudaPrefs.readSection('results');
  if (saved.protocol && ['all', 'smb', 'ftp', 'http'].indexOf(saved.protocol) >= 0) {
    currentProto = saved.protocol;
  }
  document.getElementById('shares-only-filter').checked = !!saved.shares_only;
  document.getElementById('favorites-only-filter').checked = !!saved.favorites_only;
  document.getElementById('hide-avoid-filter').checked = !!saved.hide_avoid;

  var activeBtn = document.querySelector('.proto-tab[data-proto="' + currentProto + '"]');
  if (activeBtn) {
    document.querySelectorAll('.proto-tab').forEach(function(b) { b.classList.remove('active'); });
    activeBtn.classList.add('active');
  }
}

function persistResultsPrefs() {
  if (!window.DirracudaPrefs || !window.DirracudaPrefs.isAvailable()) return;
  window.DirracudaPrefs.writeSection('results', {
    protocol: currentProto,
    shares_only: document.getElementById('shares-only-filter').checked,
    favorites_only: document.getElementById('favorites-only-filter').checked,
    hide_avoid: document.getElementById('hide-avoid-filter').checked
  });
}

function _probeStatusFromEmoji(value) {
  if (value === '✖') return 'issue';
  if (value === '✔') return 'clean';
  return 'unprobed';
}

function _probeEmojiFromStatus(value) {
  var status = String(value || '').toLowerCase();
  if (status === 'issue') return '✖';
  if (status === 'clean') return '✔';
  return '○';
}

function _getRowCol(tr, index) {
  return tr.children[index] || null;
}

function _getFavoriteValue(tr) {
  var td = _getRowCol(tr, 1);
  return td && td.textContent.trim() === '✔' ? 1 : 0;
}

function _getAvoidValue(tr) {
  var td = _getRowCol(tr, 2);
  return td && td.textContent.trim() === '✖' ? 1 : 0;
}

function _getProbeStatus(tr) {
  var td = _getRowCol(tr, 3);
  return _probeStatusFromEmoji(td ? td.textContent.trim() : '○');
}

function _applyRowState(tr, state) {
  if (!state || !tr) return;
  if (Object.prototype.hasOwnProperty.call(state, 'favorite')) {
    var favTd = _getRowCol(tr, 1);
    if (favTd) favTd.textContent = Number(state.favorite) ? '✔' : '○';
  }
  if (Object.prototype.hasOwnProperty.call(state, 'avoid')) {
    var avoidTd = _getRowCol(tr, 2);
    if (avoidTd) avoidTd.textContent = Number(state.avoid) ? '✖' : '○';
  }
  if (Object.prototype.hasOwnProperty.call(state, 'probe_status')) {
    var probeTd = _getRowCol(tr, 3);
    if (probeTd) probeTd.textContent = _probeEmojiFromStatus(state.probe_status);
  }
}

function _optimisticToggle(tr, action) {
  var before = {
    favorite: _getFavoriteValue(tr),
    avoid: _getAvoidValue(tr),
    probe_status: _getProbeStatus(tr)
  };

  if (action === 'favorite') {
    _applyRowState(tr, {favorite: before.favorite ? 0 : 1});
  } else if (action === 'avoid') {
    _applyRowState(tr, {avoid: before.avoid ? 0 : 1});
  } else if (action === 'compromised') {
    var isCompromised = before.probe_status === 'issue';
    _applyRowState(tr, {probe_status: isCompromised ? 'clean' : 'issue'});
  }

  return before;
}

function _buildToggleTargets(rows) {
  var targets = [];
  rows.forEach(function(tr) {
    var hostType = tr.dataset.hostType || '';
    var serverId = Number(tr.dataset.serverId || '0');
    var rowKey = tr.dataset.rowKey || '';
    if (!hostType || !Number.isInteger(serverId) || serverId <= 0) return;
    targets.push({
      host_type: hostType,
      protocol_server_id: serverId,
      row_key: rowKey
    });
  });
  return targets;
}

async function _postToggleAction(action, targets) {
  var resp = await fetch('/api/results/actions/toggle', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': token
    },
    body: JSON.stringify({action: action, targets: targets})
  });
  var data = await resp.json();
  return {ok: resp.ok, status: resp.status, data: data};
}

async function _performToggleAction(action, rows) {
  if (!rows || !rows.length) {
    setResults('Select at least one row first.', 'status-warn');
    return;
  }

  var targets = _buildToggleTargets(rows);
  if (!targets.length) {
    setResults('No valid rows selected for action.', 'status-warn');
    return;
  }

  var beforeByRowKey = {};
  rows.forEach(function(tr) {
    var rowKey = tr.dataset.rowKey || '';
    if (!rowKey) return;
    beforeByRowKey[rowKey] = _optimisticToggle(tr, action);
  });

  try {
    var result = await _postToggleAction(action, targets);
    if (!result.ok) {
      rows.forEach(function(tr) {
        var rowKey = tr.dataset.rowKey || '';
        if (!rowKey || !beforeByRowKey[rowKey]) return;
        _applyRowState(tr, beforeByRowKey[rowKey]);
      });
      setResults('Action failed: ' + (result.data.error || result.status), 'status-error');
      return;
    }

    var payload = result.data || {};
    var outcomes = payload.results || [];
    var byRowKey = {};
    outcomes.forEach(function(outcome) {
      if (!outcome || !outcome.row_key) return;
      byRowKey[outcome.row_key] = outcome;
    });

    rows.forEach(function(tr) {
      var rowKey = tr.dataset.rowKey || '';
      if (!rowKey) return;
      var outcome = byRowKey[rowKey];
      if (!outcome || outcome.ok !== true || !outcome.state) {
        if (beforeByRowKey[rowKey]) {
          _applyRowState(tr, beforeByRowKey[rowKey]);
        }
        return;
      }
      _applyRowState(tr, outcome.state);
    });

    var failed = Number(payload.failed || 0);
    var updated = Number(payload.updated || 0);
    if (failed > 0) {
      setResults('Action complete: ' + updated + ' updated, ' + failed + ' failed.', 'status-warn');
    } else {
      setResults('Action complete: ' + updated + ' updated.', 'status-ok');
    }
  } catch (err) {
    rows.forEach(function(tr) {
      var rowKey = tr.dataset.rowKey || '';
      if (!rowKey || !beforeByRowKey[rowKey]) return;
      _applyRowState(tr, beforeByRowKey[rowKey]);
    });
    setResults('Action failed: network error.', 'status-error');
  }
}

function _setRowSelected(rowKey, selected) {
  if (!rowKey) return;
  if (selected) {
    selectedRowKeys.add(rowKey);
  } else {
    selectedRowKeys.delete(rowKey);
  }
}

function _syncSelectionUi() {
  var rows = document.querySelectorAll('#results-body tr.result-row');
  rows.forEach(function(tr) {
    var rowKey = tr.dataset.rowKey || '';
    var cb = tr.querySelector('input.row-select');
    if (!cb) return;
    cb.checked = selectedRowKeys.has(rowKey);
  });

  var allCb = document.getElementById('select-all-rows');
  var visible = Array.from(rows).filter(function(tr) {
    return !!tr.dataset.rowKey;
  });
  var checkedCount = visible.filter(function(tr) {
    return selectedRowKeys.has(tr.dataset.rowKey || '');
  }).length;

  if (allCb) {
    allCb.indeterminate = checkedCount > 0 && checkedCount < visible.length;
    allCb.checked = visible.length > 0 && checkedCount === visible.length;
  }

  var hasSelection = selectedRowKeys.size > 0;
  document.getElementById('bulk-favorite-btn').disabled = !hasSelection;
  document.getElementById('bulk-avoid-btn').disabled = !hasSelection;
  document.getElementById('bulk-compromised-btn').disabled = !hasSelection;
  document.getElementById('clear-selection-btn').disabled = !hasSelection;
}

function _resetSelection() {
  selectedRowKeys = new Set();
  _syncSelectionUi();
}

function _collectSelectedRows() {
  var rows = document.querySelectorAll('#results-body tr.result-row');
  var selectedRows = [];
  rows.forEach(function(tr) {
    var rowKey = tr.dataset.rowKey || '';
    if (rowKey && selectedRowKeys.has(rowKey)) {
      selectedRows.push(tr);
    }
  });
  return selectedRows;
}

function _selectAllVisibleRows(checked) {
  var rows = document.querySelectorAll('#results-body tr.result-row');
  rows.forEach(function(tr) {
    var rowKey = tr.dataset.rowKey || '';
    if (!rowKey) return;
    _setRowSelected(rowKey, checked);
  });
  _syncSelectionUi();
}

function buildRow(r) {
  var tr = document.createElement('tr');
  tr.className = 'result-row';
  tr.setAttribute('tabindex', '0');
  tr.dataset.rowKey = r.row_key || '';
  tr.dataset.hostType = r.host_type || '';
  tr.dataset.serverId = String(r.protocol_server_id || '');
  tr.dataset.ipAddress = r.ip_address || '';

  var rowKey = tr.dataset.rowKey;

  var selectTd = document.createElement('td');
  selectTd.setAttribute('data-label', 'Select');
  var selectCb = document.createElement('input');
  selectCb.type = 'checkbox';
  selectCb.className = 'row-select';
  selectCb.setAttribute('aria-label', 'Select row ' + (r.ip_address || rowKey || ''));
  selectCb.checked = selectedRowKeys.has(rowKey);
  selectCb.addEventListener('click', function(e) {
    e.stopPropagation();
  });
  selectCb.addEventListener('change', function(e) {
    e.stopPropagation();
    _setRowSelected(rowKey, selectCb.checked);
    _syncSelectionUi();
  });
  selectTd.appendChild(selectCb);
  tr.appendChild(selectTd);

  var cells = [
    ['Favorite', r.favorite],
    ['Avoid', r.avoid],
    ['Probed', r.probe_status_emoji],
    ['Extracted', r.extract_status_emoji],
    ['Type', r.host_type],
    ['IP Address', r.ip_address],
    ['Shares', r.shares],
    ['Accessible', r.accessible_shares_list],
    ['Denied', r.denied_shares_count],
    ['Last Seen', r.last_seen],
    ['Country', r.country]
  ];

  cells.forEach(function(pair, idx) {
    var td = document.createElement('td');
    td.setAttribute('data-label', pair[0]);
    td.textContent = pair[1] != null ? String(pair[1]) : '';

    if (idx === 0 || idx === 1 || idx === 2) {
      var action = idx === 0 ? 'favorite' : (idx === 1 ? 'avoid' : 'compromised');
      td.classList.add('action-cell');
      td.setAttribute('role', 'button');
      td.setAttribute('tabindex', '0');
      td.addEventListener('click', function(e) {
        e.stopPropagation();
        _performToggleAction(action, [tr]);
      });
      td.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          e.stopPropagation();
          _performToggleAction(action, [tr]);
        }
      });
    }

    tr.appendChild(td);
  });

  tr.addEventListener('click', function(e) {
    if (e.target && e.target.closest('input, button, .action-cell')) {
      return;
    }
    toggleDetailRow(tr);
  });

  tr.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' || e.key === ' ') {
      if (e.target && e.target !== tr) {
        return;
      }
      e.preventDefault();
      toggleDetailRow(tr);
    }
  });

  return tr;
}

function closeOpenDetail() {
  if (!openDetailRowKey) return;
  var tbody = document.getElementById('results-body');
  var openRow = tbody.querySelector('tr.result-row[data-row-key="' + openDetailRowKey + '"]');
  if (openRow) openRow.classList.remove('selected');
  var detailRow = tbody.querySelector('tr.result-detail-row[data-parent-key="' + openDetailRowKey + '"]');
  if (detailRow) detailRow.remove();
  openDetailRowKey = null;
}

function _overviewValue(value) {
  if (value == null || value === '') return '(none)';
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  return String(value);
}

function _detailSummaryText(payload) {
  return 'Host Details: ' +
    (payload.ip_address || 'Unknown') +
    ' (' + (payload.protocol || payload.host_type || '?') + ')';
}

function renderDetailContent(container, payload) {
  container.innerHTML = '';
  container.classList.remove('status-error');

  var header = document.createElement('div');
  header.className = 'result-detail-header';
  header.textContent = _detailSummaryText(payload);
  container.appendChild(header);

  var overview = payload.overview || {};
  var rows = [
    ['Protocol', overview.protocol],
    ['Status', overview.status],
    ['Last Seen', overview.last_seen],
    ['Country', overview.country + (overview.country_code ? ' (' + overview.country_code + ')' : '')],
    ['Scan Count', overview.scan_count],
    ['Auth', overview.auth_method],
    ['Access', overview.access_summary],
    ['Probe', overview.probe_status],
    ['Extracted', overview.extracted]
  ];

  var kv = document.createElement('div');
  kv.className = 'result-detail-kv';
  rows.forEach(function(pair) {
    var item = document.createElement('div');
    item.className = 'result-detail-kv-item';

    var k = document.createElement('span');
    k.className = 'result-detail-k';
    k.textContent = pair[0] + ':';
    item.appendChild(k);

    var v = document.createElement('span');
    v.className = 'result-detail-v';
    v.textContent = _overviewValue(pair[1]);
    item.appendChild(v);

    kv.appendChild(item);
  });
  container.appendChild(kv);

  var notesWrap = document.createElement('div');
  notesWrap.className = 'result-detail-notes-wrap';
  var notesLabel = document.createElement('div');
  notesLabel.className = 'result-detail-notes-label';
  notesLabel.textContent = 'Notes (read-only):';
  var notes = document.createElement('pre');
  notes.className = 'result-detail-notes';
  notes.textContent = payload.notes || '(none)';
  notesWrap.appendChild(notesLabel);
  notesWrap.appendChild(notes);
  container.appendChild(notesWrap);

  var toggleBtn = document.createElement('button');
  toggleBtn.type = 'button';
  toggleBtn.className = 'detail-toggle';
  toggleBtn.setAttribute('aria-expanded', 'false');
  toggleBtn.textContent = 'Show full details + probe tree ▾';
  container.appendChild(toggleBtn);

  var fullWrap = document.createElement('div');
  fullWrap.className = 'detail-full-wrap';
  fullWrap.hidden = true;
  var fullBox = document.createElement('textarea');
  fullBox.className = 'detail-full-box';
  fullBox.setAttribute('readonly', 'readonly');
  fullBox.setAttribute('rows', '10');
  fullBox.value = payload.full_details_text || '(no detail text)';
  fullWrap.appendChild(fullBox);
  container.appendChild(fullWrap);

  toggleBtn.addEventListener('click', function() {
    var isOpen = !fullWrap.hidden;
    fullWrap.hidden = isOpen;
    toggleBtn.setAttribute('aria-expanded', isOpen ? 'false' : 'true');
    toggleBtn.textContent = (
      isOpen
        ? 'Show full details + probe tree ▾'
        : 'Hide full details + probe tree ▴'
    );
  });
}

function renderDetailError(container, msg) {
  container.innerHTML = '';
  container.classList.add('status-error');
  container.textContent = msg;
}

function _detailColspan() {
  var cols = document.querySelectorAll('#results-table thead th').length;
  return cols > 0 ? cols : 12;
}

function toggleDetailRow(baseRow) {
  var rowKey = baseRow.dataset.rowKey || '';
  var hostType = baseRow.dataset.hostType || '';
  var serverId = Number(baseRow.dataset.serverId || '0');
  if (!rowKey || !hostType || !Number.isInteger(serverId) || serverId <= 0) return;

  if (openDetailRowKey === rowKey) {
    closeOpenDetail();
    return;
  }

  closeOpenDetail();
  openDetailRowKey = rowKey;
  baseRow.classList.add('selected');

  var detailRow = document.createElement('tr');
  detailRow.className = 'result-detail-row';
  detailRow.dataset.parentKey = rowKey;
  var detailCell = document.createElement('td');
  detailCell.colSpan = _detailColspan();
  var detailBox = document.createElement('div');
  detailBox.className = 'result-detail-box';
  detailBox.textContent = 'Loading details...';
  detailCell.appendChild(detailBox);
  detailRow.appendChild(detailCell);
  baseRow.insertAdjacentElement('afterend', detailRow);

  if (detailCache[rowKey]) {
    renderDetailContent(detailBox, detailCache[rowKey]);
    return;
  }

  var url = '/api/results/details?host_type=' +
    encodeURIComponent(hostType) +
    '&protocol_server_id=' +
    encodeURIComponent(String(serverId));

  fetch(url).then(function(resp) {
    return resp.json().then(function(data) {
      if (openDetailRowKey !== rowKey) return;
      if (!resp.ok) {
        renderDetailError(detailBox, 'Failed to load details: ' + (data.error || resp.status));
        return;
      }
      detailCache[rowKey] = data;
      renderDetailContent(detailBox, data);
    });
  }).catch(function() {
    if (openDetailRowKey !== rowKey) return;
    renderDetailError(detailBox, 'Failed to load details: request error');
  });
}

function loadResults() {
  var search = document.getElementById('search-filter').value.trim();
  var sharesOnly = document.getElementById('shares-only-filter').checked;
  var favoritesOnly = document.getElementById('favorites-only-filter').checked;
  var hideAvoid = document.getElementById('hide-avoid-filter').checked;
  var url = '/api/results/' + currentProto +
    '?page=' + currentPage + '&page_size=' + pageSize +
    '&shares_only=' + (sharesOnly ? 'true' : 'false') +
    '&favorites_only=' + (favoritesOnly ? 'true' : 'false') +
    '&hide_avoid=' + (hideAvoid ? 'true' : 'false');
  if (search) url += '&search=' + encodeURIComponent(search);

  var tbody = document.getElementById('results-body');
  closeOpenDetail();
  _resetSelection();
  tbody.innerHTML = '<tr><td colspan="' + _detailColspan() + '">Loading...</td></tr>';
  setResults('');
  updatePagerButtons();

  fetch(url).then(function(resp) {
    return resp.json().then(function(data) {
      tbody.innerHTML = '';
      if (!resp.ok) {
        setResults('Error: ' + (data.error || resp.status), 'status-error');
        tbody.innerHTML = '<tr><td colspan="' + _detailColspan() + '" class="status-text">Query failed.</td></tr>';
        openDetailRowKey = null;
        _syncSelectionUi();
        return;
      }
      currentPage = Number(data.page || currentPage);
      totalPages = Number(data.total_pages || 1);
      if (!data.results || !data.results.length) {
        tbody.innerHTML = '<tr><td colspan="' + _detailColspan() + '" class="status-text">No results.</td></tr>';
        openDetailRowKey = null;
      } else {
        data.results.forEach(function(r) {
          tbody.appendChild(buildRow(r));
        });
      }
      updatePagerLabel(Number(data.total_count || 0), currentPage, totalPages);
      updatePagerButtons();
      _syncSelectionUi();
    });
  }).catch(function() {
    setResults('Request failed.', 'status-error');
    document.getElementById('results-body').innerHTML =
      '<tr><td colspan="' + _detailColspan() + '" class="status-text">Request failed.</td></tr>';
    openDetailRowKey = null;
    updatePagerButtons();
    _syncSelectionUi();
  });
}

document.querySelectorAll('.proto-tab').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('.proto-tab').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    currentProto = btn.getAttribute('data-proto');
    persistResultsPrefs();
    currentPage = 1;
    loadResults();
  });
});

document.getElementById('shares-only-filter').addEventListener('change', persistResultsPrefs);
document.getElementById('favorites-only-filter').addEventListener('change', persistResultsPrefs);
document.getElementById('hide-avoid-filter').addEventListener('change', persistResultsPrefs);

document.getElementById('load-btn').addEventListener('click', function() {
  currentPage = 1;
  loadResults();
});
document.getElementById('search-filter').addEventListener('keydown', function(e) {
  if (e.key === 'Enter') {
    e.preventDefault();
    currentPage = 1;
    loadResults();
  }
});

document.getElementById('first-btn').addEventListener('click', function() {
  if (currentPage > 1) {
    currentPage = 1;
    loadResults();
  }
});

document.getElementById('prev-btn').addEventListener('click', function() {
  if (currentPage > 1) {
    currentPage--;
    loadResults();
  }
});

document.getElementById('next-btn').addEventListener('click', function() {
  if (currentPage < totalPages) {
    currentPage++;
    loadResults();
  }
});

document.getElementById('last-btn').addEventListener('click', function() {
  if (currentPage < totalPages) {
    currentPage = totalPages;
    loadResults();
  }
});

document.getElementById('jump-btn').addEventListener('click', function() {
  var raw = document.getElementById('jump-page').value;
  var jumpPage = Number(raw);
  if (!Number.isInteger(jumpPage) || jumpPage < 1 || jumpPage > totalPages) {
    setResults('Jump-to page must be between 1 and ' + totalPages + '.', 'status-warn');
    return;
  }
  if (jumpPage !== currentPage) {
    currentPage = jumpPage;
    loadResults();
  }
});

document.getElementById('jump-page').addEventListener('keydown', function(e) {
  if (e.key === 'Enter') {
    e.preventDefault();
    document.getElementById('jump-btn').click();
  }
});

document.getElementById('select-all-rows').addEventListener('click', function(e) {
  e.stopPropagation();
});

document.getElementById('select-all-rows').addEventListener('change', function() {
  _selectAllVisibleRows(this.checked);
});

document.getElementById('clear-selection-btn').addEventListener('click', function() {
  _resetSelection();
  setResults('Selection cleared.', 'status-neutral');
});

document.getElementById('bulk-favorite-btn').addEventListener('click', function() {
  _performToggleAction('favorite', _collectSelectedRows());
});

document.getElementById('bulk-avoid-btn').addEventListener('click', function() {
  _performToggleAction('avoid', _collectSelectedRows());
});

document.getElementById('bulk-compromised-btn').addEventListener('click', function() {
  _performToggleAction('compromised', _collectSelectedRows());
});

document.getElementById('export-btn').addEventListener('click', async function() {
  var es = document.getElementById('export-status');
  es.textContent = 'Exporting...';
  es.className = '';
  try {
    var resp = await fetch('/api/export', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token},
      body: '{}'
    });
    var data = await resp.json();
    if (resp.ok && data.filename) {
      es.textContent = 'Export ready: ' + data.filename;
      es.className = 'status-text ok';
      window.location.href = '/api/export/' + encodeURIComponent(data.filename);
    } else {
      es.textContent = 'Export failed: ' + (data.error || resp.status);
      es.className = 'status-text error';
    }
  } catch (ex) {
    es.textContent = 'Export failed: network error';
    es.className = 'status-text error';
  }
});

applyResultsPrefsFromStorage();
loadResults();
