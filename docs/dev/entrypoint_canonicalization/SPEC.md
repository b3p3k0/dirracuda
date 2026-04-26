# SPEC - Entrypoint Canonicalization Closure

## Goal
Prevent future implementation drift between runtime entrypoints by making `./dirracuda` the only supported GUI launch path.

## Product Contract
1. `./dirracuda` is the canonical and supported GUI runtime entrypoint.
2. `python gui/main.py` must:
   - print deprecation guidance to stderr
   - exit non-zero (`2`)
   - never launch GUI runtime
3. `from gui.main import SMBSeekGUI` remains import-compatible via alias to canonical class.

## Engineering Contract
1. Runtime implementation changes must target the canonical `dirracuda` path.
2. `gui/main.py` is a compatibility shim, not a second runtime implementation.
3. Tests are canonical-first:
   - close behavior, startup unification, and tmpfs warning flows validate `dirracuda`.
   - one dedicated shim smoke test validates `gui/main.py` runtime rejection behavior.

## Documentation Contract
1. `docs/TECHNICAL_REFERENCE.md` must describe `gui/main.py` as compatibility-only.
2. `CLAUDE.md` must include a clear entrypoint guardrail.
