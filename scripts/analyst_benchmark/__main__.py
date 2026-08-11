"""Entry point for frozen C0B-1 and namespaced public benchmark commands."""
from __future__ import annotations

import sys
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["c0b2"]:
        from .c0b2_cli import main as c0b2_main
        return c0b2_main(args[1:])
    if args[:1] == ["c0b3"]:
        from .c0b3_cli import main as c0b3_main
        return c0b3_main(args[1:])
    if args[:1] == ["c0b4"]:
        from .c0b4_cli import main as c0b4_main
        return c0b4_main(args[1:])

    # Preserve the C0B-1 parser and dispatch byte-for-byte by delegating every
    # non-namespaced invocation with its original argument sequence.
    from .runner import main as c0b1_main
    return c0b1_main(args)

if __name__ == "__main__":
    sys.exit(main())
