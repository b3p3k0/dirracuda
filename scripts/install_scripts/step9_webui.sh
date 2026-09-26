# Sourced by install.sh — not standalone.
# Step 9: Optional web UI dependency install and initial credential setup.

section "[Step 9 of 10]  Web UI setup  (optional)"

echo "  The web UI is an optional browser-based interface for scan management,"
echo "  results browsing, and database export. It runs as a separate service"
echo "  alongside the desktop GUI and is disabled by default."
echo

# ── Part A: Dependencies ──────────────────────────────────────────────────────

echo "  ┌─ Web UI dependencies ───────────────────────────────────────────────┐"
echo "  │  fastapi  uvicorn  jinja2  httpx                                     │"
echo "  │  Installed separately to keep the main runtime lean.                 │"
echo "  └─────────────────────────────────────────────────────────────────────┘"
echo

if [[ ! -x venv/bin/python3 ]]; then
    warn "Virtual environment not found — skipping web UI setup."
    warn "Complete step 3 first, then re-run the installer or install manually:"
    warn "  venv/bin/pip install -r experimental/webui/requirements-web.txt"
    pause
    return 0
fi

WEBUI_DEPS_OK=false
if venv/bin/python3 -c "import fastapi, uvicorn, jinja2, httpx" 2>/dev/null; then
    success "Web UI dependencies already installed."
    WEBUI_DEPS_OK=true
else
    if confirm "Install web UI Python dependencies?" "n"; then
        info "Installing web UI dependencies..."
        venv/bin/pip install -r experimental/webui/requirements-web.txt
        echo
        if venv/bin/python3 -c "import fastapi, uvicorn, jinja2, httpx" 2>/dev/null; then
            success "Web UI dependencies installed."
            WEBUI_DEPS_OK=true
        else
            warn "Install completed but import check failed — something may be wrong."
        fi
    else
        warn "Skipped. Install later with:"
        warn "  venv/bin/pip install -r experimental/webui/requirements-web.txt"
    fi
fi

echo

# ── Part B: Credential setup ──────────────────────────────────────────────────

echo "  ┌─ Web UI credentials ────────────────────────────────────────────────┐"
echo "  │  The web UI requires a login. There's no in-app signup — you set    │"
echo "  │  credentials here (or manually before first launch).                 │"
echo "  └─────────────────────────────────────────────────────────────────────┘"
echo

if confirm "Set up a web UI login now?" "n"; then
    echo
    read -rp "  Username: " WEBUI_USERNAME || WEBUI_USERNAME=''
    WEBUI_USERNAME="${WEBUI_USERNAME// /}"

    if [[ -z "$WEBUI_USERNAME" ]]; then
        warn "Username cannot be empty — skipping credential setup."
        warn "Set up manually: see experimental/webui/README.md"
    else
        read -rsp "  Password: " WEBUI_PASSWORD || WEBUI_PASSWORD=''
        echo
        read -rsp "  Confirm password: " WEBUI_PASSWORD2 || WEBUI_PASSWORD2=''
        echo
        echo

        if [[ -z "$WEBUI_PASSWORD" ]]; then
            warn "Password cannot be empty — skipping credential setup."
            warn "Set up manually: see experimental/webui/README.md"
        elif [[ "$WEBUI_PASSWORD" != "$WEBUI_PASSWORD2" ]]; then
            warn "Passwords do not match — skipping credential setup."
            warn "Set up manually: see experimental/webui/README.md"
        else
            mkdir -p "$DIRRACUDA_CONF_DIR"

            CRED_ERR=''
            if ! CRED_ERR=$(
                WEBUI_U="$WEBUI_USERNAME" WEBUI_P="$WEBUI_PASSWORD" \
                venv/bin/python3 - 2>&1 <<'PYEOF'
import os, sys
sys.path.insert(0, '.')
from experimental.webui.auth import set_password
try:
    set_password(os.environ['WEBUI_U'], os.environ['WEBUI_P'])
except ValueError as e:
    print(f"Invalid credential: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
            ); then
                warn "Could not save credentials${CRED_ERR:+: $CRED_ERR}"
                warn "Set up manually: see experimental/webui/README.md"
            else
                success "Credentials saved for user '$WEBUI_USERNAME'."
                info "Run the same command again to add more users or change a password."
            fi

            unset WEBUI_PASSWORD WEBUI_PASSWORD2 WEBUI_U WEBUI_P
        fi
    fi
else
    echo
    info "Skipped. Set up credentials before first launch:"
    info "  ./venv/bin/python -c \\"
    info "    \"from experimental.webui.auth import set_password; set_password('admin', 'yourpassword')\""
fi

echo

# ── Part C: Optional user service ─────────────────────────────────────────────

echo "  ┌─ Headless user service ─────────────────────────────────────────────┐"
echo "  │  dirracuda-d can run the Web UI without the desktop GUI. On systems │"
echo "  │  using systemd, an optional per-user unit can start at user login.   │"
echo "  │  This does not enable lingering or install a system-wide service.    │"
echo "  └─────────────────────────────────────────────────────────────────────┘"
echo

WEBUI_CREDS_OK=false
if venv/bin/python3 -c \
    "from experimental.webui.auth import credential_exists; raise SystemExit(0 if credential_exists() else 1)" \
    2>/dev/null; then
    WEBUI_CREDS_OK=true
fi

if [[ "$WEBUI_DEPS_OK" != "true" ]]; then
    warn "Web UI dependencies are unavailable — skipping user-service setup."
elif [[ "$WEBUI_CREDS_OK" != "true" ]]; then
    warn "No usable Web UI credential exists — skipping user-service setup."
    warn "Configure one later with: ./dirracuda-d credentials set USERNAME"
elif ! command -v systemctl >/dev/null 2>&1; then
    info "systemctl is unavailable — direct ./dirracuda-d control remains available."
elif ! systemctl --user show-environment >/dev/null 2>&1; then
    warn "The systemd user manager is unavailable in this session."
    info "You can retry later with: ./dirracuda-d systemd install"
elif confirm "Create, enable, and start the per-user dirracuda-d service?" "n"; then
    echo
    if ./dirracuda-d systemd install; then
        success "Per-user service installed, enabled, and started."
        info "It will start with your user manager/login; lingering was not changed."
    else
        warn "User-service setup failed. Review: ./dirracuda-d systemd status"
    fi
else
    info "Skipped. Direct background control remains available."
fi

echo

# ── Part D: Next steps ────────────────────────────────────────────────────────

info "To start the web UI:"
info "  ./dirracuda-d start"
info "  Then open: http://127.0.0.1:2600"
echo
info "Daemon help and diagnostics:"
info "  ./dirracuda-d --help"
info "  ./dirracuda-d doctor"
echo
info "Remote access, TLS, and full config: see experimental/webui/README.md"

pause
