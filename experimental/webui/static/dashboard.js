(function() {
  var statusEl = document.getElementById('shodan-balance-status');
  var refreshBtn = document.getElementById('shodan-balance-refresh');

  function toStatusText(payload) {
    if (!payload || typeof payload !== 'object') {
      return "✔ Shodan API key configured <balance unavailable: unknown>";
    }
    if (payload.state === 'ok') {
      return "✔ Shodan API key configured <query credits: " + String(payload.query_credits) + ">";
    }
    if (payload.state === 'no_key') {
      return "✖ Shodan API key configured <none>";
    }
    var reason = String(payload.reason || 'unknown');
    return "✔ Shodan API key configured <balance unavailable: " + reason + ">";
  }

  function loadShodanBalance(force) {
    if (!statusEl) return;
    statusEl.textContent = "Checking...";
    var url = '/api/dashboard/shodan-balance';
    if (force) {
      url += '?force=true';
    }
    fetch(url)
      .then(function(resp) {
        return resp.json().then(function(data) {
          if (!resp.ok) {
            statusEl.textContent = "✔ Shodan API key configured <balance unavailable: unknown>";
            return;
          }
          statusEl.textContent = toStatusText(data);
        });
      })
      .catch(function() {
        statusEl.textContent = "✔ Shodan API key configured <balance unavailable: network>";
      });
  }

  if (refreshBtn) {
    refreshBtn.addEventListener('click', function() {
      loadShodanBalance(true);
    });
  }
  loadShodanBalance(false);
})();

(function() {
  var activeInfo = document.getElementById('active-info');
  var tid = activeInfo ? activeInfo.dataset.taskId : '';
  if (!tid) return;
  var terminal = {'done': 1, 'failed': 1, 'cancelled': 1};
  function poll() {
    fetch('/api/scans/' + encodeURIComponent(tid))
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(data) {
        if (!data) return;
        var el = document.getElementById('active-status');
        if (el) el.textContent = data.status;
        var pr = document.getElementById('active-progress');
        if (pr && data.progress_message) pr.textContent = data.progress_message;
        if (!terminal[data.status]) {
          setTimeout(poll, 3000);
        }
      })
      .catch(function() {});
  }
  setTimeout(poll, 3000);
})();
