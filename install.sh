#!/usr/bin/env bash
set -euo pipefail

# ──────────────────────────────────────────────────────────────────────────────
# Terminal helpers
# ──────────────────────────────────────────────────────────────────────────────

BOLD=$(tput bold 2>/dev/null) || BOLD=''
RESET=$(tput sgr0 2>/dev/null) || RESET=''
GREEN=$(tput setaf 2 2>/dev/null) || GREEN=''
YELLOW=$(tput setaf 3 2>/dev/null) || YELLOW=''
RED=$(tput setaf 1 2>/dev/null) || RED=''
CYAN=$(tput setaf 6 2>/dev/null) || CYAN=''

info()    { printf '%s▶%s %s\n' "$CYAN"   "$RESET" "$*"; }
success() { printf '%s✔%s %s\n' "$GREEN"  "$RESET" "$*"; }
warn()    { printf '%s!%s %s\n' "$YELLOW" "$RESET" "$*"; }
die()     { printf '\n%s✘ ERROR:%s %s\n\n' "$RED" "$RESET" "$*" >&2; exit 1; }

section() {
    printf '\n%s%s%s\n' "$BOLD" "$*" "$RESET"
    printf '%.0s─' {1..60}
    printf '\n\n'
}

pause() {
    read -rp "  Press Enter to continue..." _ || true
    echo
}

confirm() {
    local prompt="$1"
    local default="${2:-y}"
    local yn_str reply
    [[ "$default" == "y" ]] && yn_str="[Y/n]" || yn_str="[y/N]"
    while true; do
        read -rp "  $prompt $yn_str " reply || reply="$default"
        reply="${reply:-$default}"
        case "$reply" in
            [Yy]* ) return 0 ;;
            [Nn]* ) return 1 ;;
            * ) echo "  Please answer y or n." ;;
        esac
    done
}

# ──────────────────────────────────────────────────────────────────────────────
# Guard: must run from project root
# ──────────────────────────────────────────────────────────────────────────────

if [[ ! -f requirements.txt ]] || [[ ! -d conf ]]; then
    die "Run this script from the Dirracuda project root directory."
fi

DIRRACUDA_HOME="$HOME/.dirracuda"
DIRRACUDA_CONF_DIR="$DIRRACUDA_HOME/conf"
DIRRACUDA_DATA_DIR="$DIRRACUDA_HOME/data"
DIRRACUDA_CONFIG="$DIRRACUDA_CONF_DIR/config.json"
DIRRACUDA_DB_PATH="$DIRRACUDA_DATA_DIR/dirracuda.db"
DIRRACUDA_CANON_TMPFS_MP="$DIRRACUDA_HOME/data/tmpfs_quarantine"
DIRRACUDA_LEGACY_TMPFS_MP="$DIRRACUDA_HOME/quarantine_tmpfs"

# ──────────────────────────────────────────────────────────────────────────────
# Welcome
# ──────────────────────────────────────────────────────────────────────────────

clear
printf '%s' "$BOLD"
cat << 'EOF'
╔══════════════════════════════════════════════════════════════╗
║                  Dirracuda — Installer                       ║
╚══════════════════════════════════════════════════════════════╝
EOF
printf '%s\n' "$RESET"

echo "  This installer will walk you through setting up Dirracuda."
echo "  You will be asked to confirm before anything is changed."
echo
echo "  Steps:"
echo "    [1] Check your Python version"
echo "    [2] Install required system libraries  (sudo required)"
echo "    [3] Create a Python virtual environment and install dependencies"
echo "    [4] Create a configuration file"
echo "    [5] Set launcher permissions"
echo "    [6] Configure your Shodan API key       (optional)"
echo "    [7] Import an existing database         (optional)"
echo "    [8] ClamAV antivirus / RAM quarantine   (optional)"
echo

pause

# ──────────────────────────────────────────────────────────────────────────────
# Step 1: Python version
# ──────────────────────────────────────────────────────────────────────────────

section "[Step 1 of 8]  Python version check"

echo "  Dirracuda requires Python 3.8 or newer. Python 3.10+ is recommended."
echo

if ! command -v python3 &>/dev/null; then
    die "python3 not found. Please install Python 3.8+ and re-run this script."
fi

PY_FULL=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
PY_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")

echo "  Detected: Python $PY_FULL"
echo

if (( PY_MAJOR < 3 || (PY_MAJOR == 3 && PY_MINOR < 8) )); then
    die "Python $PY_FULL is below the minimum required version (3.8). Please upgrade."
elif (( PY_MAJOR == 3 && PY_MINOR < 10 )); then
    warn "Python $PY_FULL meets the minimum, but 3.10+ is recommended for best compatibility."
else
    success "Python $PY_FULL — OK"
fi

pause

