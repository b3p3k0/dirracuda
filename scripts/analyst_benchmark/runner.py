"""
CLI, confirmation gates, and stage dispatch.

DISPOSITION: retained diagnostic.

Gate discipline (BENCHMARK_PROTOCOL_C0B1.md §9):
  - A NO-ARGUMENT invocation does nothing: usage to stderr, exit 2, zero side
    effects. That is the real safety check; --self-test is a separate mode.
  - --confirm-dependency-probe gates the Stage A PyPI download.
  - --confirm-live gates settings reads, get_paths(), and ALL Ollama contact.
    No request of any kind - including /api/tags and /api/show - happens before
    --confirm-live and a successful transport/digest preflight.
  - --preflight-only stops after preflight: no metadata probe, no top_k probe,
    no scored request.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

STAGE_B_CALL_CAP = 400
STAGE_B_SOFT_WALL_MIN = 120.0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m scripts.analyst_benchmark",
        description="Analyst benchmark instrument (C0B-1, Stages A and B).",
        epilog="Confirmation flags are mandatory. Nothing runs without one.")
    p.add_argument("--stage", choices=["A", "B"], help="stage to execute")
    p.add_argument("--self-test", action="store_true",
                   help="offline instrument self-check; no network, no Ollama")
    p.add_argument("--leak-scan", action="store_true",
                   help="scan task deltas against the committable allowlist")
    p.add_argument("--create-leak-baseline", action="store_true",
                   help="exclusively create the explicit pre-task baseline")
    p.add_argument("--mode", choices=["public"], default="public",
                   help="C0B-1 supports public leakage scanning only")
    p.add_argument("--baseline-file", type=Path,
                   help="explicit owner-only baseline outside the repository")
    p.add_argument("--raw-artifact", type=Path, action="append", default=[],
                   help="explicit owner-only Stage-B raw JSONL (repeatable)")
    p.add_argument("--confirm-dependency-probe", action="store_true",
                   help="authorise the Stage A PyMuPDF download from PyPI")
    p.add_argument("--confirm-live", action="store_true",
                   help="authorise settings/get_paths access and Ollama contact")
    p.add_argument("--preflight-only", action="store_true",
                   help="run transport preflight and stop")
    p.add_argument("--confirm-exclusive-ollama", action="store_true",
                   help="authorise cold-load measurement (not used by default)")
    p.add_argument("--models", default="gpt-oss:20b,qwen3.6:35b,qwen3.6:27b")
    p.add_argument("--worksheet", default="v1,v2")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--endpoint", default=None)
    p.add_argument("--call-cap", type=int, default=STAGE_B_CALL_CAP)
    p.add_argument("--soft-wall-minutes", type=float,
                   default=STAGE_B_SOFT_WALL_MIN)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args_in = sys.argv[1:] if argv is None else argv
    parser = build_parser()

    # No arguments: do nothing at all. No imports, no paths, no network.
    if not args_in:
        parser.print_usage(sys.stderr)
        print("\nRefusing to run without an explicit mode and confirmation flag.\n"
              "  offline self-check : --self-test\n"
              "  stage A            : --stage A --confirm-dependency-probe\n"
              "  preflight only     : --confirm-live --preflight-only\n"
              "  stage B pilot      : --stage B --confirm-live\n"
              "  leak scan          : --leak-scan --mode public",
              file=sys.stderr)
        return 2

    args = parser.parse_args(args_in)

    if args.self_test:
        return _self_test()
    if args.create_leak_baseline:
        if not args.baseline_file:
            print("--baseline-file is required to create a leak baseline.",
                  file=sys.stderr)
            return 2
        return _create_leak_baseline(args.baseline_file)
    if args.leak_scan:
        if not args.baseline_file or not args.raw_artifact:
            print("--leak-scan requires --baseline-file and at least one "
                  "--raw-artifact.", file=sys.stderr)
            return 2
        return _leak_scan(args.mode, args.baseline_file, args.raw_artifact)
    if args.stage == "A":
        if not args.confirm_dependency_probe:
            print("--stage A performs an external PyPI download; "
                  "--confirm-dependency-probe is required.", file=sys.stderr)
            return 2
        return _stage_a()
    if args.preflight_only or args.stage == "B":
        if not args.confirm_live:
            print("--confirm-live is required before any Ollama contact.",
                  file=sys.stderr)
            return 2
        return _live(args)

    parser.print_usage(sys.stderr)
    return 2


# ---------------------------------------------------------------------------
def _self_test() -> int:
    """Offline: gold set integrity, protocol pin, pure scorers. No network."""
    from . import goldset, metrics, protocol, worksheet, chunker

    gs = goldset.load(verify=True)
    pin = protocol.pin()
    protocol.verify(pin)
    for ws in ("v1", "v2"):
        worksheet.json_schema(ws)
    ch = chunker.chunk("x" * 9000, chunk_chars=4000, overlap_chars=256)
    probe_doc = gs.docs["pos_pii_001"].text()
    probe_quote = gs.docs["pos_pii_001"].expected_identifiers[0]
    g = metrics.ground_finding(probe_quote, probe_doc.index(probe_quote), probe_doc)
    print(json.dumps({
        "gold_set_version": gs.version,
        "documents": len(gs.docs),
        "screening_subset": len(gs.screening_subset),
        "protocol_sha256": pin.sha256,
        "chunks_for_9000_chars": len(ch),
        "grounding_probe_ok": g.grounded,
    }, indent=2))
    return 0


def _stage_a() -> int:
    from . import goldset, protocol, sandbox_smoke, stages

    print("== Stage A: offline instrument + dependency probe "
          "(zero Ollama calls) ==")
    gs = goldset.load(verify=True)
    pin = protocol.pin()
    print(f"gold set        : {len(gs.docs)} documents, version {gs.version}")
    print(f"protocol pin    : {pin.sha256}")

    print("\n-- PyMuPDF lifecycle (download -> PyPI digest -> offline install) --")
    probe = stages.run_dependency_probe()
    try:
        print(f"wheel           : {probe.wheel_filename}")
        print(f"local sha256    : {probe.local_sha256}")
        print(f"published sha256: {probe.published_sha256}")
        print(f"digest match    : {probe.digest_match}")
        print(f"pymupdf         : {probe.pymupdf_version}")
        print(f"mupdf (embedded): {probe.mupdf_version}")
        if not probe.ok:
            print(f"FAILED          : {probe.error}", file=sys.stderr)
            return 1
        if not stages.mupdf_meets_floor(probe.mupdf_version):
            print(f"FAILED          : embedded MuPDF {probe.mupdf_version} "
                  f"below the 1.28.0 floor; not selecting a pin.",
                  file=sys.stderr)
            return 1

        print("\n-- bubblewrap smoke --")
        checks = sandbox_smoke.run_all(scratch_python=probe.scratch_python)
        for check in checks:
            print(f"  [{'PASS' if check.ok else 'FAIL'}] "
                  f"{check.name}: {check.detail}")

        out = {
            "stage": "A",
            "gold_set_documents": len(gs.docs),
            "protocol_sha256": pin.sha256,
            "pymupdf": probe.__dict__,
            "sandbox": [check.__dict__ for check in checks],
        }
        artifact = _write_stage_a_artifact(out)
        print(f"\nresult artifact : {artifact}")
        failed = [check.name for check in checks if not check.ok]
        print(f"\nStage A: {'PASS' if not failed else 'FAIL ' + str(failed)}")
        return 0 if not failed else 1
    finally:
        print("\n" + stages.cleanup_scratch(probe))


def _write_stage_a_artifact(payload: dict,
                            output_dir: Path = Path("/tmp")) -> Path:
    """Write an owner-only, collision-resistant Stage A diagnostic."""
    fd, raw_path = tempfile.mkstemp(prefix="dirracuda-c0b1-stage-a-",
                                    suffix=".json", dir=str(output_dir))
    path = Path(raw_path)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        path.unlink(missing_ok=True)
        raise
    return path


def _live(args) -> int:
    from . import (client, goldset, ledger, metrics, preflight, protocol,
                   report, stages)

    endpoint = args.endpoint or preflight.DEFAULT_ENDPOINT
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    worksheets = [w.strip() for w in args.worksheet.split(",") if w.strip()]

    # Establish immutable provenance before the first Ollama request, including
    # preflight. --confirm-live has already authorized this get_paths() access.
    try:
        pin = protocol.pin()
        run_id = report.new_run_id()
        report.create_run(run_id)
        led = ledger.Ledger(hard_cap=args.call_cap,
                            soft_wall_seconds=args.soft_wall_minutes * 60.0)
        report.write_raw(run_id, "run_header.json", {
            "run_id": run_id,
            "created_epoch": int(time.time()),
            "protocol": pin._asdict(),
            "endpoint": endpoint,
            "requested_models": models,
            "expected_digests": {m: preflight.APPROVED_DIGESTS.get(m)
                                 for m in models},
            "worksheets": worksheets,
            "seed": args.seed,
            "call_cap": args.call_cap,
        })
    except Exception as exc:                            # noqa: BLE001
        print(f"provenance setup FAILED: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1

    print(f"== Transport preflight ({endpoint}) ==")
    pre = preflight.run_preflight(endpoint, models, charge=led.charge)
    for c in pre.checks:
        print(f"  [{'PASS' if c.ok else 'FAIL'}] {c.name}: {c.detail}")
    print(f"\n{preflight.LOCAL_ONLY_STATEMENT}\n")
    if not pre.ok:
        if led.state == ledger.STATE_RUNNING:
            led.state = ledger.STATE_BLOCKED
        led.note("failed: transport/digest preflight")
        report.write_raw(run_id, "preflight_result.json", {
            "ok": False, "checks": pre.checks, "server_version": pre.server_version,
            "resolved_digests": pre.resolved, "ledger": led.summary(),
        })
        print("preflight FAILED; no document is sent.", file=sys.stderr)
        return 1
    if args.preflight_only:
        led.state = ledger.STATE_COMPLETE
        protocol.verify(pin)
        path = report.write_raw(run_id, "preflight_result.json", {
            "ok": True, "checks": pre.checks, "server_version": pre.server_version,
            "resolved_digests": pre.resolved, "ledger": led.summary(),
        })
        print("preflight-only: stopping before any metadata or scored request.")
        print(f"provenance sink: {path}")
        return 0

    gs = goldset.load(verify=True)
    protocol.verify(pin)
    report.write_raw(run_id, "preflight_result.json", {
        "ok": True, "checks": pre.checks, "server_version": pre.server_version,
        "resolved_digests": pre.resolved, "ledger": led.summary(),
    })
    cli = client.OllamaClient(endpoint)

    print(f"== Stage B pilot (run {run_id}) ==")
    print(f"subset {len(gs.screening_subset)} docs x {len(models)} models "
          f"x {len(worksheets)} worksheets, seed {args.seed}")

    meta = {}
    for m in models:
        led.charge("api_show")
        try:
            info = cli.show(m)
            meta[m] = {"parameters": info.get("parameters"),
                       "template_sha256": hashlib.sha256(
                           str(info.get("template", "")).encode("utf-8")
                       ).hexdigest()}
        except Exception as exc:                    # noqa: BLE001
            meta[m] = {"error": type(exc).__name__}
    print(f"/api/show captured for {len(meta)} models")

    probes = [stages.top_k_probe(cli, m, led) for m in models]
    for p in probes:
        print(f"  top_k probe {p['model']}: identical={p['identical']} "
              f"({p['distinct_outputs']} distinct over {p['repeats']})")

    started = time.time()
    try:
        cells = stages.run_stage_b(
            cli, gs, models, worksheets, led, run_id,
            seed=args.seed, opts_base=client.GenOptions(seed=args.seed),
            progress=lambda cell, doc, n, tot: print(
                f"    {cell} {n}/{tot} {doc}", flush=True))
    except ledger.HardCapExceeded as exc:
        led.state = ledger.STATE_BLOCKED
        led.note(f"failed: {type(exc).__name__}")
        report.write_raw(run_id, "run_failure.json", {
            "error": type(exc).__name__, "ledger": led.summary(),
        })
        print(f"\nBLOCKED: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:                            # noqa: BLE001
        led.state = ledger.STATE_BLOCKED
        led.note(f"failed: {type(exc).__name__}")
        report.write_raw(run_id, "run_failure.json", {
            "error": type(exc).__name__, "ledger": led.summary(),
        })
        print(f"\nBLOCKED: benchmark raised {type(exc).__name__}", file=sys.stderr)
        return 1

    verdicts = [
        metrics.screen(st.cell, calls=st.calls,
                       valid_first_pass=st.valid_first_pass,
                       grounded=st.findings_grounded,
                       findings=st.findings_total,
                       injection_events=st.injection_events,
                       robustness_failures=st.robustness_failures)
        for st in cells.values()]

    try:
        protocol.verify(pin)
    except protocol.ProtocolMismatch as exc:
        led.state = ledger.STATE_BLOCKED
        led.note("failed: protocol changed during run")
        report.write_raw(run_id, "run_failure.json", {
            "error": type(exc).__name__, "ledger": led.summary(),
        })
        print(f"\nBLOCKED: {exc}", file=sys.stderr)
        return 1

    if led.state == ledger.STATE_RUNNING:
        led.state = ledger.STATE_COMPLETE

    payload = {
        "run_id": run_id, "protocol_sha256": pin.sha256,
        "endpoint": endpoint, "server_version": pre.server_version,
        "resolved_digests": pre.resolved, "seed": args.seed,
        "elapsed_seconds": round(time.time() - started, 1),
        "ledger": led.summary(), "api_show": meta, "top_k_probes": probes,
        "cells": {k: {kk: vv for kk, vv in v.__dict__.items()
                      if kk not in ("doc_scores",)} for k, v in cells.items()},
        "verdicts": [v.__dict__ for v in verdicts],
    }
    path = report.write_raw(run_id, "stage_b_summary.json", payload)
    print(f"\nraw sink: {path} (0600, outside the repository)")
    print("\n" + report.render_screening_table(verdicts))
    print(f"\nledger: {led.summary()['calls_total']} calls "
          f"(cap {led.hard_cap}), state {led.state}")
    return 0


def _create_leak_baseline(path: Path) -> int:
    from . import leakscan
    try:
        created = leakscan.create_baseline(path)
    except Exception as exc:                            # noqa: BLE001
        print(f"baseline creation failed closed: {exc}", file=sys.stderr)
        return 1
    print(f"baseline created: {created} (0600)")
    return 0


def _leak_scan(mode: str, baseline_file: Path,
               raw_artifacts: List[Path]) -> int:
    from . import leakscan
    return leakscan.run(mode=mode, baseline_path=baseline_file,
                        raw_artifacts=raw_artifacts)


if __name__ == "__main__":
    raise SystemExit(main())
