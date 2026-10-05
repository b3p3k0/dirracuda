# Concurrent Process Management — Lessons Learned

Owner: RA. Written 2026-10-05 at closeout of CPM-B1, B2, A, C.

1. **Check which file you measured.** The AA findings read journal mode from
   `~/.dirracuda/dirracuda.db`, a 0-byte legacy stub. The runtime DB is
   `~/.dirracuda/data/dirracuda.db` and was already WAL. Resolve the path with
   `resolve_runtime_main_db_path()` before reading live state.
2. **Per-connection pragmas are not file state.** `busy_timeout` and
   `synchronous` reset on every connect. Only `journal_mode=WAL` persists.
3. **Trace a delay to its sleep before promising a speedup.** The pacing keys
   did not touch the GUI probe path at all. The Dashboard win came from the
   lock removal and the worker count.
4. **A grep table in a plan is a floor, not a ceiling.** The DA's re-grep found
   7 more primary-DB openers than the plan listed. Keep the "re-run the grep and
   reconcile" step in every cutover card.
5. **Persistent connections change failure semantics.** Per-call connections
   discarded uncommitted work on close. Thread-local connections must roll back
   on the outermost exit, or one forgotten commit blocks the CLI writer.
6. **A `@contextmanager` must yield exactly once.** The old locked-retry branch
   yielded twice and could only ever raise `RuntimeError`.
7. **Do not let a validation probe mutate its target.** `analyze_database_schema`
   can inspect a non-active file; it must stay read-only.
8. **DA sandbox results need a real-venv re-run.** Codex reported 90 shared
   failures every card; all were sandbox limits (sockets, Analyst trust check,
   Tk). The real venv was green each time.
9. **Historical records stay historical.** The DA rewrote FINDINGS_AA.md to
   match new code. Corrections go in an erratum or the plan, not over the
   original assessment.