# ──────────────────────────────────────────────────────────────────────────────
# Step 2: System dependencies
# ──────────────────────────────────────────────────────────────────────────────

section "[Step 2 of 8]  System dependencies"

echo "  The following system packages are required to build and run Dirracuda:"
echo
echo "    Python build tools and GUI framework:"
echo "      python3-dev  python3-tk  python3-venv"
echo
echo "    Network authentication libraries (Kerberos, SSL, FFI):"
echo "      libkrb5-dev  libssl-dev  libffi-dev"
echo
echo "    Image libraries (GUI icons and thumbnails):"
echo "      libjpeg-dev  zlib1g-dev  libtiff-dev"
echo
echo "  This step requires sudo (administrator) access."
echo

if ! command -v sudo &>/dev/null; then
    die "sudo not found. Please run this script as a user with sudo privileges."
fi

if confirm "Install these system packages now?"; then
    info "Updating package list..."
    sudo apt-get update -qq
    info "Installing packages..."
    sudo apt-get install -y \
        python3-dev python3-tk python3-venv \
        libkrb5-dev libssl-dev libffi-dev \
        libjpeg-dev zlib1g-dev libtiff-dev
    echo
    success "System packages installed."
else
    warn "Skipped. Dirracuda may not work correctly without these packages."
fi

pause

# ──────────────────────────────────────────────────────────────────────────────
# Step 3: Python virtual environment
# ──────────────────────────────────────────────────────────────────────────────

section "[Step 3 of 8]  Python virtual environment"

echo "  A virtual environment keeps Dirracuda's Python packages isolated from"
echo "  the rest of your system, so they don't interfere with other software."
echo

VENV_READY=false

if [[ -d venv ]]; then
    warn "A virtual environment already exists at ./venv"
    if confirm "Recreate it from scratch? (the current venv will be removed)" "n"; then
        rm -rf venv
        info "Removed existing virtual environment."
    else
        success "Using existing virtual environment."
        info "Re-running dependency install to ensure everything is up to date..."
        venv/bin/pip install --upgrade pip -q
        venv/bin/pip install -r requirements.txt -q
        success "Dependencies are up to date."
        VENV_READY=true
    fi
fi

if [[ "$VENV_READY" != "true" ]]; then
    if confirm "Create a virtual environment and install Python packages?"; then
        info "Creating virtual environment..."
        python3 -m venv venv
        info "Upgrading pip..."
        venv/bin/pip install --upgrade pip -q
        info "Installing dependencies — this may take a minute..."
        venv/bin/pip install -r requirements.txt
        echo
        success "Virtual environment created and dependencies installed."
    else
        warn "Skipped. Dirracuda will not run without its Python dependencies."
    fi
fi

pause

# ──────────────────────────────────────────────────────────────────────────────
# Step 4: Config file
# ──────────────────────────────────────────────────────────────────────────────

section "[Step 4 of 8]  Configuration file"

echo "  Dirracuda reads settings from ~/.dirracuda/conf/config.json."
echo "  We'll create one from the bundled example. You can edit it"
echo "  at any time — the defaults are fine for getting started."
echo

if [[ -f "$DIRRACUDA_CONFIG" ]]; then
    success "$DIRRACUDA_CONFIG already exists — skipping (will not overwrite)."
else
    if confirm "Create $DIRRACUDA_CONFIG from the example template?"; then
        mkdir -p "$DIRRACUDA_CONF_DIR"
        cp conf/config.json.example "$DIRRACUDA_CONFIG"
        success "$DIRRACUDA_CONFIG created."
    else
        warn "Skipped. Dirracuda will create a default config on first run at:"
        warn "  $DIRRACUDA_CONFIG"
        warn "Note: the Shodan API key step (step 6) requires this file to exist."
    fi
fi

DIRRACUDA_EXCLUSION_FILE="$DIRRACUDA_CONF_DIR/exclusion_list.json"
DIRRACUDA_RANSOMWARE_FILE="$DIRRACUDA_CONF_DIR/ransomware_indicators.json"

for _src_dst in \
    "conf/exclusion_list.json:$DIRRACUDA_EXCLUSION_FILE" \
    "conf/ransomware_indicators.json:$DIRRACUDA_RANSOMWARE_FILE"; do
    _src="${_src_dst%%:*}"
    _dst="${_src_dst##*:}"
    if [[ -f "$_dst" ]]; then
        success "$_dst already exists — skipping."
    elif [[ -f "$_src" ]]; then
        mkdir -p "$DIRRACUDA_CONF_DIR"
        cp "$_src" "$_dst"
        success "$(basename "$_dst") copied to $DIRRACUDA_CONF_DIR."
    else
        warn "Source file $PWD/$_src not found — skipping $(basename "$_dst")."
    fi
