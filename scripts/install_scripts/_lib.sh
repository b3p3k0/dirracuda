# Sourced by install.sh — not standalone.
# Provides: terminal helpers, env vars shared across all install steps.

# ── Terminal helpers ──────────────────────────────────────────────────────────

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

# ── Environment paths ─────────────────────────────────────────────────────────

DIRRACUDA_HOME="$HOME/.dirracuda"
DIRRACUDA_CONF_DIR="$DIRRACUDA_HOME/conf"
DIRRACUDA_DATA_DIR="$DIRRACUDA_HOME/data"
DIRRACUDA_CONFIG="$DIRRACUDA_CONF_DIR/config.json"
DIRRACUDA_DB_PATH="$DIRRACUDA_DATA_DIR/dirracuda.db"
DIRRACUDA_CANON_TMPFS_MP="$DIRRACUDA_HOME/data/tmpfs_quarantine"
DIRRACUDA_LEGACY_TMPFS_MP="$DIRRACUDA_HOME/quarantine_tmpfs"
CONFIG_CREATED_THIS_INSTALL=false
