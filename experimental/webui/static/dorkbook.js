/* Unified provider library. Selecting a row is always preview-only. */
(function () {
  'use strict';
  var token = document.querySelector('meta[name="csrf-token"]').content;
  var entries = [], defaults = {}, selected = null, loadVersion = 0, searchTimer;
  var applyChannel = null;
  try { applyChannel = new BroadcastChannel('dirracuda.dorkbook.applied'); } catch (_) {}
  var labels = {shodan: 'Shodan', self_hosted: 'Self-hosted Search'};
  var focusProvider = new URLSearchParams(window.location.search).get('provider');
  function el(id) { return document.getElementById(id); }
  function status(message, error) {
    el('dorkbook-status').textContent = message;
    el('dorkbook-status').className = error ? 'status-error' : 'status-ok';
  }
  async function api(url, options) {
    var response = await fetch(url, Object.assign({credentials: 'same-origin', cache: 'no-store'}, options));
    var data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Request failed (' + response.status + ')');
    return data;
  }
  function destination(row) {
    return row.provider === 'self_hosted' ? 'self_hosted' : 'shodan:' + row.protocol;
  }
  function preview(row) {
    selected = row;
    el('preview-name').textContent = row ? row.nickname || 'Unnamed dork' : 'Select a dork';
    el('preview-destination').textContent = row ? labels[row.provider] + (row.protocol ? ' / ' + row.protocol : '') : '';
    el('preview-kind').textContent = row ? (row.row_kind === 'builtin' ? 'Built-in · read-only' : 'Custom dork') : '';
    el('preview-query').textContent = row ? row.query : '';
    el('copy-dork').disabled = !row;
    el('preview-notes').textContent = row ? row.notes || '' : '';
    el('apply-dork').disabled = !row;
    el('delete-dork').disabled = !row || row.row_kind === 'builtin';
    document.querySelectorAll('[data-entry-id]').forEach(function (tr) {
      tr.setAttribute('aria-selected', row && Number(tr.dataset.entryId) === row.entry_id ? 'true' : 'false');
    });
  }
  function render() {
    var tbody = el('dorkbook-tbody');
    tbody.replaceChildren();
    var search = el('dorkbook-search').value.trim().toLowerCase();
    var topic = el('dorkbook-topic').value;
    ['shodan', 'self_hosted'].forEach(function (provider) {
      var group = document.createElement('tr'), heading = document.createElement('th');
      group.id = 'provider-' + provider;
      heading.colSpan = 5; heading.scope = 'rowgroup'; heading.textContent = labels[provider];
      group.appendChild(heading); tbody.appendChild(group);
      var filtered = entries.filter(function (row) {
        return row.provider === provider && (!topic || row.topic === topic) &&
          [row.nickname, row.query, row.notes, row.topic].join(' ').toLowerCase().includes(search);
      });
      if (!filtered.length) {
        var empty = document.createElement('tr'), cell = document.createElement('td');
        cell.colSpan = 5; cell.textContent = 'No matching dorks.'; empty.appendChild(cell); tbody.appendChild(empty);
      }
      filtered.forEach(function (row) {
        var tr = document.createElement('tr'); tr.dataset.entryId = row.entry_id;
        tr.tabIndex = 0;
        [row.nickname || 'Unnamed dork', row.protocol || '—', row.topic, row.query,
          defaults[destination(row)] === row.query ? '✓ Default' : ''].forEach(function (value, index) {
          var td = document.createElement('td');
          td.dataset.label = ['Name', 'Protocol', 'Topic', 'Query', 'Default'][index];
          if (row.row_kind === 'builtin') {
            var italic = document.createElement('em'); italic.textContent = value; td.appendChild(italic);
          } else { td.textContent = value; }
          tr.appendChild(td);
        });
        tr.addEventListener('click', function () { preview(row); });
        tr.addEventListener('keydown', function (event) {
          if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); preview(row); }
        });
        tbody.appendChild(tr);
      });
    });
    preview(selected && entries.find(function (row) { return row.entry_id === selected.entry_id; }) || null);
  }
  async function load() {
    var version = ++loadVersion;
    try {
      var responses = await Promise.all([api('/api/dorkbook/entries'), api('/api/dorkbook/defaults')]);
      if (version !== loadVersion) return;
      entries = responses[0].entries; defaults = responses[1].defaults;
      var prior = el('dorkbook-topic').value;
      el('dorkbook-topic').replaceChildren(new Option('All topics', ''));
      Array.from(new Set(entries.map(function (row) { return row.topic; }))).sort().forEach(function (topic) {
        el('dorkbook-topic').add(new Option(topic, topic));
      });
      el('dorkbook-topic').value = prior;
      render();
      if (labels[focusProvider]) { el('provider-' + focusProvider).scrollIntoView({block: 'center'}); focusProvider = null; }
    } catch (error) { status(error.message, true); }
  }
  el('dorkbook-search').addEventListener('input', function () {
    clearTimeout(searchTimer); searchTimer = setTimeout(render, 150);
  });
  el('dorkbook-topic').addEventListener('change', render);
  el('add-provider').addEventListener('change', function () {
    var web = this.value === 'self_hosted'; el('add-protocol').disabled = web;
    el('add-query').maxLength = web ? 500 : 2000;
  });
  el('dorkbook-add-form').addEventListener('submit', async function (event) {
    event.preventDefault();
    var provider = el('add-provider').value;
    try {
      await api('/api/dorkbook/entries', {method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token},
        body: JSON.stringify({provider: provider, protocol: provider === 'shodan' ? el('add-protocol').value : null,
          topic: el('add-topic').value, nickname: el('add-nickname').value, query: el('add-query').value, notes: el('add-notes').value})});
      el('add-nickname').value = ''; el('add-query').value = ''; el('add-notes').value = '';
      status('Dork added.'); await load();
    } catch (error) { status(error.message, true); }
  });
  el('copy-dork').addEventListener('click', async function () {
    if (!selected) return;
    try {
      if (!navigator.clipboard) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(selected.query);
      status('Query copied.');
    } catch (_) {
      // HTTP deployments may lack Clipboard API access; leave an exact selection.
      var selection = window.getSelection();
      if (!selection) { status('Clipboard unavailable. Select and copy the query above.', true); return; }
      var range = document.createRange();
      range.selectNodeContents(el('preview-query'));
      selection.removeAllRanges(); selection.addRange(range);
      status('Clipboard unavailable. Query selected — press Ctrl+C (⌘C on Mac).');
    }
  });
  el('apply-dork').addEventListener('click', async function () {
    if (!selected) return;
    var button = this; button.disabled = true;
    try {
      var data = await api('/api/dorkbook/apply', {method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token}, body: JSON.stringify({entry_id: selected.entry_id})});
      // Explicit Apply events update the matching input in other open browser tabs.
      try { if (applyChannel) applyChannel.postMessage({destination: data.destination, query: data.query}); } catch (_) {}
      status('Saved default for ' + labels[data.provider] + (data.protocol ? ' / ' + data.protocol : '') + '.');
      await load();
    } catch (error) { status(error.message, true); }
    finally { button.disabled = !selected; }
  });
  el('delete-dork').addEventListener('click', async function () {
    if (!selected || selected.row_kind === 'builtin') return;
    if (!el('mute-delete-confirm').checked && !window.confirm('Delete this custom dork? Its applied search default will be kept.')) return;
    try {
      await api('/api/dorkbook/entries/' + selected.entry_id, {method: 'DELETE', headers: {'X-CSRF-Token': token}});
      preview(null); status('Dork deleted.'); await load();
    } catch (error) { status(error.message, true); }
  });
  window.addEventListener('focus', load);
  load();
}());