done
unset _src_dst _src _dst

pause

# ──────────────────────────────────────────────────────────────────────────────
# Step 5: Launcher permissions
# ──────────────────────────────────────────────────────────────────────────────

section "[Step 5 of 8]  Launcher permissions"

echo "  The ./dirracuda launcher needs execute permission to run."
echo "  This is sometimes missing after downloading or cloning the project."
echo

if [[ -x dirracuda ]]; then
    success "./dirracuda is already executable — skipping."
else
    if confirm "Set execute permission on ./dirracuda?"; then
        chmod +x dirracuda
        success "Permission set."
    else
        warn "Skipped. If the app won't start, run: chmod +x dirracuda"
    fi
fi

pause

# ──────────────────────────────────────────────────────────────────────────────
# Step 6: Shodan API key (optional)
# ──────────────────────────────────────────────────────────────────────────────

section "[Step 6 of 8]  Shodan API key  (optional)"

echo "  Dirracuda uses Shodan to search for publicly exposed network hosts."
echo "  You'll need a free Shodan account and API key to use these features."
echo
echo "  You can sign up and find your key at:"
echo "    https://account.shodan.io"
echo
echo "  If you don't have a key yet, press Enter to skip."
echo "  You can add it later in ~/.dirracuda/conf/config.json under:  shodan → api_key"
echo

SHODAN_KEY=''

if confirm "Would you like to enter a Shodan API key now?" "n"; then
    echo
    echo "  Hold on — before you paste anything..."
    echo
    if confirm "  You're about to hand a secret key to a shell script. Did you read the source to make sure we're not doing anything sketchy with it?"; then
        echo
        success "Good. Can't be too careful — especially with a security tool."
        echo
    else
        echo
        if confirm "  Fair enough. Want to open the script in less so you can review it?" "n"; then
            echo
            info "Opening install.sh in less — press q to quit and return to the installer."
            echo
            pause
            less "$0"
            echo
            success "Welcome back. Let's continue."
            echo
        else
            echo
            warn "Well, your call. We'll save the key as-is — but do audit your tools, yeah?"
            echo
        fi
    fi
    read -rp "  Enter your Shodan API key: " SHODAN_KEY || SHODAN_KEY=''
    SHODAN_KEY="${SHODAN_KEY//[[:space:]]/}"
fi

if [[ -n "$SHODAN_KEY" ]]; then
    if [[ ! -f "$DIRRACUDA_CONFIG" ]]; then
        warn "$DIRRACUDA_CONFIG not found — cannot save key."
        warn "Add it manually under: shodan → api_key"
    elif [[ ! -x venv/bin/python3 ]]; then
        warn "Virtual environment not found — cannot save key."
        warn "Add it manually to ~/.dirracuda/conf/config.json under: shodan → api_key"
    else
        if SHODAN_KEY_VAL="$SHODAN_KEY" DIRRACUDA_CONFIG_PATH="$DIRRACUDA_CONFIG" venv/bin/python3 - <<'PYEOF'
import json, pathlib, sys, os
key = os.environ['SHODAN_KEY_VAL']
p = pathlib.Path(os.environ['DIRRACUDA_CONFIG_PATH'])
try:
    cfg = json.loads(p.read_text())
    cfg.setdefault('shodan', {})['api_key'] = key
    p.write_text(json.dumps(cfg, indent=2))
except Exception as e:
    print(f'Could not save API key: {e}', file=sys.stderr)
    sys.exit(1)
PYEOF
        then
            success "Shodan API key saved to $DIRRACUDA_CONFIG."
        else
            warn "Could not save API key automatically."
            warn "Add it manually to ~/.dirracuda/conf/config.json under: shodan → api_key"
        fi
    fi
else
    info "Skipped. Add your key later in ~/.dirracuda/conf/config.json → shodan → api_key"
fi

pause

# ──────────────────────────────────────────────────────────────────────────────
# Step 7: Import existing database (optional)
# ──────────────────────────────────────────────────────────────────────────────

section "[Step 7 of 8]  Import existing database  (optional)"

echo "  If you have a dirracuda.db from a previous installation, you can"
echo "  import it into the canonical home data path to preserve scan history."
echo "  Target: $DIRRACUDA_DB_PATH"
echo
echo "  Leave blank to skip — a fresh database will be created on first run."
echo

read -rp "  Path to existing dirracuda.db (or press Enter to skip): " DB_IMPORT_PATH || DB_IMPORT_PATH=''

