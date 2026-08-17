# Risk Register - Keyboard Control Enhancement

Date: 2026-06-30

| ID | Risk | Likelihood | Impact | Mitigation |
|---|---|---:|---:|---|
| KCE-1 | Stale docs send DA to the wrong dashboard shortcut mapping. | High | Medium | C0 reconciles source-of-truth docs before behavior work. |
| KCE-2 | `BackSpace` parent navigation hijacks editable fields in browser windows. | Medium | High | C1 requires reproduction and focus-aware tests before fix. |
| KCE-3 | Duplicate bindings cause double invocation. | Medium | Medium | Use shared helpers; tests assert callback counts. |
| KCE-4 | A shortcut bypasses disabled/busy state. | Low | High | Shortcuts call existing handlers, not direct state mutations. |
| KCE-5 | App-global `bind_all` leaks beyond intended shortcuts. | Medium | Medium | C3 audits `bind_all`; only `Q/H/T` allowed. |
| KCE-6 | `dirracuda` grows past the line-count gate. | Medium | High | Avoid touching it; require no-growth or extraction plan. |
| KCE-7 | Manual-only keyboard behavior drifts without test coverage. | Medium | Medium | Add helper tests for every new shortcut policy. |
| KCE-8 | Docs get updated without verifying runtime. | Medium | Medium | Every card starts from code/test confirmation and ends with README review. |
