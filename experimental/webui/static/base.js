(function initPrefsConsentBanner() {
  var banner = document.getElementById('prefs-consent-banner');
  var yesBtn = document.getElementById('prefs-consent-yes');
  var noBtn = document.getElementById('prefs-consent-no');
  if (!banner || !yesBtn || !noBtn) return;

  function hideBanner() {
    banner.hidden = true;
    banner.classList.add('hidden');
  }

  function showBanner() {
    banner.hidden = false;
    banner.classList.remove('hidden');
  }

  if (!window.DirracudaPrefs || !window.DirracudaPrefs.isAvailable()) {
    hideBanner();
    return;
  }

  // Bind handlers regardless of prior consent so manual toggles always respond.
  yesBtn.addEventListener('click', function() {
    window.DirracudaPrefs.setConsent('allow');
    hideBanner();
  });
  noBtn.addEventListener('click', function() {
    window.DirracudaPrefs.setConsent('deny');
    hideBanner();
  });

  if (window.DirracudaPrefs.getConsent() !== null) {
    hideBanner();
    return;
  }
  showBanner();
})();

var logoutBtn = document.getElementById('nav-logout-btn');
if (logoutBtn) {
  logoutBtn.addEventListener('click', async function() {
  var token = document.querySelector('meta[name="csrf-token"]').content;
  try {
    await fetch('/logout', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token},
      body: '{}'
    });
  } catch (_) {}
  window.location.href = '/login';
  });
}
