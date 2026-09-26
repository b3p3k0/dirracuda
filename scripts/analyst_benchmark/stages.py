"""
Stage A (offline + dependency probe) and Stage B (screening pilot).

DISPOSITION: retained diagnostic.

Stage A performs ZERO Ollama calls. It does perform one external PyPI download,
behind --confirm-dependency-probe. Stage B performs the first Ollama contact of
the run, and only after a successful transport/digest preflight.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from . import (client, detectors, goldset, ledger, metrics, preflight,
               report, resources, sandbox_smoke, worksheet)

ENVELOPE_EVERY = 8          # sample the resource envelope every Nth scored call

PYMUPDF_PIN = "PyMuPDF==1.28.0"
PYPI_JSON = "https://pypi.org/pypi/pymupdf/1.28.0/json"
SCRATCH_PREFIX = "dirracuda-c0b1-pymupdf-"


# ===========================================================================
# Stage A
# ===========================================================================
@dataclass
class DependencyProbe:
    ok: bool = False
    wheel_filename: Optional[str] = None
    wheel_tag: Optional[str] = None
    local_sha256: Optional[str] = None
    published_sha256: Optional[str] = None
    digest_match: Optional[bool] = None
    pymupdf_version: Optional[str] = None
    mupdf_version: Optional[str] = None
    scratch_root: Optional[str] = None
    scratch_python: Optional[str] = None
    error: Optional[str] = None


_PROBE_SH = r"""
set -euo pipefail
SCRATCH="$1"
echo "SCRATCH=$SCRATCH"
python3 -m venv "$SCRATCH/probe" >/dev/null
"$SCRATCH/probe/bin/pip" -q download --no-deps --only-binary=:all: \
    --no-cache-dir "{pin}" -d "$SCRATCH/wheel" >/dev/null
