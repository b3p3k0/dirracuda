"use strict";

(function () {
  const csrfToken = () =>
    (document.querySelector('meta[name="csrf-token"]') || {}).content || "";

  const escHtml = (s) =>
    String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  function setStatus(el, msg, cls) {
    if (!el) return;
    el.textContent = msg;
    el.className = cls || "";
  }

  let _searchDebounce = null;

  // ---------------------------------------------------------------------------
  // Status + keys fetch
  // ---------------------------------------------------------------------------

  async function fetchStatus() {
    try {
      const r = await fetch("/api/keymaster/status");
      if (!r.ok) return;
      const data = await r.json();
      applyStatusToUI(data);
    } catch (_) {
      // non-fatal; UI defaults to locked state
    }
  }

  function applyStatusToUI(data) {
    const unlockSection = document.getElementById("km-unlock-section");
    const unlockedSection = document.getElementById("km-unlocked-section");
    const addBtn = document.getElementById("km-add-btn");

    const isUnlocked = data.is_unlocked === true;
    const secureMode = data.secure_mode === true;
    const passConfigured = data.passphrase_configured === true;

    if (isUnlocked || !secureMode) {
      if (unlockSection) unlockSection.hidden = true;
      if (unlockedSection) unlockedSection.hidden = false;
    } else {
      if (unlockSection) unlockSection.hidden = false;
      if (unlockedSection) unlockedSection.hidden = true;
      const label = document.getElementById("km-lock-label");
      if (label && secureMode && !passConfigured) {
        label.textContent =
          "Secure mode is enabled but no passphrase is configured. Set a passphrase in the desktop app first.";
      }
    }

    if (addBtn) addBtn.disabled = !isUnlocked && secureMode;
  }

  async function fetchKeys(search) {
    const q = encodeURIComponent(search || "");
    const url = `/api/keymaster/keys?provider=SHODAN&search=${q}`;
    const statusEl = document.getElementById("km-keys-status");
    try {
      const r = await fetch(url);
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        setStatus(statusEl, body.error || "Failed to load keys.", "status-error");
        return;
      }
      const data = await r.json();
      setStatus(statusEl, "", "");
      renderKeysTable(data.keys || [], data.is_unlocked === true);
      applyStatusToUI(data);
    } catch (_) {
      setStatus(statusEl, "Network error loading keys.", "status-error");
    }
  }

  function renderKeysTable(keys, isUnlocked) {
    const tbody = document.getElementById("km-keys-tbody");
    const emptyRow = document.getElementById("km-empty-row");
    if (!tbody) return;

    // Remove old data rows (keep #km-empty-row sentinel)
    Array.from(tbody.querySelectorAll("tr[data-key-id]")).forEach((r) =>
      r.remove()
    );

    if (keys.length === 0) {
      if (emptyRow) emptyRow.hidden = false;
      return;
    }
    if (emptyRow) emptyRow.hidden = true;

    keys.forEach((k) => {
      const tr = document.createElement("tr");
      tr.dataset.keyId = k.key_id;
      tr.dataset.label = k.label || "";
      tr.dataset.notes = k.notes || "";

      const lastUsed = k.last_used_at
        ? escHtml(k.last_used_at.replace("T", " "))
        : "—";
      const keyDisplay = escHtml(k.api_key_masked || "");

      tr.innerHTML =
        `<td>${escHtml(k.label)}</td>` +
        `<td class="key-preview"><code>${keyDisplay}</code></td>` +
        `<td>${escHtml(k.notes)}</td>` +
        `<td>${lastUsed}</td>` +
        `<td class="actions">` +
        `<button type="button" class="btn-edit" data-key-id="${k.key_id}">Edit</button>` +
        `<button type="button" class="btn-delete" data-key-id="${k.key_id}">Delete</button>` +
        `<button type="button" class="btn-apply" data-key-id="${k.key_id}"${isUnlocked ? "" : " disabled"}>Apply</button>` +
        `</td>`;

      tbody.appendChild(tr);
    });
  }

  // ---------------------------------------------------------------------------
  // Unlock form
  // ---------------------------------------------------------------------------

  function initUnlockForm() {
    const form = document.getElementById("km-unlock-form");
    if (!form) return;
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const statusEl = document.getElementById("km-unlock-status");
      const passInput = document.getElementById("km-passphrase");
      const btn = document.getElementById("km-unlock-btn");

      setStatus(statusEl, "Unlocking…", "status-neutral");
      btn.disabled = true;

      try {
        const r = await fetch("/api/keymaster/unlock", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": csrfToken(),
          },
          body: JSON.stringify({ passphrase: passInput.value }),
        });
        const body = await r.json().catch(() => ({}));
        if (r.ok) {
          setStatus(statusEl, "", "");
          passInput.value = "";
          await fetchStatus();
          await fetchKeys();
        } else {
          setStatus(
            statusEl,
            body.error || "Unlock failed.",
            "status-error"
          );
        }
      } catch (_) {
        setStatus(statusEl, "Network error.", "status-error");
      } finally {
        btn.disabled = false;
      }
    });
  }

  // ---------------------------------------------------------------------------
  // Modal (add / edit)
  // ---------------------------------------------------------------------------

  function openModal(row) {
    const overlay = document.getElementById("km-modal-overlay");
    const title = document.getElementById("km-modal-title");
    const keyIdInput = document.getElementById("km-modal-key-id");
    const labelInput = document.getElementById("km-modal-label");
    const apiKeyInput = document.getElementById("km-modal-api-key");
    const notesInput = document.getElementById("km-modal-notes");
    const statusEl = document.getElementById("km-modal-status");

    if (row) {
      title.textContent = "Edit Key";
      keyIdInput.value = row.dataset.keyId || "";
      labelInput.value = row.dataset.label || "";
      apiKeyInput.value = "";
      notesInput.value = row.dataset.notes || "";
    } else {
      title.textContent = "Add Key";
      keyIdInput.value = "";
      labelInput.value = "";
      apiKeyInput.value = "";
      notesInput.value = "";
    }
    setStatus(statusEl, "", "");
    if (overlay) overlay.hidden = false;
    if (labelInput) labelInput.focus();
  }

  function closeModal() {
    const overlay = document.getElementById("km-modal-overlay");
    if (overlay) overlay.hidden = true;
  }

  function initModal() {
    const form = document.getElementById("km-modal-form");
    const cancelBtn = document.getElementById("km-modal-cancel");
    if (!form) return;

    cancelBtn && cancelBtn.addEventListener("click", closeModal);

    const overlay = document.getElementById("km-modal-overlay");
    overlay &&
      overlay.addEventListener("click", (e) => {
        if (e.target === overlay) closeModal();
      });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const statusEl = document.getElementById("km-modal-status");
      const saveBtn = document.getElementById("km-modal-save");
      const keyId = document.getElementById("km-modal-key-id").value.trim();
      const label = document.getElementById("km-modal-label").value.trim();
      const apiKey = document.getElementById("km-modal-api-key").value.trim();
      const notes = document.getElementById("km-modal-notes").value;

      if (!label) {
        setStatus(statusEl, "Label is required.", "status-error");
        return;
      }
      if (!keyId && !apiKey) {
        setStatus(statusEl, "API Key is required when adding.", "status-error");
        return;
      }

      setStatus(statusEl, "Saving…", "status-neutral");
      saveBtn.disabled = true;

      const isEdit = !!keyId;
      const url = isEdit
        ? `/api/keymaster/keys/${keyId}`
        : "/api/keymaster/keys";
      const method = isEdit ? "PATCH" : "POST";
      const payload = isEdit
        ? { label, api_key: apiKey, notes }
        : { provider: "SHODAN", label, api_key: apiKey, notes };

      try {
        const r = await fetch(url, {
          method,
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": csrfToken(),
          },
          body: JSON.stringify(payload),
        });
        const body = await r.json().catch(() => ({}));
        if (r.ok || r.status === 201) {
          closeModal();
          await fetchKeys(document.getElementById("km-search")?.value || "");
        } else {
          setStatus(
            statusEl,
            body.error || "Save failed.",
            "status-error"
          );
        }
      } catch (_) {
        setStatus(statusEl, "Network error.", "status-error");
      } finally {
        saveBtn.disabled = false;
      }
    });
  }

  // ---------------------------------------------------------------------------
  // Table actions (delete / apply) via event delegation
  // ---------------------------------------------------------------------------

  function initTableActions() {
    const tbody = document.getElementById("km-keys-tbody");
    const statusEl = document.getElementById("km-keys-status");
    const addBtn = document.getElementById("km-add-btn");
    if (!tbody) return;

    addBtn &&
      addBtn.addEventListener("click", () => openModal(null));

    tbody.addEventListener("click", async (e) => {
      const btn = e.target.closest("button");
      if (!btn) return;

      const keyId = btn.dataset.keyId;
      if (!keyId) return;

      if (btn.classList.contains("btn-edit")) {
        const row = tbody.querySelector(`tr[data-key-id="${keyId}"]`);
        openModal(row);
        return;
      }

      if (btn.classList.contains("btn-delete")) {
        if (!confirm("Delete this key? This cannot be undone.")) return;
        btn.disabled = true;
        try {
          const r = await fetch(`/api/keymaster/keys/${keyId}`, {
            method: "DELETE",
            headers: { "X-CSRF-Token": csrfToken() },
          });
          const body = await r.json().catch(() => ({}));
          if (r.ok) {
            await fetchKeys(document.getElementById("km-search")?.value || "");
          } else {
            setStatus(
              statusEl,
              body.error || "Delete failed.",
              "status-error"
            );
            btn.disabled = false;
          }
        } catch (_) {
          setStatus(statusEl, "Network error.", "status-error");
          btn.disabled = false;
        }
        return;
      }

      if (btn.classList.contains("btn-apply")) {
        btn.disabled = true;
        setStatus(statusEl, "", "");
        try {
          const r = await fetch("/api/keymaster/apply", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRF-Token": csrfToken(),
            },
            body: JSON.stringify({ key_id: parseInt(keyId, 10) }),
          });
          const body = await r.json().catch(() => ({}));
          if (r.ok) {
            setStatus(statusEl, "Key applied to active config.", "status-ok");
            await fetchKeys(document.getElementById("km-search")?.value || "");
          } else {
            setStatus(
              statusEl,
              body.error || "Apply failed.",
              "status-error"
            );
            btn.disabled = false;
          }
        } catch (_) {
          setStatus(statusEl, "Network error.", "status-error");
          btn.disabled = false;
        }
      }
    });
  }

  // ---------------------------------------------------------------------------
  // Search
  // ---------------------------------------------------------------------------

  function initSearch() {
    const input = document.getElementById("km-search");
    if (!input) return;
    input.addEventListener("input", () => {
      clearTimeout(_searchDebounce);
      _searchDebounce = setTimeout(() => fetchKeys(input.value), 300);
    });
  }

  // ---------------------------------------------------------------------------
  // Init
  // ---------------------------------------------------------------------------

  document.addEventListener("DOMContentLoaded", async () => {
    initUnlockForm();
    initModal();
    initTableActions();
    initSearch();
    await fetchStatus();
    await fetchKeys();
  });
})();
