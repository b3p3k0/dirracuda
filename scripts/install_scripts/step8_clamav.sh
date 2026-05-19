# Sourced by install.sh — not standalone.
# Step 8: Optional ClamAV install and tmpfs RAM quarantine setup.

# ── ClamAV config helpers ────────────────────────────────────────────────────

enable_clamav_for_fresh_config() {
    if [[ "$CONFIG_CREATED_THIS_INSTALL" != "true" ]]; then
        return 0
    fi
    if [[ ! -f "$DIRRACUDA_CONFIG" ]]; then
        warn "$DIRRACUDA_CONFIG not found — first app launch will auto-enable ClamAV if it is still detected."
        return 0
    fi
    if [[ ! -x venv/bin/python3 ]]; then
        warn "Virtual environment not found — cannot auto-enable ClamAV in config now."
        warn "First app launch will auto-enable ClamAV if it is still detected."
        return 0
    fi

    if DIRRACUDA_CONFIG_PATH="$DIRRACUDA_CONFIG" venv/bin/python3 - <<'PYEOF'
import json
import os
import pathlib
import sys

p = pathlib.Path(os.environ["DIRRACUDA_CONFIG_PATH"])
try:
    cfg = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        cfg = {}
    clamav = cfg.get("clamav")
    if not isinstance(clamav, dict):
        clamav = {}
        cfg["clamav"] = clamav
    clamav["enabled"] = True
    clamav["backend"] = "auto"
    p.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
except Exception as exc:
    print(f"Could not update config: {exc}", file=sys.stderr)
    sys.exit(1)
PYEOF
    then
        success "ClamAV integration auto-enabled in fresh config."
        info "You can disable it later in App Config → ClamAV Settings."
    else
        warn "Could not auto-enable ClamAV in config."
        warn "Enable manually in ~/.dirracuda/conf/config.json under: clamav → enabled: true"
    fi
}

report_existing_clamav_config_state() {
    if [[ "$CONFIG_CREATED_THIS_INSTALL" == "true" ]]; then
        return 0
    fi
    if [[ ! -f "$DIRRACUDA_CONFIG" ]]; then
        info "No config exists yet; first app launch will auto-enable ClamAV if it is still detected."
        return 0
    fi
    if [[ ! -x venv/bin/python3 ]]; then
        info "Existing config preserved. Check clamav.enabled in $DIRRACUDA_CONFIG to change integration state."
        return 0
    fi

    local clamav_state
    if clamav_state=$(DIRRACUDA_CONFIG_PATH="$DIRRACUDA_CONFIG" venv/bin/python3 - <<'PYEOF'
import json
import os
import pathlib
import sys

p = pathlib.Path(os.environ["DIRRACUDA_CONFIG_PATH"])
try:
    cfg = json.loads(p.read_text(encoding="utf-8"))
    clamav = cfg.get("clamav", {}) if isinstance(cfg, dict) else {}
    raw = clamav.get("enabled", False) if isinstance(clamav, dict) else False
    enabled = raw if isinstance(raw, bool) else str(raw).strip().lower() in {"1", "true", "yes", "on"}
    print("enabled" if enabled else "disabled")
except Exception as exc:
    print(f"unknown ({exc})")
    sys.exit(1)
PYEOF
    ); then
        info "Existing config preserved: ClamAV integration is currently $clamav_state."
    else
        warn "Existing config preserved, but ClamAV state could not be read."
    fi
}

# ── Step 8 ───────────────────────────────────────────────────────────────────

section "[Step 8 of 8]  Optional extras"

# ── ClamAV ────────────────────────────────────────────────────────────────────

echo "  ┌─ ClamAV antivirus support ──────────────────────────────────────────┐"
echo "  │                                                                       │"
echo "  │  ClamAV is a free, open-source antivirus scanner. When enabled,      │"
echo "  │  Dirracuda can scan files it downloads and quarantine infected ones   │"
echo "  │  before they reach your filesystem. Fresh installs auto-enable it     │"
echo "  │  when a scanner is detected; you can disable it in App Config.        │"
echo "  │                                                                       │"
echo "  └───────────────────────────────────────────────────────────────────────┘"
echo

