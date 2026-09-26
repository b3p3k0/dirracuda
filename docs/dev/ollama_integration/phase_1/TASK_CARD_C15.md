# TASK CARD C15 — Release matrix, packaging, and public end-to-end acceptance

Status: **COMPLETE — public V1 release boundary accepted (2026-08-16)**

## Scope

C15 closes the initial Analyst build at its reviewed V1 boundary. It aggregates the
existing scale/crash/cancellation matrices, makes the optional dependency lane reachable
from the normal installer, performs one explicit public-synthetic production pipeline
acceptance, updates release-facing documentation, and runs the complete regression gate.

Private Stage E remains deferred. C15 does not authorize reading a real extracted
document, changing the selected model, exposing Ollama beyond loopback, or weakening a
sandbox, digest, durability, privacy or evidence check.

## Locked release boundary

- Core install/startup remains independent of Analyst's optional native dependencies.
  The interactive installer offers Analyst only as a default-No optional step.
- The optional step installs only the reviewed system prerequisites (`bubblewrap` and
  Antiword), invokes `scripts/install_analyst_deps.py`, and runs its exact verification.
  It never replaces the controlled installer with a broad `pip install` command.
- V1 remains Linux x86-64, CPython 3.14, strict isolation, local literal-loopback Ollama,
  the selected exact model/tag/digest, and the frozen parser versions. Other platforms
  or versions fail closed pending a new review.
- The initial supported desktop flow is `./dirracuda` → Accessories → Analyst. Direct
  `gui/main.py` execution remains invalid. The worker remains an internal detached
  module launched by the desktop service.
- The existing loopback-only Ollama deployment is the V1 reference. Future LAN or
  Tailscale access requires an authenticated TLS gateway in front of a still-private
  Ollama API; raw port 11434 is never the remote interface.

## Public end-to-end acceptance

- A dedicated runner requires an exact `--confirm-live` flag at the contact-bearing API
  and CLI. Without it, there is zero database, source, parser, Ollama or output action.
- The fixture is hard-coded public synthetic text in a fresh temporary directory. The
  runner never accepts a source path, prompt, report path, model or endpoint argument.
- It creates one normal Fast directory run, executes the production preflight, Phase 1,
  separately charged version/tags/chat contacts, Phase 2 grounding, report publication
  and completed-report verification. It makes no private-corpus claim.
- Output is one bounded JSON object containing only the closed outcome, run id,
  report-manifest hash and aggregate counts. Source text, prompt/model content,
  findings, paths, errors and exception strings are never printed or retained.
- Resource contention is `INCONCLUSIVE_RESOURCE`, not a quality failure. Any other
  non-complete result is a failed release acceptance and is not silently retried beyond
  the durable production policy.

## Cumulative matrix

- Dependency installer exact check and live strict-sandbox synthetic preflight.
- Import-purity/core-startup checks with optional parser packages absent.
- Exact schema/FK/integrity, mergerfs rollback-journal, lease/fence and multi-process
  migration/claim races.
- Process-crash boundaries before/after claim, parse checkpoints, inference charge and
  finish, resource scheduling, model/file closure, finalization and artifact publish.
- Durable and local cancellation during parser, detector, backoff, network and report
  work; no stale checkpoint or third semantic attempt.
- 46,724-file streaming report scale, bounded inventory/detector/chunk/contact caps, and
  paginated desktop hydration/report reads.
- Manifest-only extraction handoff, no-follow source/output/log/report paths, privacy
  scans, CSV/HTML hostility, exact fallback permissions and full shared/GUI regressions.

## Stop conditions

- Do not modify `requirements.txt`, the primary DB schema/migrations, auth, CI, or the
  selected model/dependency versions in C15.
- Do not perform private Stage E without fresh explicit user authorization.
- Stop release acceptance on dependency/sandbox/deployment drift, non-loopback Ollama,
  digest mismatch, resource pause, protocol violation, report verification failure, or
  any privacy marker in persisted/routine output.

## Terminal outcome

The optional installer lane, confirmation-gated release runner, user-facing docs and
offline release matrix are implemented. The live prerequisite gate reverified exact
parser pins, strict sandbox success, Ollama 0.32.5, the immutable reviewed container
digest, `OLLAMA_HOST=127.0.0.1:11434`, `OLLAMA_NO_CLOUD=1` and a loopback-only listener.

One confirmed production run used only the runner's hard-coded public synthetic text.
It completed inventory, strict parser execution, deterministic detection, separately
charged local Ollama controls and chat, grounding, finalization and independent report
verification. Its complete content-free receipt was:

```json
{"detector_hits":1,"detector_scanned_files":1,"discovered_files":1,"excluded_paths":0,"model_findings":2,"model_reviewed_files":1,"outcome":"complete","protocol":"analyst-release-live-v1","report_manifest_sha256":"d115b7277d74c61290ca182c60b7d291ceb25eb8256bf2ca1fb38a86e59638a5","run_id":"8bcee257afee043b99f567236018d4cd","selected_files":1}
```

This is public-synthetic release evidence, not private Stage-E validation. Future raw
LAN/Tailscale exposure remains prohibited; remote access still requires a separately
reviewed authenticated TLS gateway while Ollama remains bound to loopback.

## Validation record

- Exact dependency check: PASS (PyMuPDF/MuPDF 1.28.0, defusedxml 0.7.1,
  python-calamine 0.8.2).
- Live strict-sandbox public preflight: PASS.
- C15 installer/runner suite: 8 PASS; C9/C15 live-runner offline set: 18 PASS.
- Shared tests: 2,480 PASS; focused shared Analyst tests: 1,406 PASS.
- GUI tests: 2,022 PASS; focused GUI Analyst tests: 13 PASS.
- Web UI tests: 620 PASS with one upstream Starlette deprecation warning.
- Scripts tests: 1,480 PASS and 6 SKIP in the broad run. Four historical C0B source-seal
  and cancellation cases were invalidated by validation-time repository edits/signals;
  the same four were then rerun against an untouched tree and passed 4/4.
- Shell syntax, Python compilation and whitespace checks: PASS.
