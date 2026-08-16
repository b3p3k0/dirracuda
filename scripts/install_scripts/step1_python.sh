# Sourced by install.sh — not standalone.
# Step 1: Python version check.

section "[Step 1 of 10]  Python version check"

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
