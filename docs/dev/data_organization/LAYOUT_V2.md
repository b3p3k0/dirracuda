# Home-First Data Organization (Layout v2)

## Canonical Layout

```text
~/.dirracuda/
  conf/
    config.json
    exclusion_list.json
    ransomware_indicators.json
    signatures/
      rce_smb/*.yaml
    wordlists/
  data/
    dirracuda.db
    experimental/
      se_dork.db
      reddit_od.db
      dorkbook.db
      keymaster.db
    quarantine/
    extracted/
    tmpfs_quarantine/
    cache/
      probes/
        smb/
        ftp/
        http/
  state/
    gui_settings.json
    templates/
      scan/
      filter/
    migrations/
      state.json
      reports/
      backups/
        <timestamp>/
  logs/
    rce_analysis.jsonl
    extract/
    app/
```

## Runtime Policy

- Canonical config path: `~/.dirracuda/conf/config.json`
- Canonical main DB path: `~/.dirracuda/data/dirracuda.db`
- Canonical GUI settings path: `~/.dirracuda/state/gui_settings.json`
- Canonical scan templates: `~/.dirracuda/state/templates/scan/`
- Canonical filter templates: `~/.dirracuda/state/templates/filter/`
- Canonical probe cache fallback files: `~/.dirracuda/data/cache/probes/{smb,ftp,http}/`

## Compatibility + Migration

- Startup bootstrap ensures layout directories and missing conf assets are created/copied.
- One-time migration is tracked in `~/.dirracuda/state/migrations/state.json`.
- Migration backups are written to `~/.dirracuda/state/migrations/backups/<timestamp>/`.
- Migration reports are written to `~/.dirracuda/state/migrations/reports/layout_v2_<timestamp>.json`.
- Legacy sources considered during migration/fallback:
  - `~/.smbseek/`
  - flat `~/.dirracuda` pre-v2 files
  - repo-local `conf/config.json`, `dirracuda.db`, `smbseek.db`
- App startup remains non-blocking on migration failure; legacy fallback paths are used when required and surfaced in a warning dialog.
