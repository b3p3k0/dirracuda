# Sourced by install.sh — not standalone.
# Step 4: Config file creation and supplemental config files.

section "[Step 4 of 8]  Configuration file"

echo "  Dirracuda reads settings from ~/.dirracuda/conf/config.json."
echo "  We'll create one from the bundled example. You can edit it"
echo "  at any time — the defaults are fine for getting started."
echo

if [[ -f "$DIRRACUDA_CONFIG" ]]; then
    success "$DIRRACUDA_CONFIG already exists — skipping (will not overwrite)."
else
    if confirm "Create $DIRRACUDA_CONFIG from the example template?"; then
        mkdir -p "$DIRRACUDA_CONF_DIR"
        cp conf/config.json.example "$DIRRACUDA_CONFIG"
        CONFIG_CREATED_THIS_INSTALL=true
        success "$DIRRACUDA_CONFIG created."
    else
        warn "Skipped. Dirracuda will create a default config on first run at:"
        warn "  $DIRRACUDA_CONFIG"
        warn "Note: the Shodan API key step (step 6) requires this file to exist."
    fi
fi

DIRRACUDA_EXCLUSION_FILE="$DIRRACUDA_CONF_DIR/exclusion_list.json"
DIRRACUDA_RANSOMWARE_FILE="$DIRRACUDA_CONF_DIR/ransomware_indicators.json"

for _src_dst in \
    "conf/exclusion_list.json:$DIRRACUDA_EXCLUSION_FILE" \
    "conf/ransomware_indicators.json:$DIRRACUDA_RANSOMWARE_FILE"; do
    _src="${_src_dst%%:*}"
    _dst="${_src_dst##*:}"
    if [[ -f "$_dst" ]]; then
        success "$_dst already exists — skipping."
    elif [[ -f "$_src" ]]; then
        mkdir -p "$DIRRACUDA_CONF_DIR"
        cp "$_src" "$_dst"
        success "$(basename "$_dst") copied to $DIRRACUDA_CONF_DIR."
    else
        warn "Source file $PWD/$_src not found — skipping $(basename "$_dst")."
    fi
done
unset _src_dst _src _dst

pause
