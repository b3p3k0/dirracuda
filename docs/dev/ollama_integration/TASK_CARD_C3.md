# C3 — Strict parser supervisor and bubblewrap boundary

Date: 2026-08-14
Status: **Complete**

## Issue

Native document parsers will process hostile files in C4+. They need one production
supervisor that proves containment, resource bounds, bounded IPC, cancellation and
source identity before any format-specific parser is introduced.

## Root cause

Bubblewrap is a policy builder, not a complete security policy. The C0B smoke proved
basic host capability but is not reusable production supervision. It also confirmed that
`RLIMIT_NPROC` is unsafe here because it counts all processes owned by the user; a useful
fork limit must be scoped to the parser's cgroup.

## Scope

- Launch an exact command through a transient user-systemd scope with `TasksMax`, then
  bubblewrap, then `prlimit`. No shell and no `preexec_fn`.
- Require absolute trusted tool paths and a caller-supplied, explicit read-only runtime
  bind list. Never bind `/`, the repository root, user HOME, source parent, D-Bus, SSH
  agents or `/run/user` into the sandbox.
- Bind only the already-open source descriptor at `/input/document` using
  `--ro-bind-fd`; pass no unrelated descriptor.
- Use an empty mount namespace, private HOME/tmp, synthetic `/proc` and `/dev`, cleared
  environment, new PID/network/IPC/UTS namespaces, no capabilities, a new session and
  parent-death kill.
- Apply address-space, CPU, open-file and core limits with `/usr/bin/prlimit`. Apply the
  process/task bound with systemd `TasksMax` before the parser starts.
- Stream stdout/stderr through nonblocking bounded readers. Wall timeout, caller
  cancellation or either output cap kills the exact transient unit/cgroup and reaps the
  launcher.
- Compare the already-open source descriptor to the C2 fingerprint before dispatch and
  after completion. Any mismatch discards output as `source_changed_since_inventory`.
- Return closed reason codes; never log or interpolate parser stderr, source text or
  paths into routine diagnostics.

## Compatibility and fallback

- Strict mode requires bubblewrap, prlimit, cgroup v2 PID control and a working user
  systemd manager. Missing capability is `sandbox_unavailable`, not reduced isolation.
- The frozen per-run reduced-isolation acknowledgement remains a later UI/service path.
  C3 does not create an implicit fallback, and automatic post-extract work cannot use it.
- The C0B `sandbox_smoke.py` stays as immutable historical benchmark machinery rather
  than being deleted: removing it would make the frozen Stage-A command and evidence
  irreproducible. It is not imported or used by production.

## Out of scope

- Format sniffing and parsers (C4–C7), sidecar state (C8), worker orchestration (C10),
  GUI/reduced-mode acknowledgement (C13), and real/private documents.
- Dependency, migration, auth and CI changes.

## Acceptance

1. Policy/argv tests prove exact namespace, environment, descriptor and bind behavior.
2. A live synthetic probe proves network, HOME/repo and unrelated host paths are absent.
3. A live finite fork probe proves `TasksMax` prevents the requested child count.
4. Address-space, timeout, cancellation and stdout/stderr overflow produce exact bounded
   outcomes and leave no transient unit/process behind.
5. Pre/post source mutation returns `source_changed_since_inventory` and discards bytes.
6. Missing tools/systemd/cgroup capability fails closed before a parser command.
7. No parser library is imported into the durable process.
8. Focused tests, privacy scan, file sizes and root README review pass.

## Outcome

C3 shipped one production supervisor in `experimental/analyst/sandbox.py`. The live
synthetic proof passed on this host: the exact source descriptor was mounted read-only,
network/HOME/repository access was absent, and the cgroup task ceiling stopped the finite
fork probe. Focused tests also proved address-space, output, timeout, cancellation,
source-mutation, capability and cleanup outcomes.

The probe uses a named, owner-only temporary synthetic file. Linux `memfd` descriptors
appear as deleted anonymous objects and bubblewrap 0.11.1 cannot use them as a
`--ro-bind-fd` mount source. Production inputs are the named files already opened by C2;
they still reach bubblewrap only through `pass_fds` and are re-fingerprinted before and
after parsing.

## Sources

- Bubblewrap security model and command contract:
  https://github.com/containers/bubblewrap
- Bubblewrap current CLI source (`--ro-bind-fd`, namespaces, parent death):
  https://github.com/containers/bubblewrap/blob/main/bubblewrap.c
- Linux cgroup v2 PID controller:
  https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html
- `prlimit` interface:
  https://man7.org/linux/man-pages/man1/prlimit.1.html
