# Analyst — Contract Errata

Errata against the frozen C0A contract ([`CONTRACT.md`](CONTRACT.md)). `CONTRACT.md`
itself is **not edited** — it stays frozen as reviewed and committed at `91bb2aa`.
Each entry here records a specific, narrow correction, who accepted it, and when.

---

## E1 — GPT-OSS cannot disable thinking

**Status:** ACCEPTED (HI, 2026-08-04)
**Affects:** §7 (model worksheet), §8 (Ollama request contract)
**Raised by:** C0B-1 planning, senior review revision 3

### The conflict

§7 states "Temperature 0. Thinking disabled." and §8 states "thinking disabled where
supported". These disagree, and the stricter reading is unachievable for one of the
two candidates named in §D1.

Ollama documents that GPT-OSS is an exception to the boolean `think` parameter:

> GPT-OSS requires `think` to be set to `"low"`, `"medium"`, or `"high"`. Passing
> true/false is ignored for that model.

The trace cannot be fully disabled for GPT-OSS. Source:
https://docs.ollama.com/capabilities/thinking

### The correction

§7 and §8 align to the §8 wording: **thinking is disabled where supported.**
Concretely, for the C0B candidate set:

| Model | `think` value |
|---|---|
| `gpt-oss:20b` | `"low"` — the shortest trace the model accepts |
| `qwen3.6:35b` | `false` |
| `qwen3.6:27b` | `false` |

Preflight confirms the installed Qwen models honour `false` before any scored request.

### Consequences accepted with this erratum

1. **The streamed `thinking` field is sensitive model output.** It is bounded in
   bytes, counted against cancellation and output-budget accounting, excluded from
   operational logs and from every committed artifact, and deleted alongside other
   ephemeral raw results under the same retention policy.
2. **Candidates do not share identical reasoning settings.** This is a stated
   limitation of any comparison involving GPT-OSS, not a controlled variable. It is
   reported wherever GPT-OSS results appear.
3. **The interaction between thinking tokens and `num_predict` is measured, not
   assumed.** C0B-1 Stage B records it.

### Reversal condition

If generated reasoning traces over private material are later judged unacceptable,
`gpt-oss:20b` is eliminated from the candidate set rather than accommodated. This
erratum does not authorize reasoning traces for any purpose beyond running the
approved benchmark and the resulting Analyst pipeline.

---

## E2 — B5 terminal proof is compositional

**Status:** ACCEPTED (HI, 2026-08-06)
**Affects:** `BENCHMARK_PUBLIC_CDF_SCHEMA.md` §12.4
**Raised by:** final B5 hostile review

### The conflict

The original B5 sentence required one fake-session flow to use the actual bounded
transport through the selected C→D→F path and "every terminal branch." That is not a
coherent requirement for terminals which occur before HTTP dispatch: filesystem refusal,
an uncharged budget refusal, and explicit abandon must not send a request; abandon must
not construct the client at all. Replaying the full expensive prefix for each quality reason would also
duplicate evidence already divided deliberately between deterministic scorer tests and
durable terminal/receipt tests.

### The correction

B5 terminal proof is compositional and closed:

1. One fake-session flow uses the actual `BoundedOllamaTransport` for the complete
   C→D1→D2→D3→conditional D4→F seeds→acceptance path, including crash, retry, pause,
   cancellation and resume.
2. Focused scorer and durable-runtime tests cover every enumerated C/D/F quality-terminal
   reason and exact artifact/decision ownership. Production-generated checkpoints prove
   receipt and no-transport re-entry for every structurally distinct terminal owner path:
   C, each D phase, F seed 1, F final-seed ranking and F acceptance.
3. Transport-originated safety and provenance terminals use the actual bounded adapter.
   Pre-dispatch budget and filesystem terminals prove that no HTTP request is made;
   abandon proves that the transport client is not constructed.
4. The complete proof suite forbids socket connection and private-root discovery/access.

The authoritative closed proof-node matrix is in
`BENCHMARK_PUBLIC_CDF_SCHEMA.md` §12.4 and is checked against the frozen reason/state
sets by `test_b5_terminal_proof_matrix_is_closed_and_names_existing_nodes`.

### Consequences accepted with this erratum

This changes proof composition, not a scoring threshold, terminal outcome, runtime
behavior or live-data rule. It was accepted before B5 approval and before any public
checkpoint or scored Ollama call. Any new terminal reason must update both the strict
schema and the closed proof matrix; an unlisted reason fails the offline gate.
