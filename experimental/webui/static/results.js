var currentProto = 'all';
var currentPage = 1;
var pageSize = 50;
var totalPages = 1;
var openDetailRowKey = null;
var detailCache = {};
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

function buildRow(r) {
  var tr = document.createElement('tr');
  tr.className = 'result-row';
  tr.setAttribute('tabindex', '0');
  tr.dataset.rowKey = r.row_key || '';
  tr.dataset.hostType = r.host_type || '';
  tr.dataset.serverId = String(r.protocol_server_id || '');
  tr.dataset.ipAddress = r.ip_address || '';

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

  cells.forEach(function(pair) {
    var td = document.createElement('td');
    td.setAttribute('data-label', pair[0]);
    td.textContent = pair[1] != null ? String(pair[1]) : '';
    tr.appendChild(td);
  });

  tr.addEventListener('click', function() {
    toggleDetailRow(tr);
  });
  tr.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' || e.key === ' ') {
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
  detailCell.colSpan = 11;
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
  tbody.innerHTML = '<tr><td colspan="11">Loading...</td></tr>';
  setResults('');
  updatePagerButtons();

  fetch(url).then(function(resp) {
    return resp.json().then(function(data) {
      tbody.innerHTML = '';
      if (!resp.ok) {
        setResults('Error: ' + (data.error || resp.status), 'status-error');
        tbody.innerHTML = '<tr><td colspan="11" class="status-text">Query failed.</td></tr>';
        openDetailRowKey = null;
        return;
      }
      currentPage = Number(data.page || currentPage);
      totalPages = Number(data.total_pages || 1);
      if (!data.results || !data.results.length) {
        tbody.innerHTML = '<tr><td colspan="11" class="status-text">No results.</td></tr>';
        openDetailRowKey = null;
      } else {
        data.results.forEach(function(r) {
          tbody.appendChild(buildRow(r));
        });
      }
      updatePagerLabel(Number(data.total_count || 0), currentPage, totalPages);
      updatePagerButtons();
    });
  }).catch(function() {
    setResults('Request failed.', 'status-error');
    document.getElementById('results-body').innerHTML =
      '<tr><td colspan="11" class="status-text">Request failed.</td></tr>';
    openDetailRowKey = null;
    updatePagerButtons();
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
