"""
Chunking with defined overlap and normalized offsets.

DISPOSITION: ported to production in C1.

Pure. Offsets are absolute into the source text so a finding can be traced back
to the document regardless of which chunk produced it (CONTRACT.md §7).
"""
from __future__ import annotations

from typing import Iterator, List, NamedTuple


class Chunk(NamedTuple):
    index: int
    start: int          # absolute offset into the source text
    end: int            # exclusive
    text: str

    @property
    def length(self) -> int:
        return self.end - self.start


def chunk(text: str, *, chunk_chars: int, overlap_chars: int) -> List[Chunk]:
    """Split into overlapping windows.

    Guarantees:
      - every character of `text` appears in at least one chunk;
      - consecutive chunks share exactly `overlap_chars` characters (except at
        the tail, where the remainder may be shorter);
      - `start` advances strictly, so this always terminates.
    """
    if chunk_chars <= 0:
        raise ValueError("chunk_chars must be positive")
    if overlap_chars < 0:
        raise ValueError("overlap_chars must not be negative")
    if overlap_chars >= chunk_chars:
        raise ValueError("overlap_chars must be smaller than chunk_chars")

    if not text:
        return [Chunk(0, 0, 0, "")]

    out: List[Chunk] = []
    stride = chunk_chars - overlap_chars
    start = 0
    idx = 0
    while start < len(text):
        end = min(start + chunk_chars, len(text))
        out.append(Chunk(idx, start, end, text[start:end]))
        if end == len(text):
            break
        start += stride
        idx += 1
    return out


def iter_chunks(text: str, *, chunk_chars: int, overlap_chars: int) -> Iterator[Chunk]:
    yield from chunk(text, chunk_chars=chunk_chars, overlap_chars=overlap_chars)


def spans_boundary(start: int, end: int, *, chunk_chars: int,
                   overlap_chars: int) -> bool:
    """True when [start, end) crosses a chunk cut without overlap coverage.

    Used to score boundary misses: an identifier that straddles a cut should
    still be recoverable when the overlap is wide enough to contain it whole.
    """
    for c in chunk(" " * max(end, chunk_chars), chunk_chars=chunk_chars,
                   overlap_chars=overlap_chars):
        if c.start <= start and end <= c.end:
            return False
    return True


def locate(chunks: List[Chunk], absolute_offset: int) -> int:
    """Index of the first chunk containing an absolute offset, or -1."""
    for c in chunks:
        if c.start <= absolute_offset < c.end:
            return c.index
    return -1
