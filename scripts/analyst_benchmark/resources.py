"""
Resource envelope sampling and comparability banding.

DISPOSITION: retained diagnostic.

The GPU is a shared, variable resource. This harness never kills, stops, or
explicitly unloads neighbouring work - but its own inference consumes shared
GPU/CPU/RAM and may itself contribute to Ollama queueing and scheduler pressure.
So the envelope is recorded, not controlled, and performance is only compared
between trials collected under comparable envelopes.

Labelling discipline:
  size_vram / size        -> approximate GPU residency
  1 - size_vram / size    -> approximate CPU residency (offload)
Neither is "VRAM headroom". Device-total memory.used is never conflated with
compute-process attribution: they are different measurements.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Transient vs persistent resource outcomes.
RESOURCE_INTERRUPTION = "resource_interruption"
CANDIDATE_RESOURCE_INFEASIBLE = "candidate_resource_infeasible"

BACKOFF_BASE_S = 15.0
BACKOFF_CAP_S = 300.0
MAX_CONSECUTIVE_BACKOFFS = 6        # the 7th yields PAUSED_RESOURCE

RESIDENCY_BAND_PP = 10.0            # comparability band, percentage points


@dataclass
class Envelope:
    gpu_total_mib: Optional[int] = None
    gpu_used_mib: Optional[int] = None          # device total, all consumers
    gpu_free_mib: Optional[int] = None
    gpu_util_pct: Optional[int] = None
    compute_procs_mib: Optional[int] = None     # sum of compute-app attribution
    ram_available_mib: Optional[int] = None
    swap_used_mib: Optional[int] = None
    load1: Optional[float] = None
    ps_size: Optional[int] = None
    ps_size_vram: Optional[int] = None
    ps_context_length: Optional[int] = None
    notes: List[str] = field(default_factory=list)

    @property
    def gpu_residency(self) -> Optional[float]:
        """Approximate GPU residency in [0, 1]. Not headroom."""
        if not self.ps_size or self.ps_size_vram is None:
            return None
        return self.ps_size_vram / self.ps_size

    @property
    def cpu_residency(self) -> Optional[float]:
        """Approximate CPU residency (offload) in [0, 1]."""
        r = self.gpu_residency
        return None if r is None else 1.0 - r

    def as_dict(self) -> Dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "notes"}
        d["notes"] = list(self.notes)
        d["gpu_residency_approx"] = self.gpu_residency
        d["cpu_residency_approx"] = self.cpu_residency
        return d


def _nvidia_smi(query: str, extra: List[str]) -> List[List[str]]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run([exe, query, *extra, "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    return [[c.strip() for c in ln.split(",")]
            for ln in out.stdout.splitlines() if ln.strip()]


def _meminfo() -> Dict[str, int]:
    vals: Dict[str, int] = {}
    try:
        with open("/proc/meminfo", "r", encoding="ascii") as fh:
            for line in fh:
                k, _, rest = line.partition(":")
                parts = rest.split()
                if parts and parts[0].isdigit():
                    vals[k] = int(parts[0])          # kB
    except OSError:
        pass
    return vals


def sample(ps_payload: Optional[dict] = None,
           model: Optional[str] = None) -> Envelope:
    """One envelope reading. Read-only: never signals or unloads anything."""
    env = Envelope()

    rows = _nvidia_smi("--query-gpu",
                       ["memory.total,memory.used,memory.free,utilization.gpu"])
    if rows:
        try:
            t, u, f, g = rows[0][:4]
            env.gpu_total_mib, env.gpu_used_mib = int(t), int(u)
            env.gpu_free_mib, env.gpu_util_pct = int(f), int(g)
        except (ValueError, IndexError):
            env.notes.append("gpu query unparsed")

    procs = _nvidia_smi("--query-compute-apps", ["pid,used_memory"])
    if procs:
        total = 0
        for row in procs:
            try:
                total += int(row[1])
            except (ValueError, IndexError):
                continue
        env.compute_procs_mib = total

    mi = _meminfo()
    if "MemAvailable" in mi:
        env.ram_available_mib = mi["MemAvailable"] // 1024
    if "SwapTotal" in mi and "SwapFree" in mi:
        env.swap_used_mib = (mi["SwapTotal"] - mi["SwapFree"]) // 1024
    try:
        env.load1 = os.getloadavg()[0]
    except OSError:
        pass

    if ps_payload and model:
        for m in ps_payload.get("models", []):
            if m.get("name") == model or m.get("model") == model:
                env.ps_size = m.get("size")
                env.ps_size_vram = m.get("size_vram")
                env.ps_context_length = (m.get("context_length")
                                         or m.get("details", {}).get("context_length"))
                break

    return env


def classify_failure(outcome: str, error: Optional[str],
                     http_status: Optional[int]) -> Optional[str]:
    """Map a failed call to a resource outcome, or None if it is not one.

    An OOM is never automatically blamed on a neighbour: it lands in the
    transient bucket, and only repeated infeasibility across trials promotes a
    candidate to candidate_resource_infeasible (decided by the caller).
    """
    if http_status in (429, 500, 502, 503, 504):
        return RESOURCE_INTERRUPTION
    if outcome in ("transport_error", "timeout"):
        return RESOURCE_INTERRUPTION
    if error and "memory" in error.lower():
        return RESOURCE_INTERRUPTION
    return None


def backoff_seconds(consecutive: int) -> float:
    return min(BACKOFF_BASE_S * (2 ** max(consecutive - 1, 0)), BACKOFF_CAP_S)


def should_pause(consecutive: int) -> bool:
    return consecutive > MAX_CONSECUTIVE_BACKOFFS


def comparable(a: Envelope, b: Envelope) -> bool:
    """True when two trials may be compared on performance.

    Requires both to report approximate GPU residency within RESIDENCY_BAND_PP.
    Outside the band, timings are descriptive only and are never ranked.
    """
    ra, rb = a.gpu_residency, b.gpu_residency
    if ra is None or rb is None:
        return False
    return abs(ra - rb) * 100.0 <= RESIDENCY_BAND_PP
