# Config Modularization Lessons Learned (2026-05-22)

1. Canonical-only runtime paths reduce drift bugs, but internal compatibility hooks are still needed for tests and controlled tooling paths.
2. Module-pref migration must deep-merge legacy GUI prefs with existing module config; overwrite-on-migrate silently drops fields.
3. Keep shard ownership explicit and enforce section-owner writes to prevent cross-feature clobbering.
4. Preserve a materialized compatibility `~/.dirracuda/conf/config.json` during dual-read rollout so legacy readers remain stable while consumers migrate.
5. Route high-frequency single-key writes (for example `shodan.api_key`) through owner-scoped APIs, not raw JSON rewrites.
6. For runtime safety, modular migration should fail soft: emit a warning, keep session alive, and provide concrete recovery/report paths.
7. Treat GUI prefs and experimental module prefs separately (`prefs/user-prefs.json` + module shards) so non-module UI state stays isolated.
8. Update docs and tests in the same card as config-path changes; stale path assumptions are the most common regression source.
