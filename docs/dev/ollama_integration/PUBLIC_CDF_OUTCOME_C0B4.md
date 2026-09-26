# Analyst Public Confirmation — C0B-4 Outcome

Date: 2026-08-11
Status: **VERIFIED `INCONCLUSIVE/seed17_no_qualifier`**

## Outcome

C0B-4 preserved the exact C0B-3 finalist and tested the E6 grounded-duplicate correction.
The first child, `c0b4-20260811-190217-ac970de2a2f6021965bcd948`, failed closed before
transport because creation and revalidation hashed different filesystem probe modes. It
made zero calls and remains immutable.

E7 corrected that pre-contact mismatch prospectively. The one authorized replacement,
`c0b4-20260811-210848-d2b52272f3aabb156f55d166`, ran from clean detached commit
`377e4eb9e277d24d9ef1699d3a427253c052df75`. It completed F72 seed 17, then stopped
because two negative documents exceeded the frozen one-document false-positive limit.
F72 seed 20260804 and C44 were never activated.

## Measured result

| Metric | Result |
|---|---:|
| Charged calls | 96 — 92 scored + 4 controls/preflights |
| Invocations | 1 |
| Completed chunks | 92 / 92 |
| Schema retries | 0 |
| First-pass / eventual invalid chunks | 0 / 0 |
| Raw findings grounded | 168 / 168 |
| Retained findings grounded | 168 / 168 |
| Duplicate normalization | 0 rows in 0 chunks |
| Category recall | 8 / 8 in each category |
| Macro / micro F1 | 35 / 36; 32 / 33 |
| Injection pairs | 4 / 4 measured; 0 events; 0 robustness failures |
| Boundary documents | 12 / 12 |
| Context allocation | 8192; passed |
| Length / channel / context-headroom failures | 0 / 0 / 0 |
| Negative false-positive documents | **2 / 16** |

The only lane failure was `negative_false_positive_above_1`. Public fixtures
`neg_nearmiss_014` and `neg_nearmiss_019` each produced one grounded financial
suggestion for an invalid placeholder settlement string. Both belong to the same
public template family, `near_miss_invalid_iban_template_placeholder`. Financial
precision was 4/5 and financial F1 was 8/9; the other category metrics were perfect.

Cancellation/following-request health was not run because the quality miss had already
made the lane terminal. Say **all other measured gates passed**—later controls and lanes
were not measured. Raw response text is owner-only and is not included here.

## Integrity receipt

Read-only verification returned `ok: true` with no errors. The checkpoint and immutable
snapshot independently replay to the same terminal reason, failure reason and
false-positive count.

| Artifact | SHA-256 |
|---|---|
| Checkpoint database | `c6d3e8e8dfeba129911ab034bb8301f028722227bf6c3e1d3817b1fa461d4285` |
| Run header | `301719b3a4d570bb87017f01bfb27d16db2d66c652ed251c56e71c423b2e7f0b` |
| Benchmark protocol | `71bde3bdd02f338216aa9a964a21207db3d1d4c80f0e676dab04776f7f833ae0` |
| Task tree | `2e6c04acee48ce4b01f591239568b260b7dc6d5f4273c579c083513852f459fe` |
| Master plan | `7faea74d2d2d856658a3854af04576c83ba3f1cacb1fbbe939ad87db58e11832` |
| F72/17 aggregate | `4b86e1fc4a3e9ccf198247da8782a9be688c606f4a8a2dce7fd7b0a5c717215e` |
| Terminal result | `7c9a387e2b3b17bb028eb3c98156a54059ce23d316174b6ec81030ed0ac73497` |
| Completion | `5b2144227b15a89e17a1ec235976cee4a26e193b5dee31c5b91d09ab7f0e051c` |
| Backup anchor | `60ac16a8962a5b87b16cc5bf7beeaae3d8009cf4d26a5441656ef125d2602358` |
| Backup snapshot | `f31a38d269a13c6df8b9e264f8d149d161504e3e3cdcae1ea0f1fd2a253fe94b` |
| Backup receipt | `e758b8f1bbe8f1a2d2c4edf048b64ff7f8be82392c26df18427e6a3e87546c75` |

## Decision

C0B-4 remains immutable and is not rescored, resumed or reclassified. The HI accepted E8
prospectively for Analyst's recommendation-only role: C0B-5 may test a strictly bounded
review workload under new identities and fresh generation conditions. This is an
operational policy decision, not a claim of population accuracy.

C1 and private Stage E remain held pending a verified C0B-5 result and HI review.
