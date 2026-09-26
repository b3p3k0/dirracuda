# C0B-7 Offline Evidence Recovery Protocol

Date frozen: 2026-08-14
Status: **Prospective, offline-only**

## 1. Purpose

C0B-7 recovers the final public acceptance decision from immutable C0B-6 evidence. It
does not mutate the C0B-6 terminal, contact Ollama, regenerate an answer, change a
threshold, or consume another live-child allowance.

C0B-6 completed all 240 planned calls and stored every lane aggregate before its final
join failed closed. The join confused the SHA-256 identity of the complete C0B-3
decision row with the SHA-256 of its JSON payload. It then passed canonical JSON—with
alphabetically sorted object keys—through a legacy validator that incorrectly treated
mapping insertion order as semantic category order.

## 2. Frozen source evidence

- run: `c0b6-20260814-154202-472a5f0a12e0bf0dded7a13a`
- source commit: `c7d5eda633f11d9aeb98ccd17b326cbec08ad1c1`
- terminal/origin/calls: `BLOCKED_PROVENANCE` / `acceptance_derivation` / 240
- checkpoint SHA-256: `f91637933737a054e580f1915d2c239a6d5c5d2756b7e68059d519ebe729e61c`
- snapshot SHA-256: `8ae6d0ef009aa4f17b9e75657faa20392267f66d311974df13b72e5d32dc6de4`
- backup anchor SHA-256: `bc2b21296e4dffa1cdb6cb05fe822fb187220f7974c92b77df7ede46cfe77097`
- backup receipt SHA-256: `ce0c4f8894260a69b39eec111785d6dbc067f44f8d7b350bb309e971bc8be783`
- failure artifact SHA-256: `2d17a6fda6a3e4105f9ebd36ca7b9374a37524b4cba22180af6bfc0d87b5b1c3`

Both checkpoint and snapshot must pass existing independent C0B-6 semantic replay. Any
byte, identity, census, control, aggregate, source or parent mismatch fails closed.

## 3. Allowed correction

C0B-3 replay remains the authority for decision-row identity. C0B-7 carries that
already-verified row SHA-256 forward; it never compares the JSON payload hash to it.

For legacy validation only, C0B-7 may construct a temporary view whose
`category_recall` mappings follow `pii, financial, contact, demographic`. It must prove
that the original is an exact four-key mapping, only insertion order changes, and
canonical JSON before and after is byte-identical. The original parent payload remains
immutable and authoritative; the ordered view is never replacement evidence.

## 4. Recovered decision

C0B-7 independently rebuilds D50, combines it with replayed C0B-6 F72/20260811 and C44,
and applies the unchanged C0B-6 acceptance rule. F72/20260818 remains a stability and
public-summary component, as originally designed.

The result is `RECOVERED_CONFIRMED` or `RECOVERED_INCONCLUSIVE`. It is retrospective
technical recovery from prospectively generated evidence, not a new untouched holdout.
C0B-6 remains `BLOCKED_PROVENANCE` forever.

## 5. Safety and gates

- no network or transport import is reachable;
- all SQLite connections are pinned read-only;
- checkpoint, snapshot and receipt are never modified;
- only schema-bounded public aggregates and hashes may be emitted;
- checkpoint and snapshot recovery must agree independently;
- C0B-7A freezes this protocol and the C0B-6 outcome before implementation;
- C0B-7B tests row-vs-payload identity, mapping-order normalization, tampering,
  checkpoint/snapshot agreement, immutability and the transport-import ban;
- C0B-7C runs once against the pinned pair and records the recovered decision.

No C0B-7 card authorizes an Ollama call.
