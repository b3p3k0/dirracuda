# Sourced by install.sh — not standalone.
# Step 10: Optional Analyst dependency and strict-sandbox setup.

section "[Step 10 of 10]  Analyst document review  (optional)"

echo "  Analyst is an optional local document-review workflow. Its parser lane"
echo "  is currently reviewed only for Linux x86-64 with CPython 3.14 and uses"
echo "  strict bubblewrap/cgroup isolation plus exact hash-pinned dependencies."
echo
echo "  PyMuPDF/MuPDF are AGPL-3.0; review docs/ANALYST_GUIDE.md and the notices"
echo "  under licenses/ before enabling this optional feature."
echo

if ! confirm "Install and verify optional Analyst dependencies?" "n"; then
    warn "Skipped. Core Dirracuda remains fully usable without Analyst."
    warn "Install later with: ./venv/bin/python scripts/install_analyst_deps.py"
    pause
    return 0
fi

if [[ ! -x venv/bin/python ]]; then
    warn "Virtual environment not found — Analyst setup cannot continue."
    warn "Complete step 3, then re-run the installer."
    pause
    return 0
fi

if ! command -v sudo &>/dev/null; then
    warn "sudo is unavailable — cannot install bubblewrap and Antiword."
    pause
    return 0
fi

info "Installing Analyst system prerequisites..."
if ! sudo apt-get update -qq; then
    warn "System package metadata refresh failed; Analyst setup stopped."
    pause
    return 0
fi
if ! sudo apt-get install -y bubblewrap antiword; then
    warn "Analyst system prerequisite installation failed."
    pause
    return 0
fi

for analyst_tool in bwrap prlimit systemd-run antiword; do
    if ! command -v "$analyst_tool" &>/dev/null; then
        warn "Analyst prerequisite verification failed closed."
        pause
        return 0
    fi
done

if [[ "$(dpkg-query -W -f='${Version}' antiword 2>/dev/null || true)" != "0.37-17" ]]; then
    warn "The reviewed Antiword package revision (0.37-17) is unavailable."
    pause
    return 0
fi

info "Installing exact reviewed Analyst parser artifacts..."
if ! ./venv/bin/python scripts/install_analyst_deps.py; then
    warn "Analyst dependency installation failed closed."
    pause
    return 0
fi

if ! ./venv/bin/python scripts/install_analyst_deps.py --check; then
    warn "Analyst dependency verification failed closed."
    pause
    return 0
fi

if ! ./venv/bin/python -c \
    'from experimental.analyst.sandbox import strict_preflight; raise SystemExit(0 if strict_preflight().ok else 1)'; then
    warn "Analyst strict-sandbox preflight failed. Review docs/ANALYST_GUIDE.md."
    pause
    return 0
fi

success "Analyst dependencies and strict sandbox verified."
pause
