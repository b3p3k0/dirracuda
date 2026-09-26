"""
Analyst benchmark instrument (C0B).

Import-safe by contract: importing this package performs no filesystem access,
no network access, no settings reads, and no optional-dependency imports.
Every side effect lives behind an explicit confirmation flag in `runner`.

Module dispositions are recorded in
docs/dev/ollama_integration/BENCHMARK.md — each module is either ported into
production by a named card, retained as a maintained diagnostic, or deleted by
a named card. Nothing here is production runtime.
"""
