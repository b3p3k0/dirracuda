# Lessons Learned (User Manual)

1. Keep manual rendering logic testable with pure helper functions to avoid
   brittle GUI-only tests.
2. For singleton auxiliary windows, always normalize owner to app toplevel,
   otherwise duplicate dialogs appear from nested parents.
3. Use a single keyboard quick reference source (`docs/KBD_QUICKREF.md`) to
   avoid docs drift.
4. Graceful missing-file handling is mandatory for docs-backed UI surfaces.