CLAMAV_AVAILABLE=false
if command -v clamscan &>/dev/null || command -v clamdscan &>/dev/null; then
    CLAMAV_AVAILABLE=true
    success "ClamAV is already installed — skipping."
else
    if confirm "Install ClamAV now?" "n"; then
        info "Installing ClamAV..."
        sudo apt-get install -y clamav clamav-daemon
        echo
        success "ClamAV installed."
        info "Tip: run 'sudo freshclam' to download the latest virus definitions."
        if command -v clamscan &>/dev/null || command -v clamdscan &>/dev/null; then
            CLAMAV_AVAILABLE=true
        fi
    else
        info "Skipped. Install later with: sudo apt-get install clamav clamav-daemon"
    fi
fi

if [[ "$CLAMAV_AVAILABLE" == "true" ]]; then
    enable_clamav_for_fresh_config
    report_existing_clamav_config_state
else
    info "ClamAV integration remains disabled until a scanner is installed."
fi

echo

# ── tmpfs quarantine ─────────────────────────────────────────────────────────

echo "  ┌─ RAM-backed quarantine (tmpfs) ─────────────────────────────────────┐"
echo "  │                                                                       │"
echo "  │  When enabled, files downloaded by Dirracuda are stored in a         │"
echo "  │  pre-mounted RAM volume. They never touch your physical disk.         │"
echo "  │                                                                       │"
echo "  │  Dirracuda now uses detect-only tmpfs behavior and never mounts       │"
echo "  │  or unmounts as root. You can optionally add/update /etc/fstab here.  │"
echo "  │                                                                       │"
echo "  └───────────────────────────────────────────────────────────────────────┘"
echo

if confirm "Enable RAM-backed quarantine (tmpfs)?" "n"; then
    if [[ ! -f "$DIRRACUDA_CONFIG" ]]; then
        warn "$DIRRACUDA_CONFIG not found — cannot update config."
        warn "Enable manually in ~/.dirracuda/conf/config.json under: quarantine → use_tmpfs: true"
    elif [[ ! -x venv/bin/python3 ]]; then
        warn "Virtual environment not found — cannot update config."
        warn "Enable manually in ~/.dirracuda/conf/config.json under: quarantine → use_tmpfs: true"
    else
        if DIRRACUDA_CONFIG_PATH="$DIRRACUDA_CONFIG" venv/bin/python3 - <<'PYEOF'
import json, pathlib, sys
import os
p = pathlib.Path(os.environ['DIRRACUDA_CONFIG_PATH'])
try:
    cfg = json.loads(p.read_text())
    cfg.setdefault('quarantine', {})['use_tmpfs'] = True
    p.write_text(json.dumps(cfg, indent=2))
except Exception as e:
    print(f'Could not update config: {e}', file=sys.stderr)
    sys.exit(1)
PYEOF
        then
            success "tmpfs quarantine enabled in $DIRRACUDA_CONFIG."
            info "Dirracuda will only use tmpfs when a mount is already present."
        else
            warn "Could not update config automatically."
            warn "Enable manually in ~/.dirracuda/conf/config.json under: quarantine → use_tmpfs: true"
        fi
    fi

    echo
    if confirm "Add/update /etc/fstab for canonical tmpfs mountpoint ($DIRRACUDA_CANON_TMPFS_MP)?" "n"; then
        if ! command -v sudo &>/dev/null; then
            warn "sudo not found — cannot edit /etc/fstab automatically."
        else
            FSTAB_TS=$(date +%Y%m%d_%H%M%S)
            FSTAB_BACKUP="/etc/fstab.dirracuda.${FSTAB_TS}.bak"
            info "Backing up /etc/fstab to $FSTAB_BACKUP ..."
            if sudo cp /etc/fstab "$FSTAB_BACKUP"; then
                success "Backup created: $FSTAB_BACKUP"
            else
                warn "Failed to back up /etc/fstab. Skipping fstab update."
                FSTAB_BACKUP=""
            fi

            if [[ -n "${FSTAB_BACKUP:-}" ]]; then
                if sudo env \
                   DIRRACUDA_CANON_TMPFS_MP="$DIRRACUDA_CANON_TMPFS_MP" \
                   DIRRACUDA_LEGACY_TMPFS_MP="$DIRRACUDA_LEGACY_TMPFS_MP" \
                   python3 - <<'PYEOF'
