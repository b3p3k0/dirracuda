# Experimental Dialog Lessons Learned

Date: 2026-05-03
Scope: Sidecar browser promotion into the main Dirracuda DB.

## Guardrails To Carry Forward

1. Sidecar promotion must not depend on Server List Browser window state. Experimental browsers should promote through a DB-backed callback that works even when SLB was never opened.
2. Future sidecars should convert promotion data through `gui/utils/sidecar_promotion.py` so SMB/FTP/HTTP payload validation, DNS resolution, defaults, and HTTP probe hints stay consistent.
3. Unresolved hostnames should fail visibly and skip the write; do not store guessed or non-IP values in the main DB.
4. Keep DNS resolution on the user action path only, not on sidecar table load or filtering paths.
5. Operator docs should describe direct promotion and mention that newly added rows may be hidden by active Server List Browser filters.
6. Sidecar probe summaries should travel as explicit `_probe_cache` artifacts on promotion payloads. Copy only cacheable `clean`/`issue` statuses into main DB probe caches; do not fabricate raw probe snapshots when the sidecar stores only summary fields.
7. Sidecar details/notes views should start read-only unless the sidecar schema has an intentional notes contract and migration path.
8. Sidecars that call the shared probe runner must persist the full probe snapshot, not only a summary, when downstream promotion or details views need the probe tree. Summary-only legacy rows should stay honest and require manual re-probe to populate missing trees.
