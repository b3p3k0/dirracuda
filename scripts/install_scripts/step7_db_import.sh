# Sourced by install.sh — not standalone.
# Step 7: Optional import of an existing dirracuda database.

section "[Step 7 of 10]  Import existing database  (optional)"

echo "  If you have a dirracuda.db from a previous installation, you can"
echo "  import it into the canonical home data path to preserve scan history."
echo "  Target: $DIRRACUDA_DB_PATH"
echo
echo "  Leave blank to skip — a fresh database will be created on first run."
echo

read -rp "  Path to existing dirracuda.db (or press Enter to skip): " DB_IMPORT_PATH || DB_IMPORT_PATH=''

if [[ -n "$DB_IMPORT_PATH" ]]; then
    DB_IMPORT_PATH="${DB_IMPORT_PATH/#\~/$HOME}"

    if [[ ! -f "$DB_IMPORT_PATH" ]]; then
        warn "File not found: $DB_IMPORT_PATH — skipping."
    elif [[ ! -x venv/bin/python3 ]]; then
        warn "Virtual environment not found — cannot validate the file."
        warn "Copy it manually to: $DIRRACUDA_DB_PATH"
    else
        DB_VALID=false
        if DB_FILE_PATH="$DB_IMPORT_PATH" venv/bin/python3 - <<'PYEOF'
import sys, os
path = os.environ['DB_FILE_PATH']
try:
    with open(path, 'rb') as f:
        header = f.read(16)
    if not header.startswith(b'SQLite format 3'):
        print(f'  File does not appear to be a valid SQLite database.')
        sys.exit(1)
except Exception as e:
    print(f'  Could not read file: {e}')
    sys.exit(1)
PYEOF
        then
            DB_VALID=true
        fi

        if [[ "$DB_VALID" == "true" ]]; then
            mkdir -p "$DIRRACUDA_DATA_DIR"
            if [[ -f "$DIRRACUDA_DB_PATH" ]]; then
                warn "A database already exists at $DIRRACUDA_DB_PATH."
                if confirm "Overwrite it with the imported file?" "n"; then
                    DB_BACKUP_PATH="$DIRRACUDA_DB_PATH.pre_import_$(date +%Y%m%d_%H%M%S).bak"
                    if cp "$DIRRACUDA_DB_PATH" "$DB_BACKUP_PATH"; then
                        info "Existing database backed up to: $DB_BACKUP_PATH"
                        cp "$DB_IMPORT_PATH" "$DIRRACUDA_DB_PATH"
                        success "Database imported from: $DB_IMPORT_PATH"
                    else
                        warn "Could not create backup at $DB_BACKUP_PATH — keeping existing database."
                        info "Skipped import to avoid data loss."
                    fi
                else
                    info "Skipped — existing database kept."
                fi
            else
                cp "$DB_IMPORT_PATH" "$DIRRACUDA_DB_PATH"
                success "Database imported from: $DB_IMPORT_PATH"
            fi
        else
            warn "Import skipped — the file does not appear to be a valid SQLite database."
        fi
    fi
else
    info "Skipped. A fresh database will be created when you first run Dirracuda."
fi

pause
