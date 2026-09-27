/* Query defaults stay shared; manual edits stay local to the current run. */
(function () {
  'use strict';
  var fields = Array.from(document.querySelectorAll('[data-dork-destination]'));
  var baselines = new Map(fields.map(function (field) { return [field, field.value]; }));
  var generation = 0;
  async function refresh() {
    var version = ++generation;
    try {
      var response = await fetch('/api/dorkbook/defaults', {credentials: 'same-origin', cache: 'no-store'});
      if (!response.ok) return;
      var payload = await response.json();
      if (version !== generation) return;
      fields.forEach(function (field) {
        var latest = payload.defaults[field.dataset.dorkDestination];
        if (typeof latest !== 'string') return;
        if (field.value === baselines.get(field)) field.value = latest;
        baselines.set(field, latest);
      });
    } catch (_) { /* Leave typed text usable if config refresh is unavailable. */ }
  }
  window.addEventListener('focus', refresh);
  try {
    var applyChannel = new BroadcastChannel('dirracuda.dorkbook.applied');
    applyChannel.addEventListener('message', function (event) {
      var applied = event.data;
      if (!applied || typeof applied.query !== 'string') return;
      if (!fields.some(function (field) { return field.dataset.dorkDestination === applied.destination; })) return;
      generation += 1;
      fields.forEach(function (field) {
        if (field.dataset.dorkDestination === applied.destination) {
          field.value = applied.query; baselines.set(field, applied.query);
        }
      });
    });
  } catch (_) { /* Older browsers refresh clean inputs on focus; no query is stored in the browser. */ }
  refresh();
}());
