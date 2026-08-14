"""Pure overlapping text chunking with absolute source offsets."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from .models import Chunk


def chunk_text(
    text: str, *, chunk_chars: int, overlap_chars: int
) -> list[Chunk]:
    """Split text into bounded windows with a fixed overlap."""
    _validate_window(chunk_chars, overlap_chars)
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not text:
        return [Chunk(0, 0, 0, "")]

    chunks: list[Chunk] = []
    stride = chunk_chars - overlap_chars
    start = 0
    while start < len(text):
        end = min(start + chunk_chars, len(text))
        chunks.append(Chunk(len(chunks), start, end, text[start:end]))
        if end == len(text):
            break
        start += stride
    return chunks


def iter_chunks(
    text: str, *, chunk_chars: int, overlap_chars: int
) -> Iterator[Chunk]:
    yield from chunk_text(
        text, chunk_chars=chunk_chars, overlap_chars=overlap_chars
    )


def locate(chunks: Sequence[Chunk], absolute_offset: int) -> int:
    """Return the first chunk containing an absolute character offset."""
    if not isinstance(absolute_offset, int):
        raise TypeError("absolute_offset must be an integer")
    for item in chunks:
        if item.start <= absolute_offset < item.end:
            return item.index
    return -1


def spans_boundary(
    start: int,
    end: int,
    *,
    chunk_chars: int,
    overlap_chars: int,
) -> bool:
    """Return whether no single window can contain ``[start, end)``."""
    _validate_window(chunk_chars, overlap_chars)
    if not isinstance(start, int) or not isinstance(end, int):
        raise TypeError("span offsets must be integers")
    if start < 0 or end < start:
        raise ValueError("span must satisfy 0 <= start <= end")
    extent = max(end, chunk_chars)
    windows = chunk_text(
        " " * extent, chunk_chars=chunk_chars, overlap_chars=overlap_chars
    )
    return not any(item.start <= start and end <= item.end for item in windows)


def _validate_window(chunk_chars: int, overlap_chars: int) -> None:
    if not isinstance(chunk_chars, int) or not isinstance(overlap_chars, int):
        raise TypeError("chunk and overlap sizes must be integers")
    if chunk_chars <= 0:
        raise ValueError("chunk_chars must be positive")
    if overlap_chars < 0:
        raise ValueError("overlap_chars must not be negative")
    if overlap_chars >= chunk_chars:
        raise ValueError("overlap_chars must be smaller than chunk_chars")