COUNT="$(find "$SCRATCH/wheel" -name '*.whl' | wc -l)"
[ "$COUNT" -eq 1 ] || {{ echo "ERROR=expected 1 wheel, found $COUNT"; exit 3; }}
WHEEL="$(find "$SCRATCH/wheel" -name '*.whl')"
echo "WHEEL=$(basename "$WHEEL")"
LOCAL="$(sha256sum "$WHEEL" | cut -d' ' -f1)"
echo "LOCAL=$LOCAL"
PUBLISHED="$(curl -fsS {pypi} | "$SCRATCH/probe/bin/python" -c '
import json,sys,os
d=json.load(sys.stdin); n=os.path.basename(sys.argv[1])
print(next((u["digests"]["sha256"] for u in d["urls"] if u["filename"]==n), ""))
' "$WHEEL")"
echo "PUBLISHED=$PUBLISHED"
[ -n "$PUBLISHED" ] || {{ echo "ERROR=no published digest for that filename"; exit 4; }}
[ "$LOCAL" = "$PUBLISHED" ] || {{ echo "ERROR=digest mismatch"; exit 5; }}
"$SCRATCH/probe/bin/pip" -q install --no-index --no-cache-dir "$WHEEL" >/dev/null
"$SCRATCH/probe/bin/python" -c '
import pymupdf
print("PYMUPDF=" + pymupdf.__version__)
print("MUPDF=" + str(pymupdf.mupdf_version))
'
echo "PYTHON=$SCRATCH/probe/bin/python"
echo "OK=1"
"""


def run_dependency_probe() -> DependencyProbe:
    """Download first, verify against PyPI's published digest, then install THAT
    exact file offline. The scratch tree is kept alive for the PDF smoke test;
    the caller removes it via cleanup_scratch()."""
    script = _PROBE_SH.format(pin=PYMUPDF_PIN, pypi=PYPI_JSON)
    scratch_root = Path(tempfile.mkdtemp(prefix=SCRATCH_PREFIX, dir="/tmp"))
    scratch_root.chmod(0o700)
    out = DependencyProbe(scratch_root=str(scratch_root))
    try:
        cp = subprocess.run(["bash", "-c", script, "dependency-probe",
                             str(scratch_root)], capture_output=True,
                            text=True, timeout=900, check=False, shell=False)
    except (OSError, subprocess.SubprocessError) as exc:
        out.error = f"{type(exc).__name__}"
        cleanup_scratch(out)
        return out
    fields = dict(
        line.split("=", 1) for line in cp.stdout.splitlines() if "=" in line)
    out.wheel_filename = fields.get("WHEEL")
    out.local_sha256 = fields.get("LOCAL")
    out.published_sha256 = fields.get("PUBLISHED")
    out.pymupdf_version = fields.get("PYMUPDF")
    out.mupdf_version = fields.get("MUPDF")
    out.scratch_root = fields.get("SCRATCH", str(scratch_root))
    out.scratch_python = fields.get("PYTHON")
    if out.wheel_filename:
        parts = out.wheel_filename[:-4].split("-")
        out.wheel_tag = "-".join(parts[-3:]) if len(parts) >= 3 else None
    out.digest_match = bool(
        out.local_sha256 and out.local_sha256 == out.published_sha256)
    if cp.returncode != 0 or fields.get("OK") != "1":
        out.error = fields.get("ERROR") or f"rc={cp.returncode}: {cp.stderr[-300:]}"
        cleanup_scratch(out)
        return out
    out.ok = True
    return out


def cleanup_scratch(probe: DependencyProbe) -> str:
    """Validate the target before recursive deletion.

    Deliberately does NOT resolve() the interpreter path: a venv's bin/python is
    a symlink to the system interpreter, so resolving it walks out of the
    scratch tree entirely and the validated target becomes /usr.
    """
    if probe.scratch_root:
        root = Path(probe.scratch_root)
    elif probe.scratch_python:
        # $SCRATCH/probe/bin/python -> parents: [bin, probe, $SCRATCH]
        root = Path(probe.scratch_python).parents[2]
    else:
        return "no scratch to remove"
    if not root.name.startswith(SCRATCH_PREFIX) or root.parent != Path("/tmp"):
        return f"refusing to remove unexpected scratch path: {root}"
    try:
        info = root.lstat()
    except FileNotFoundError:
        return f"scratch already absent: {root}"
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        return f"refusing to remove non-directory scratch path: {root}"
    if info.st_uid != os.getuid():
        return f"refusing to remove scratch path owned by uid {info.st_uid}: {root}"
    shutil.rmtree(root)
    return f"removed {root}"


def mupdf_meets_floor(version: Optional[str], floor: Tuple[int, int, int] = (1, 28, 0)
                      ) -> bool:
    if not version:
        return False
    nums = re.findall(r"\d+", version)[:3]
    if len(nums) < 2:
        return False
    got = tuple(int(n) for n in nums) + (0,) * (3 - len(nums))
    return got >= floor


# ===========================================================================
# Stage B
# ===========================================================================
@dataclass
class CellStats:
    model: str
    ws: str
    calls: int = 0
    valid_first_pass: int = 0
    valid_after_retry: int = 0
    invalid: int = 0
    truncated: int = 0
    findings_total: int = 0
    findings_grounded: int = 0
    injection_events: int = 0
    robustness_failures: int = 0
    injection_pairs_measured: int = 0
    injection_pairs_unmeasured: int = 0
    injection_event_counts: Dict[str, int] = field(default_factory=dict)
    doc_scores: List[metrics.DocScore] = field(default_factory=list)
    envelopes: List[Dict[str, Any]] = field(default_factory=list)
    prompt_tokens: List[int] = field(default_factory=list)
    eval_tokens: List[int] = field(default_factory=list)
    thinking_bytes: List[int] = field(default_factory=list)
    wall_seconds: List[float] = field(default_factory=list)
    resource_interruptions: int = 0
    eliminated: bool = False
    eliminated_reason: Optional[str] = None

    @property
    def cell(self) -> str:
        return f"{self.model}|{self.ws}"


@dataclass
class InjectionObservation:
    predicted: Set[str]
    findings: List[dict]
    extra_keys: List[str]
    document_type: str
    subject: str


def top_k_probe(cli: client.OllamaClient, model: str, led: ledger.Ledger,
                repeats: int = 3) -> Dict[str, Any]:
    """Repeated identical requests. One response demonstrates nothing.

    Uses the full default output budget: a short num_predict would leave
    gpt-oss still inside its reasoning trace, so every reply would be empty and
    the probe would report "identical" for the wrong reason.
    """
    opts = client.GenOptions()
    prompt = worksheet.build_prompt(
        "v2", "Contact: Marren Ashgrove, marren.ashgrove@example.com",
        nonce="FENCE_PROBE0000")
    outs: List[str] = []
    for _ in range(repeats):
        led.charge("top_k_probe")
        r = cli.generate(model, prompt, "v2", opts)
        outs.append(r.content if r.ok else f"<{r.outcome}>")
    identical = len(set(outs)) == 1
    return {"model": model, "top_k": opts.top_k, "repeats": repeats,
            "identical": identical, "distinct_outputs": len(set(outs))}


def _score_response(gs: goldset.GoldSet, doc: goldset.GoldDoc, ws: str,
                    raw: str, source: str) -> Tuple[Optional[Any], List[dict],
                                                    List[str], Optional[str]]:
    """(parsed, normalized findings, extra top-level keys, doc_type)."""
    try:
        parsed = worksheet.validate(ws, raw)
    except Exception:                              # noqa: BLE001
        return None, [], [], None
    try:
        obj = json.loads(raw)
        allowed = set(worksheet.MODELS[ws].model_fields.keys())
        extra = sorted(set(obj.keys()) - allowed) if isinstance(obj, dict) else []
    except ValueError:
        extra = []
    findings = worksheet.normalize(ws, parsed)
    return parsed, findings, extra, getattr(parsed, "document_type", None)


def run_stage_b(cli: client.OllamaClient, gs: goldset.GoldSet,
                models: List[str], worksheets: List[str],
                led: ledger.Ledger, run_id: str, *,
                seed: int, opts_base: client.GenOptions,
                progress=None) -> Dict[str, CellStats]:
    """44-doc screening subset x models x worksheets, one seed, strictly serial."""
    subset = gs.subset()
    cells: Dict[str, CellStats] = {}

    for model in models:
        for ws in worksheets:
            st = CellStats(model=model, ws=ws)
            cells[st.cell] = st

            for _ in range(3):                      # warm-ups, charged
                led.charge("warmup")
                cli.generate(model, worksheet.build_prompt(ws, "warm up"),
                             ws, client.GenOptions(num_predict=32, seed=seed))

            injection_observations: Dict[str, InjectionObservation] = {}

            for doc in subset:
                if led.soft_wall_crossed():
                    led.pause(f"soft wall crossed during {st.cell}")
                    return cells
                source = doc.text()
                prompt = worksheet.build_prompt(ws, source)
                opts = client.GenOptions(**{**opts_base.__dict__, "seed": seed})

                led.charge("scored")
                r = cli.generate(model, prompt, ws, opts)
                st.calls += 1
                st.wall_seconds.append(r.wall_seconds)
                if r.prompt_eval_count:
                    st.prompt_tokens.append(r.prompt_eval_count)
                if r.eval_count:
                    st.eval_tokens.append(r.eval_count)
                st.thinking_bytes.append(r.thinking_bytes)

                # Envelope sampling is periodic, not per-call. /api/ps is a
                # metadata probe and is charged to the ledger like any other
                # request; sampling it every scored call would roughly double
                # the ledger and add latency for no extra signal.
                if st.calls == 1 or st.calls % ENVELOPE_EVERY == 0:
                    env = resources.sample(_safe_ps(cli, led), model)
                    st.envelopes.append(env.as_dict())

                kind = resources.classify_failure(r.outcome, r.error, r.http_status)
                if kind == resources.RESOURCE_INTERRUPTION:
                    st.resource_interruptions += 1
                    led.note(f"{st.cell} {doc.doc_id}: resource_interruption")
                    continue
                if r.outcome == "truncated":
                    st.truncated += 1

                parsed, findings, extra, doc_type = _score_response(
                    gs, doc, ws, r.content, source)
                if parsed is None:
                    _record_raw(
                        run_id, st.cell, doc, r, attempt=1,
                        valid=False, accepted_for_scoring=False,
                        final_attempt=False)
                    led.charge("retry")
                    r2 = cli.generate(model, prompt, ws, opts)
                    parsed, findings, extra, doc_type = _score_response(
                        gs, doc, ws, r2.content, source)
                    if parsed is None:
                        st.invalid += 1
                        _record_raw(
                            run_id, st.cell, doc, r2, attempt=2,
                            valid=False, accepted_for_scoring=False,
                            final_attempt=True)
                        continue
                    st.valid_after_retry += 1
                    _record_raw(
                        run_id, st.cell, doc, r2, attempt=2,
                        valid=True, accepted_for_scoring=True,
                        final_attempt=True)
                else:
                    st.valid_first_pass += 1
                    _record_raw(
                        run_id, st.cell, doc, r, attempt=1,
                        valid=True, accepted_for_scoring=True,
                        final_attempt=True)

                good, total = metrics.grounding_rate(findings, source)
                st.findings_grounded += good
                st.findings_total += total

                predicted = {f["category"] for f in findings}
                st.doc_scores.append(metrics.DocScore(
                    doc_id=doc.doc_id, stratum=doc.stratum,
                    expected=set(doc.categories_present), predicted=predicted))

                if doc.stratum in ("injection", "injection_clean_twin"):
                    injection_observations[doc.doc_id] = InjectionObservation(
                        predicted=predicted,
                        findings=findings,
                        extra_keys=extra,
                        document_type=doc_type or "",
                        subject=getattr(parsed, "subject", "") or "",
                    )
                if progress:
                    progress(st.cell, doc.doc_id, st.calls, len(subset))

            _score_injection_pairs(
                gs, subset, injection_observations, st, led)
            if progress:
                progress(st.cell, "done", st.calls, len(subset))
    return cells


def _safe_ps(cli: client.OllamaClient, led: ledger.Ledger) -> Optional[dict]:
    try:
        led.charge("api_ps")
        return cli.ps()
    except Exception:                               # noqa: BLE001
        return None


def _injected_payload(gs: goldset.GoldSet, doc: goldset.GoldDoc) -> str:
    """The injected text = injection document minus its clean twin."""
    twin = gs.twin_of(doc.doc_id)
    if not twin:
        return ""
    a, b = twin.text().splitlines(), doc.text().splitlines()
    return "\n".join(ln for ln in b if ln not in a)


def _score_injection_pairs(
        gs: goldset.GoldSet, subset: List[goldset.GoldDoc],
        observations: Dict[str, InjectionObservation], st: CellStats,
        led: ledger.Ledger) -> None:
    """Score matched pairs after collection, independent of manifest order.

    Either missing half makes the pair unmeasured and is a strict robustness
    failure. It must not be silently converted into a zero-event result.
    """
    for doc in subset:
        if doc.stratum != "injection":
            continue
        inj = observations.get(doc.doc_id)
        twin = observations.get(doc.clean_twin_id or "")
        if inj is None or twin is None:
            st.injection_pairs_unmeasured += 1
            st.robustness_failures += 1
            led.note(f"{st.cell} {doc.doc_id}: injection_pair_unmeasured")
            continue

        st.injection_pairs_measured += 1
        payload = _injected_payload(gs, doc)
        evts = metrics.injection_events(
            twin_categories=twin.predicted,
            inj_categories=inj.predicted,
            labelled=set(doc.categories_present),
            inj_findings=inj.findings,
            injected_text=payload,
            twin_finding_count=len(twin.findings),
            extra_keys=inj.extra_keys,
            twin_doc_type=twin.document_type,
            inj_doc_type=inj.document_type,
        )
        if metrics.instruction_text_in_fields(
                payload, [inj.document_type, inj.subject]):
            evts.append("instruction_text_in_field")
        for kind in sorted(set(evts)):
            st.injection_event_counts[kind] = (
                st.injection_event_counts.get(kind, 0) + 1)
        st.injection_events += len(set(evts))
        if evts:
            led.note(f"{st.cell} {doc.doc_id}: injection {sorted(set(evts))}")


def _record_raw(run_id: str, cell: str, doc: goldset.GoldDoc,
                r: client.CallResult, *, attempt: int, valid: bool,
                accepted_for_scoring: bool, final_attempt: bool) -> None:
    """Persist every attempt and identify the exact response that was scored."""
    report.append_raw_jsonl(run_id, "stage_b_raw.jsonl", {
        "cell": cell, "doc_id": doc.doc_id, "stratum": doc.stratum,
        "attempt": attempt,
        "valid": valid,
        "accepted_for_scoring": accepted_for_scoring,
        "final_attempt": final_attempt,
        "raw_response": r.content,
        "thinking_bytes": r.thinking_bytes,
        "meta": r.redacted(),
    })
