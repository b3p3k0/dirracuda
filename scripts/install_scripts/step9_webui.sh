# Sourced by install.sh — not standalone.
# Step 9: Optional web UI dependency install and initial credential setup.

section "[Step 9]  Web UI setup  (optional)"

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

# ── Part C: Next steps ────────────────────────────────────────────────────────

info "To start the web UI:"
info "  ./venv/bin/python -m experimental.webui.server"
info "  Then open: http://127.0.0.1:5480"
echo
info "Remote access, TLS, and full config: see experimental/webui/README.md"

pause
