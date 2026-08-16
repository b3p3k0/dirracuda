# Sourced by install.sh — not standalone.
# Step 5: Set execute permission on the GUI and daemon launchers.

section "[Step 5 of 10]  Launcher permissions"

echo "  The ./dirracuda and ./dirracuda-d launchers need execute permission."
echo "  This is sometimes missing after downloading or cloning the project."
echo

for launcher in dirracuda dirracuda-d; do
    if [[ ! -f "$launcher" ]]; then
        warn "./$launcher is missing — skipping."
    elif [[ -x "$launcher" ]]; then
        success "./$launcher is already executable — skipping."
    else
        if confirm "Set execute permission on ./$launcher?"; then
            chmod +x "$launcher"
            success "Permission set on ./$launcher."
        else
            warn "Skipped. If it won't start, run: chmod +x $launcher"
        fi
    fi
done

pause
