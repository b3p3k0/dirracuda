# Sourced by install.sh — not standalone.
# Step 2: System package dependencies (apt-get).

section "[Step 2 of 10]  System dependencies"

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
