# Web UI Lessons Learned

Seeded before implementation. Append after every major card.

## Carry Forward

1. Inspect the actual UI before sketching changes. The Experimental dialog is a
   `ttk.Notebook`; do not replace it with a left-nav pattern.
2. Put the `Web UI` tab between `Reddit` and `Dorkbook`, not at the end.
3. Keep the desktop tab small. Put operational controls in a launched control
   dialog.
4. Use the existing CLI subprocess boundary for v1 scans. Direct workflow calls
   are tempting, but they add cancellation and behavior-drift risk.
5. Remote support is a v1 goal, but remote exposure is not a default.
6. Auth/session/token work is security-sensitive. Keep it boring, testable, and
   easy to review.
7. Do not add bearer API tokens until browser sessions are stable.
8. Guard every DB read against real runtime schema state.
9. Validate coercion explicitly. Weird strings should fail, not become dangerous
   defaults.
10. Use ASCII sketches for every UI surface before implementation.
11. Service control must survive desktop app restarts. Use health checks plus
    pidfile/systemd state, not only in-memory process handles.
12. Share/directory summaries are v1 web UI scope. The file explorer and target
    downloads are not.
13. Mobile is v1 scope. A desktop-only web UI misses how operators actually use
    browser dashboards.
14. Web dependencies belong in `webui/requirements-web.txt` unless HI explicitly folds
    them into the main runtime.
15. Web scan launch should keep using strict request validation plus argv-list
    subprocess calls with explicit `shell=False`; never let browser input become
    shell syntax or loosely coerced scan options.
