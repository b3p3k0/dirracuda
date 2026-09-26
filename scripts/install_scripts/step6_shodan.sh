# Sourced by install.sh — not standalone.
# Step 6: Optional Shodan API key configuration.

section "[Step 6 of 10]  Shodan API key  (optional)"

echo "  Dirracuda uses Shodan to search for publicly exposed network hosts."
echo "  You'll need a free Shodan account and API key to use these features."
echo
echo "  You can sign up and find your key at:"
echo "    https://account.shodan.io"
echo
echo "  If you don't have a key yet, press Enter to skip."
echo "  You can add it later in ~/.dirracuda/conf/config.json under:  shodan → api_key"
echo

SHODAN_KEY=''

if confirm "Would you like to enter a Shodan API key now?" "n"; then
    echo
    echo "  Hold on — before you paste anything..."
    echo
    if confirm "  You're about to hand a secret key to a shell script. Did you read the source to make sure we're not doing anything sketchy with it?"; then
        echo
        success "Good. Can't be too careful — especially with a security tool."
        echo
    else
        echo
        if confirm "  Fair enough. Want to open the script in less so you can review it?" "n"; then
            echo
            info "Opening the Shodan step script in less — press q to quit and return to the installer."
            echo
            pause
            less "$SCRIPT_DIR/scripts/install_scripts/step6_shodan.sh"
            echo
            success "Welcome back. Let's continue."
            echo
        else
            echo
            warn "Well, your call. We'll save the key as-is — but do audit your tools, yeah?"
            echo
        fi
    fi
    read -rp "  Enter your Shodan API key: " SHODAN_KEY || SHODAN_KEY=''
    SHODAN_KEY="${SHODAN_KEY//[[:space:]]/}"
fi

if [[ -n "$SHODAN_KEY" ]]; then
    if [[ ! -f "$DIRRACUDA_CONFIG" ]]; then
        warn "$DIRRACUDA_CONFIG not found — cannot save key."
        warn "Add it manually under: shodan → api_key"
    elif [[ ! -x venv/bin/python3 ]]; then
        warn "Virtual environment not found — cannot save key."
        warn "Add it manually to ~/.dirracuda/conf/config.json under: shodan → api_key"
    else
        if SHODAN_KEY_VAL="$SHODAN_KEY" DIRRACUDA_CONFIG_PATH="$DIRRACUDA_CONFIG" venv/bin/python3 - <<'PYEOF'
import json, pathlib, sys, os
key = os.environ['SHODAN_KEY_VAL']
p = pathlib.Path(os.environ['DIRRACUDA_CONFIG_PATH'])
try:
    cfg = json.loads(p.read_text())
    cfg.setdefault('shodan', {})['api_key'] = key
    p.write_text(json.dumps(cfg, indent=2))
except Exception as e:
    print(f'Could not save API key: {e}', file=sys.stderr)
    sys.exit(1)
PYEOF
        then
            success "Shodan API key saved to $DIRRACUDA_CONFIG."
        else
            warn "Could not save API key automatically."
            warn "Add it manually to ~/.dirracuda/conf/config.json under: shodan → api_key"
        fi
    fi
else
    info "Skipped. Add your key later in ~/.dirracuda/conf/config.json → shodan → api_key"
fi

pause
