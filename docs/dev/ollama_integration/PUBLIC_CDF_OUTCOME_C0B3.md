# Analyst Public C0B-3 Outcome

Date: 2026-08-11
Run ID: `c0b3-20260809-154924-19afcaab26984160f20ec075`
Terminal: **`INCONCLUSIVE/no_seed1_qualifier`**

This is the immutable public-only outcome for the C0B-3 assistive confirmation run.
It contains no document text, model response, reasoning trace or private-corpus result.

## What happened

C0B-3 completed C, D and the first Stage-F seed under the bounded false-positive
policy. The fixed finalist was:

- model: `qwen3.6:27b`
- digest: `a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e`
- worksheet: v2
- chunk/overlap: 8,000 / 256 characters
- context/output: 8,192 / 1,024 tokens

The D4 confirmation passed every gate. Stage F then completed all 92 seed-1 chunks.
Ninety-one were valid. One output-truncation fixture produced a structurally valid
answer with one repeated category/quote row. The protocol treated that semantic error
as terminal after one byte-identical retry, so no seed-1 qualifier existed and later
seeds were not activated.

## Measured result

- 703 charged Ollama calls in the complete run;
- 92/92 seed-1 chunks reached a terminal work state;
- 91 valid chunks and one completed-invalid chunk;
- 185/185 authoritative raw findings grounded;
- 185/185 retained findings grounded;
- macro and micro F1 both 1.0 on the scored controls;
- zero negative false-positive documents;
- zero prompt-injection events or robustness failures;
- 12/12 boundary documents passed;
- zero length outcomes and zero context-headroom failures;
- cancellation/following-request health passed.

The invalid response itself is not included here. Postmortem inspection established:

- its only semantic error was `duplicate_evidence`;
- all 16 original quotes were exact source substrings;
- one category/quote key appeared twice and the source contained that value twice;
- removing the later duplicate yielded 15 unique, grounded findings and a valid contact
  classification;
- both attempts used the same request hash and returned byte-identical content;
- Ollama returned `done_reason=stop` after 704 generated tokens, below the 1,024-token
  output allowance.

This was not a GPU, mergerfs, cancellation, context or output-length failure.

## Frozen evidence

- C0B-3 source commit:
  `dcd7e0b9504ded47dad82f25814aea54d666b268`
- checkpoint database SHA-256:
  `f8cbd0419f62656476b38c60b628b1ce20f67b097d2ce7e8bc38381d80d852e3`
- run-header SHA-256:
  `80424fbfb492cae4264798d6294337c3beaca21f2172da302114adf05d8210b2`
- protocol SHA-256:
  `031b41f6cf0f153b94c47dc55907eae77fd6600379c009434dbc752deb33022d`
- task-tree SHA-256:
  `a936817083810cebc4f017d34f4d0be5e0821c1c1e0c9ffff218df69b9782bb0`
- final-D decision SHA-256:
  `5c00ef2b06c014f7617bdb367034dc7be99fd462467961c7a15d3eac5b53d894`
- D4 aggregate SHA-256:
  `7cf23921758c6be35038456e7f4e568cef4f20618bf8ce9a9dddac5af7bab945`
- Stage-F master-plan SHA-256:
  `093af02da48d938278e791955dc196ec1c8e0dacb434ddbe204186f2fbb963de`
- seed-1 aggregate SHA-256:
  `cd87e163b2ac08b9f4de9f90291247411e80830a23a9bf635f8e6e2ba9eb11e1`
- terminal artifact SHA-256:
  `ee2c8ed8c923deba3fb30eec3dcf5af87da69de9678bf6f45303e5ffeb1d9bcc`
- completion value SHA-256:
  `6958b94d19d2a404003fba3e2d628a6828810cd503e8ced5bfc76f4f4ead5c00`
- terminal backup-receipt SHA-256:
  `398755d38227c30c527c787c3205407ed0ba47f18ccfab4b865584fb74ec14f9`
- master manifest SHA-256:
  `df609a7c5c0baaf3215bb74ef8a3598c5f8ad5b75a16caad41cf3cd1523d5e12`
- backup anchor SHA-256:
  `b37396143265013ed01361d7ec31edff3d84c358d2f6c8ce932df39b21e61c56`
- backup snapshot SHA-256:
  `262498adb36c12ef44fdeb779283e17305378cfcac33b4e87c740017453a799c`
- seed-17 old plan SHA-256:
  `2175e51108362a273f13292b95fafd724cfc90b6817b15197c93fe2055d41f31`
- seed-20260804 old plan SHA-256:
  `0a8e56835af83659ae6274772401da742feefb6e4d4121ed7c995cafbe9dcb21`

Read-only status and verification pass with `errors: []`. Seeds 17 and 20260804 each
have 92 frozen work rows but zero attempts and zero activation. Their old plans remain
part of this terminal checkpoint and will never be resumed because they bind the old
prompt and policy.

## Decision

The HI accepted a narrow prospective correction:

1. preserve this run and its outcome unchanged;
2. tell the model to emit each category/quote only once;
3. permit deterministic removal of one fully grounded redundant row under an exact,
   separately counted bound;
4. keep every other schema, grounding, quality, safety and provenance gate unchanged;
5. confirm the fixed finalist with new F72 requests at seeds 17 and 20260804;
6. restore the complete-corpus acceptance calculation with a fresh corrected C44 seed-1
   lane, the immutable parent D50/D4 result and the new seed-17 F72 result.

C0B-4 is repair/stability confirmation, not a new untouched document holdout or a claim
of population accuracy. The exact parent finalist plus a verified C0B-4 `CONFIRMED`
terminal is the accepted substitute public selection for C1 eligibility; it does not
claim that Stages C or D ran under the corrected prompt. Private Stage E remains a
separate HI decision.
