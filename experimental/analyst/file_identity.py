"""Lossless SQLite encoding for unsigned filesystem identity fields."""

from __future__ import annotations

from typing import Final


SQLITE_INTEGER_MAX: Final = (1 << 63) - 1
UINT64_MAX: Final = (1 << 64) - 1


class FileIdentityError(ValueError):
    """A persisted filesystem identity is outside the frozen uint64 contract."""


def split_unsigned_u64(
    value: int, *, require_positive: bool = False,
) -> tuple[int, int]:
    """Split one uint64 into a SQLite-safe low 63 bits and high-bit flag."""
    minimum = 1 if require_positive else 0
    if type(value) is not int or not minimum <= value <= UINT64_MAX:
        raise FileIdentityError("filesystem identity is outside the uint64 contract")
    return value & SQLITE_INTEGER_MAX, value >> 63


def join_unsigned_u64(
    low_bits: int, high_bit: int, *, require_positive: bool = False,
) -> int:
    """Reconstruct one exact uint64 from its SQLite-safe representation."""
    if (
        type(low_bits) is not int
        or not 0 <= low_bits <= SQLITE_INTEGER_MAX
        or type(high_bit) is not int
        or high_bit not in (0, 1)
    ):
        raise FileIdentityError("persisted filesystem identity is invalid")
    value = low_bits | (high_bit << 63)
    if require_positive and value == 0:
        raise FileIdentityError("persisted filesystem identity is invalid")
    return value


__all__ = [
    "FileIdentityError",
    "SQLITE_INTEGER_MAX",
    "UINT64_MAX",
    "join_unsigned_u64",
    "split_unsigned_u64",
]
