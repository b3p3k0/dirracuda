# ClamAV Lessons Learned

Date: 2026-04-29

## Guardrails To Carry Forward

1. App Config must write non-managed runtime sections (`clamav`, `shodan`, quarantine paths) directly to the active config file; `XSMBSeekConfig.save_config()` intentionally persists only GUI/backend path ownership fields.
2. First-run ClamAV auto-enable is allowed only for freshly created configs when a scanner binary is detected. Existing configs are authoritative, including `clamav.enabled=false`.
3. Dashboard status, dashboard bulk extract, server-list batch extract, and browser downloads must resolve ClamAV settings from the same active runtime config path used for scan launches.
4. Unit-level extract paths should tolerate missing DB reader/log context; logging metadata should be enriched when available, not required for extraction success.