if [[ -n "$DB_IMPORT_PATH" ]]; then
    DB_IMPORT_PATH="${DB_IMPORT_PATH/#\~/$HOME}"

    if [[ ! -f "$DB_IMPORT_PATH" ]]; then
        warn "File not found: $DB_IMPORT_PATH — skipping."
    elif [[ ! -x venv/bin/python3 ]]; then
        warn "Virtual environment not found — cannot validate the file."
        warn "Copy it manually to: $DIRRACUDA_DB_PATH"
    else
        DB_VALID=false
        if DB_FILE_PATH="$DB_IMPORT_PATH" venv/bin/python3 - <<'PYEOF'
import sys, os
path = os.environ['DB_FILE_PATH']
try:
    with open(path, 'rb') as f:
        header = f.read(16)
    if not header.startswith(b'SQLite format 3'):
        print(f'  File does not appear to be a valid SQLite database.')
        sys.exit(1)
except Exception as e:
    print(f'  Could not read file: {e}')
    sys.exit(1)
PYEOF
        then
            DB_VALID=true
        fi

        if [[ "$DB_VALID" == "true" ]]; then
            mkdir -p "$DIRRACUDA_DATA_DIR"
            if [[ -f "$DIRRACUDA_DB_PATH" ]]; then
                warn "A database already exists at $DIRRACUDA_DB_PATH."
                if confirm "Overwrite it with the imported file?" "n"; then
                    DB_BACKUP_PATH="$DIRRACUDA_DB_PATH.pre_import_$(date +%Y%m%d_%H%M%S).bak"
                    if cp "$DIRRACUDA_DB_PATH" "$DB_BACKUP_PATH"; then
                        info "Existing database backed up to: $DB_BACKUP_PATH"
                        cp "$DB_IMPORT_PATH" "$DIRRACUDA_DB_PATH"
                        success "Database imported from: $DB_IMPORT_PATH"
                    else
                        warn "Could not create backup at $DB_BACKUP_PATH — keeping existing database."
                        info "Skipped import to avoid data loss."
                    fi
                else
                    info "Skipped — existing database kept."
                fi
            else
                cp "$DB_IMPORT_PATH" "$DIRRACUDA_DB_PATH"
                success "Database imported from: $DB_IMPORT_PATH"
            fi
        else
            warn "Import skipped — the file does not appear to be a valid SQLite database."
        fi
    fi
else
    info "Skipped. A fresh database will be created when you first run Dirracuda."
fi

pause

# ──────────────────────────────────────────────────────────────────────────────
# Step 8: Optional extras — ClamAV & tmpfs quarantine
# ──────────────────────────────────────────────────────────────────────────────

section "[Step 8 of 8]  Optional extras"

# ── ClamAV ────────────────────────────────────────────────────────────────────

echo "  ┌─ ClamAV antivirus support ──────────────────────────────────────────┐"
echo "  │                                                                       │"
echo "  │  ClamAV is a free, open-source antivirus scanner. When enabled,      │"
echo "  │  Dirracuda can scan files it downloads and quarantine infected ones   │"
echo "  │  before they reach your filesystem. It is entirely optional.          │"
echo "  │                                                                       │"
echo "  └───────────────────────────────────────────────────────────────────────┘"
echo

if command -v clamscan &>/dev/null; then
    success "ClamAV is already installed — skipping."
else
    if confirm "Install ClamAV now?" "n"; then
        info "Installing ClamAV..."
        sudo apt-get install -y clamav clamav-daemon
        echo
        success "ClamAV installed."
        info "Tip: run 'sudo freshclam' to download the latest virus definitions."
    else
        info "Skipped. Install later with: sudo apt-get install clamav clamav-daemon"
    fi
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

# ──────────────────────────────────────────────────────────────────────────────
# Post-install summary (always printed)
# ──────────────────────────────────────────────────────────────────────────────

printf '%s' "$BOLD"
cat << 'EOF'
╔══════════════════════════════════════════════════════════════╗
║  Installation complete!                                      ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  To run Dirracuda in the future:                             ║
║                                                              ║
║    1. Open a terminal in this directory.                     ║
║                                                              ║
║    2. Activate the virtual environment:                      ║
║         source venv/bin/activate                             ║
║                                                              ║
║    3. Launch the app:                                        ║
║         ./dirracuda                                          ║
║                                                              ║
║  IMPORTANT: The virtual environment must be active before    ║
║  launching. Dirracuda will not start correctly without it.   ║
╚══════════════════════════════════════════════════════════════╝
EOF
printf '%s\n' "$RESET"

# ──────────────────────────────────────────────────────────────────────────────
# Optional: launch now
# ──────────────────────────────────────────────────────────────────────────────

if confirm "Launch Dirracuda now?" "n"; then
    if [[ ! -x venv/bin/python3 ]]; then
        warn "Virtual environment not found — cannot launch."
        warn "Set it up first (step 3), then run: source venv/bin/activate && ./dirracuda"
    else
        echo
        info "Starting Dirracuda..."
        echo
        venv/bin/python3 dirracuda
    fi
fi
