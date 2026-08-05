"""
Local-only transport preflight (CONTRACT.md §8).

DISPOSITION: ported to production in C9.

Fails closed before any document reaches Ollama. The pure predicates below carry
the whole decision so they are unit-testable without a server; `run_preflight`
only sequences them and performs the two read-only HTTP calls.

Honest wording, verbatim from CONTRACT.md §8: Analyst connects only to a
literal-loopback Ollama endpoint, disables redirects, ignores ambient proxies,
rejects known cloud tag forms (`:cloud` and `-cloud`), and runs only a locally
installed model whose tag and digest match the approved benchmark. Server-level
egress control is an operator prerequisite Analyst cannot prove.
"""
from __future__ import annotations

import ipaddress
import hmac
import re
import shutil
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional
from urllib.parse import urlsplit

DEFAULT_ENDPOINT = "http://127.0.0.1:11434"

# Approved candidates, BENCHMARK_PROTOCOL_C0B1.md §2. Full immutable digests.
APPROVED_DIGESTS: Dict[str, str] = {
    "gpt-oss:20b": "17052f91a42e97930aa6e28a6c6c06a983e6a58dbb00434885a0cf5313e376f7",
    "qwen3.6:35b": "07d35212591fc27746f0a317c975a6d68754fb38e9053d82e25f06057af28522",
    "qwen3.6:27b": "a50eda8ed977ab48a12431878896b27ffd5cef552c17af3317d9623b939a7f1e",
}
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")

# Erratum E1: gpt-oss cannot disable thinking; it takes a level instead.
THINK_BY_MODEL: Dict[str, object] = {
    "gpt-oss:20b": "low",
    "qwen3.6:35b": False,
    "qwen3.6:27b": False,
}


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class PreflightResult:
    ok: bool = False
    checks: List[Check] = field(default_factory=list)
    server_version: Optional[str] = None
    resolved: Dict[str, str] = field(default_factory=dict)

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append(Check(name, ok, detail))

    def failures(self) -> List[Check]:
        return [c for c in self.checks if not c.ok]


class PreflightFailed(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Pure predicates
# ---------------------------------------------------------------------------
def is_literal_loopback(url: str) -> bool:
    """True only for a literal loopback IP host. Names are rejected outright -
    'localhost' is DNS-derived and can be repointed, so it does not qualify."""
    parts = urlsplit(url)
    if parts.scheme != "http":
        return False
    host = parts.hostname
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_cloud_tag(tag: str) -> bool:
    """Known Ollama cloud tag forms. Defense in depth, not proof of locality."""
    t = tag.strip().lower()
    return t.endswith(":cloud") or t.endswith("-cloud") or "-cloud:" in t


def digest_matches(full_digest: str, approved_digest: str) -> bool:
    """Only a complete SHA-256 identity can match; prefixes are rejected."""
    if not _SHA256_RE.fullmatch(full_digest or "") or \
            not _SHA256_RE.fullmatch(approved_digest or ""):
        return False
    return hmac.compare_digest(full_digest.lower(), approved_digest.lower())


def check_models(tags_payload: dict, wanted: List[str]) -> List[Check]:
    """Validate requested tags against an /api/tags payload. Pure."""
    installed = {m.get("name", ""): m.get("digest", "")
                 for m in tags_payload.get("models", [])}
    out: List[Check] = []
    for tag in wanted:
        if is_cloud_tag(tag):
            out.append(Check(f"tag:{tag}", False, "rejected cloud tag form"))
            continue
        if tag not in installed:
            out.append(Check(f"tag:{tag}", False, "not installed locally"))
            continue
        approved = APPROVED_DIGESTS.get(tag)
        if approved is None:
            out.append(Check(f"tag:{tag}", False, "not an approved candidate"))
            continue
        got = installed[tag]
        if not digest_matches(got, approved):
            out.append(Check(f"tag:{tag}", False,
                             f"digest {got[:16]}... != approved {approved[:16]}..."))
            continue
        out.append(Check(f"tag:{tag}", True, f"digest {got[:16]} matches"))
    return out


def think_value(tag: str):
    """Per-model thinking setting (erratum E1). KeyError for unknown tags is
    intentional: an unapproved model must never reach a request builder."""
    return THINK_BY_MODEL[tag]


# ---------------------------------------------------------------------------
# Live sequencing
# ---------------------------------------------------------------------------
def run_preflight(endpoint: str, models: List[str], *,
                  session=None, timeout: float = 10.0,
                  charge: Optional[Callable[[str], None]] = None) -> PreflightResult:
    """Read-only. Performs GET /api/version and GET /api/tags, nothing else.

    Callers must have already checked --confirm-live; this function does not
    gate itself, so that the gate lives in exactly one place (runner).
    """
    import requests  # local import: keeps package import side-effect free

    res = PreflightResult()

    res.add("endpoint_literal_loopback", is_literal_loopback(endpoint),
            f"{endpoint} (names and non-loopback addresses are rejected)")

    res.add("sandbox_bwrap_present", shutil.which("bwrap") is not None,
            "bubblewrap required; reduced-isolation mode is not offered")

    if res.failures():
        res.ok = False
        return res

    s = session or requests.Session()
    s.trust_env = False          # ignore ambient proxy environment
    s.max_redirects = 0
    proxies = {"http": None, "https": None}

    try:
        if charge:
            charge("preflight_version")
        r = s.get(f"{endpoint}/api/version", timeout=timeout,
                  allow_redirects=False, proxies=proxies)
        r.raise_for_status()
        res.server_version = r.json().get("version")
        res.add("server_reachable", True, f"ollama {res.server_version}")
    except Exception as exc:                      # noqa: BLE001 - reported, not raised
        res.add("server_reachable", False, f"{type(exc).__name__}")
        res.ok = False
        return res

    try:
        if charge:
            charge("preflight_tags")
        r = s.get(f"{endpoint}/api/tags", timeout=timeout,
                  allow_redirects=False, proxies=proxies)
        r.raise_for_status()
        payload = r.json()
    except Exception as exc:                      # noqa: BLE001
        res.add("tags_readable", False, f"{type(exc).__name__}")
        res.ok = False
        return res

    res.add("tags_readable", True, f"{len(payload.get('models', []))} installed")
    res.add("redirects_disabled", s.max_redirects == 0, "allow_redirects=False")
    res.add("proxies_ignored", s.trust_env is False, "trust_env=False")

    installed = {m.get("name", ""): m.get("digest", "")
                 for m in payload.get("models", [])}
    for chk in check_models(payload, models):
        res.checks.append(chk)
        if chk.ok:
            tag = chk.name.split(":", 1)[1]
            res.resolved[tag] = installed[tag]

    res.ok = not res.failures()
    return res


LOCAL_ONLY_STATEMENT = (
    "Analyst connects only to a literal-loopback Ollama endpoint, disables "
    "redirects, ignores ambient proxies, rejects known cloud tag forms (:cloud "
    "and -cloud), and runs only a locally installed model whose tag and digest "
    "match the approved benchmark. Server-level egress control is an operator "
    "prerequisite Analyst cannot prove."
)
