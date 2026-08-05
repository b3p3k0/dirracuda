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