import os
import pathlib
import sys

fstab = pathlib.Path("/etc/fstab")
canon = os.environ["DIRRACUDA_CANON_TMPFS_MP"]
legacy = os.environ["DIRRACUDA_LEGACY_TMPFS_MP"]
canonical_line = f"tmpfs  {canon}  tmpfs  noexec,nosuid,nodev,size=512M,noswap  0  0"

try:
    lines = fstab.read_text(encoding="utf-8").splitlines()
except Exception as exc:
    print(f"Could not read /etc/fstab: {exc}", file=sys.stderr)
    raise SystemExit(1)

out = []
legacy_commented = 0
canonical_exists = False

for raw in lines:
    line = raw.rstrip("\n")
    stripped = line.strip()
    if stripped and not stripped.startswith("#"):
        parts = stripped.split()
        if len(parts) >= 3:
            mountpoint = parts[1]
            fstype = parts[2]
            if mountpoint == canon and fstype == "tmpfs":
                canonical_exists = True
            if mountpoint == legacy and fstype == "tmpfs":
                out.append(f"# dirracuda-migrated-legacy {line}")
                legacy_commented += 1
                continue
    out.append(line)

canonical_added = False
if not canonical_exists:
    if out and out[-1].strip():
        out.append("")
    out.append("# Dirracuda tmpfs quarantine (canonical)")
    out.append(canonical_line)
    canonical_added = True

try:
    fstab.write_text("\n".join(out) + "\n", encoding="utf-8")
except Exception as exc:
    print(f"Could not write /etc/fstab: {exc}", file=sys.stderr)
    raise SystemExit(1)

print(f"legacy_commented={legacy_commented} canonical_added={int(canonical_added)}")
PYEOF
                then
                    success "Updated /etc/fstab for canonical tmpfs mountpoint."
                    info "Legacy mountpoint (if present) was commented and canonical entry ensured."
                else
                    warn "Automatic /etc/fstab update failed."
                    if [[ -n "${FSTAB_BACKUP:-}" ]]; then
                        warn "Restore backup with: sudo cp \"$FSTAB_BACKUP\" /etc/fstab"
                    fi
                fi
            fi

            if confirm "Run sudo mount -a now to apply fstab changes?" "n"; then
                mkdir -p "$DIRRACUDA_DATA_DIR"
                if sudo mkdir -p "$DIRRACUDA_CANON_TMPFS_MP" \
                   && sudo chown "$(id -u):$(id -g)" "$DIRRACUDA_DATA_DIR" \
                   && sudo mount -a; then
                    success "mount -a completed."
                    if mount | grep -F "$DIRRACUDA_CANON_TMPFS_MP" >/dev/null 2>&1; then
                        success "Canonical tmpfs mount is active at $DIRRACUDA_CANON_TMPFS_MP"
                    else
                        warn "mount -a succeeded, but canonical tmpfs mount was not detected."
                    fi
                else
                    warn "mount -a failed. Review /etc/fstab and restore backup if needed."
                    if [[ -n "${FSTAB_BACKUP:-}" ]]; then
                        warn "Backup path: $FSTAB_BACKUP"
                    fi
                fi
            else
                info "Skipped mount -a. Apply later with: sudo mount -a"
            fi
        fi
    fi
else
    info "Skipped. Downloads will use a regular directory on disk."
fi

pause
