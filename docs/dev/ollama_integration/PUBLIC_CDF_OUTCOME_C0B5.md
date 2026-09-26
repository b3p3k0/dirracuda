# Analyst Public Confirmation — C0B-5 Outcome

Date: 2026-08-14
Status: **`BLOCKED_PROVENANCE` — harness failure, no quality decision**

## Outcome

The sole C0B-5 child, `c0b5-20260812-123149-2f8f5e2d1710790ea0b5f4e3`, ran from
clean detached source commit `a45c2662313f2facf2e7252f676c3f44a1e39671`. It completed
the first F72 lane's 92 scored requests and context control, then recorded the planned
cancellation control. It failed closed before the following health request and before a
lane aggregate existed.

This is a harness/provenance failure, not a model-quality pass or miss. F72/20260811 and
C44/1 were never activated. C0B-5 is terminal, immutable and not eligible for resume or
repeat under its one-child rule.

## Durable evidence

| Evidence | Result |
|---|---:|
| Charged calls | 97 |
| Preflight controls | 3 valid |
| F72/20260804 scored requests | 92 valid |
| Context control | 1 valid; 8192 allocation passed |
| Cancellation control | 1 `CANCELLED_UNVERIFIED` |
| Health control | 0 |
| Lane aggregates | 0 |
| Later-lane activations | 0 |
| Backup receipts | 0 |

The checkpoint and two pre-receipt snapshots independently pass structural and semantic
replay when receipt ownership is not required. Both snapshots are byte-identical. No raw
model response, prompt, source text or exception text is included here.

| Artifact | SHA-256 |
|---|---|
| Checkpoint database | `f9a0b7c6bedb386ad46065224fc7396f667e1b0bc19cc556a57f397a4be5e00e` |
| Run header | `ff0cfd094ca066643a6030604fe4fd7c787f158b49c520968e22c2e3ac7bd34f` |
| Master plan | `021d77626801886f39938442d7bc35599bb1e409d825ba55efca42d973d5a8c0` |
| F72/20260804 lane plan | `767ec73dbb7572e8aaae9e1ac2c767ed70efe4823af84a4423c0b8e251b1dc0d` |
| Context evidence | `d907a5a7944595d5a815e247aa42ed90781a109c78843267a67e0b1a808812e9` |
| Failure evidence | `18b72690df71e9811a7aa4d84e6f486f8ebc01d6bdccc2eba669951455c6bbe8` |
| Failure result | `2e14ea551c0ea02e8ded53e4aba372f407d23fbfb33f3840b63af3d2db919eb6` |
| Each pre-receipt snapshot | `8f11053bbb594134c21c30810f646c1e519974cfde44f3c325fec01e9daee182` |

## Root cause and follow-up

The deterministic receipt failure is a connection-state leak. C0B-5 semantic replay set
`PRAGMA query_only=ON` on the live writable SQLite connection; the subsequent receipt
insert therefore failed with SQLite's read-only result. The earlier pre-health exception
was intentionally reduced to a generic content-safe provenance reason, so its exact
internal boundary cannot be recovered from the frozen evidence.

C0B-6 is the prospectively frozen repair. It keeps all prompts, scoring thresholds and
review budgets unchanged; isolates semantic replay on a separate pinned read-only
connection; persists closed failure-origin codes; proves the exact cancellation-to-health
resume boundary on real SQLite; and uses never-contacted F72 seeds 20260811 and 20260818.
C0B-5 remains descriptive history and is not an execution parent or a passing result.
