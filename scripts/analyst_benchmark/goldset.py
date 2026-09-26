"""
Gold-set loader and integrity check.

DISPOSITION: retained diagnostic; the fixture corpus itself becomes the C1+ test
corpus.

Reads shared/tests/fixtures/analyst_gold/manifest.json, verifies every listed
document still hashes to its recorded sha256, and exposes the balanced Stage B
screening subset.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLD_ROOT = REPO_ROOT / "shared" / "tests" / "fixtures" / "analyst_gold"
MANIFEST = GOLD_ROOT / "manifest.json"


@dataclass
class GoldDoc:
    doc_id: str
    path: Path
    stratum: str
    source_format: str
    sha256: str
    size: int
    categories_present: Set[str]
    expected_identifiers: List[str]
    clean_twin_id: Optional[str]
    adversarial_class: Optional[str]
    context_rule_exception: bool

    def text(self) -> str:
        return self.path.read_text(encoding="utf-8")


class GoldSetError(RuntimeError):
    pass


@dataclass
class GoldSet:
    version: str
    chunk_chars_reference: int
    docs: Dict[str, GoldDoc]
    screening_subset: List[str]
    provenance: Dict[str, str]

    def subset(self) -> List[GoldDoc]:
        return [self.docs[d] for d in self.screening_subset]

    def by_stratum(self, stratum: str) -> List[GoldDoc]:
        return [d for d in self.docs.values() if d.stratum == stratum]

    def twin_of(self, doc_id: str) -> Optional[GoldDoc]:
        tid = self.docs[doc_id].clean_twin_id
        return self.docs.get(tid) if tid else None


def load(verify: bool = True) -> GoldSet:
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    docs: Dict[str, GoldDoc] = {}
    for row in raw["documents"]:
        p = GOLD_ROOT / row["path"]
        doc = GoldDoc(
            doc_id=row["doc_id"], path=p, stratum=row["stratum"],
            source_format=row["source_format"], sha256=row["sha256"],
            size=row["size"],
            categories_present=set(row["categories_present"]),
            expected_identifiers=list(row["expected_identifiers"]),
            clean_twin_id=row.get("clean_twin_id"),
            adversarial_class=row.get("adversarial_class"),
            context_rule_exception=bool(row.get("context_rule_exception")),
        )
        if verify:
            if not p.exists():
                raise GoldSetError(f"missing fixture {row['path']}")
            got = hashlib.sha256(p.read_bytes()).hexdigest()
            if got != doc.sha256:
                raise GoldSetError(
                    f"{row['doc_id']} hash mismatch: manifest {doc.sha256[:16]}, "
                    f"file {got[:16]}. Regenerate or restore the fixture.")
        docs[doc.doc_id] = doc

    if len(docs) != raw["document_count"]:
        raise GoldSetError(
            f"manifest declares {raw['document_count']} documents, loaded {len(docs)}")

    missing = [d for d in raw["screening_subset"] if d not in docs]
    if missing:
        raise GoldSetError(f"screening subset references unknown docs: {missing}")

    return GoldSet(
        version=raw["gold_set_version"],
        chunk_chars_reference=raw["chunk_chars_reference"],
        docs=docs,
        screening_subset=list(raw["screening_subset"]),
        provenance=dict(raw["identifier_provenance"]),
    )
