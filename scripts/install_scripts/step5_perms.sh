# Sourced by install.sh — not standalone.
# Step 5: Set execute permission on the ./dirracuda launcher.

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
