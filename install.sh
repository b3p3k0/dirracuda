#!/usr/bin/env bash
set -euo pipefail

# ──────────────────────────────────────────────────────────────────────────────
# Guard: must run from project root (before sourcing lib so die is not yet set)
# ──────────────────────────────────────────────────────────────────────────────

if [[ ! -f requirements.txt ]] || [[ ! -d conf ]]; then
    printf '\n\033[31m✘ ERROR:\033[0m Run this script from the Dirracuda project root directory.\n\n' >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=scripts/install_scripts/_lib.sh
source "$SCRIPT_DIR/scripts/install_scripts/_lib.sh"

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
# Steps
# ──────────────────────────────────────────────────────────────────────────────

# shellcheck source=scripts/install_scripts/step1_python.sh
source "$SCRIPT_DIR/scripts/install_scripts/step1_python.sh"
# shellcheck source=scripts/install_scripts/step2_sysdeps.sh
source "$SCRIPT_DIR/scripts/install_scripts/step2_sysdeps.sh"
# shellcheck source=scripts/install_scripts/step3_venv.sh
source "$SCRIPT_DIR/scripts/install_scripts/step3_venv.sh"
# shellcheck source=scripts/install_scripts/step4_config.sh
source "$SCRIPT_DIR/scripts/install_scripts/step4_config.sh"
# shellcheck source=scripts/install_scripts/step5_perms.sh
source "$SCRIPT_DIR/scripts/install_scripts/step5_perms.sh"
# shellcheck source=scripts/install_scripts/step6_shodan.sh
source "$SCRIPT_DIR/scripts/install_scripts/step6_shodan.sh"
# shellcheck source=scripts/install_scripts/step7_db_import.sh
source "$SCRIPT_DIR/scripts/install_scripts/step7_db_import.sh"
# shellcheck source=scripts/install_scripts/step8_clamav.sh
source "$SCRIPT_DIR/scripts/install_scripts/step8_clamav.sh"

# ──────────────────────────────────────────────────────────────────────────────
# Post-install summary
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
