var exportBtn = document.getElementById('export-btn');
var exportStatus = document.getElementById('export-status');
var csrfMeta = document.querySelector('meta[name="csrf-token"]');

function setExportStatus(message, cls) {
  if (!exportStatus) return;
  exportStatus.textContent = message;
  exportStatus.className = cls ? cls : '';
}

if (exportBtn && exportStatus && csrfMeta) {
  exportBtn.addEventListener('click', async function() {
    var token = csrfMeta.content;
    setExportStatus('Exporting...', 'status-text');
    try {
      var resp = await fetch('/api/export', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': token
        },
        body: '{}'
      });

      var data = await resp.json();
      if (resp.ok && data.filename) {
        setExportStatus('Export ready: ' + data.filename, 'status-text ok');
        window.location.href = '/api/export/' + encodeURIComponent(data.filename);
        return;
      }
      setExportStatus('Export failed: ' + (data.error || resp.status), 'status-text error');
    } catch (_err) {
      setExportStatus('Export failed: network error', 'status-text error');
    }
  });
}
