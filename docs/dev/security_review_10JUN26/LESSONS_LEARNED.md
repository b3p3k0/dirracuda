# Security Review Lessons Learned

Date: 2026-06-10

Status: initial seed; append during execution

## Planning Lessons

1. External audit findings are inputs, not immutable truth. Reproduce each claim
   against the exact branch before assigning work.
2. Good pushback improved this review: F1 expanded, F4 received a safer design,
   F5 became a taxonomy program, and F6 was correctly downgraded.
3. A security symptom may expose a broader architecture problem. Redirect SSRF
   led to discovery of hostname reconnection and TLS-policy drift.
4. "One source of truth" means one persisted authority and one resolver
   contract, not merely a documented precedence chain among conflicting keys.
5. Blanket logging is not observability. Exception handling needs ownership,
   privacy constraints, and an explicit user-impact decision.
6. A mitigation already provided by the supported runtime should be recorded
   accurately. Defense in depth is still useful but must not be mislabeled as a
   currently exploitable vulnerability.

## Execution Guardrails

1. Connect to recorded network identity; keep virtual-host metadata separate.
2. Disable ambient proxy behavior for hostile-target traffic.
3. Apply resource limits before expensive decode or extraction work.
4. Enforce path containment immediately before filesystem mutation.
5. Keep remote identifiers separate from local sanitized labels.
6. Test declared-size and actual streamed-size limits.
7. Do not log secrets, untrusted content, or control characters verbatim.
8. Preserve intentional silent catches where silence is part of safe teardown
   or polling behavior.
9. When changing a shared security primitive, enumerate every caller before
   implementation and add direct caller-contract tests.
10. Treat README and Technical Reference drift as a card acceptance failure.
11. IDNA normalization must cover wire values as well as comparisons. A
    Unicode hostname can compare correctly yet still fail when written raw to
    an ASCII HTTP authority or `Host` header.
12. When a security adapter depends on standard-library internals, verify the
    exact supported-version source and test the observable wire behavior. An
    attribute that sounds authoritative may not be consulted by the connection
    path at all.
13. Determine whether a configuration value is explicit before migration
    materializes repository defaults. Once defaults are copied into an owning
    shard, presence alone cannot distinguish user intent from synthesis.

## Append Format

Each new lesson should include:

```text
Card:
Observed problem:
Root cause:
Guardrail:
Future trigger:
```
