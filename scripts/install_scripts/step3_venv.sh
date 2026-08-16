# Sourced by install.sh — not standalone.
# Step 3: Python virtual environment creation and dependency install.

section "[Step 3 of 10]  Python virtual environment"

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
