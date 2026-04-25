# Keymaster v1 Flow Charts

Date: 2026-04-25

## 1) Apply Path (Unified Logic)

```mermaid
flowchart TD
    A[Double-click row] --> D[_apply_selected_key]
    B[Context menu Apply] --> D
    C[Apply button] --> D

    D --> E{Row selected?}
    E -->|No| F[Show warning and stop]
    E -->|Yes| G[Resolve active config path]

    G --> H{Path resolved?}
    H -->|No| I[Show config-context error]
    H -->|Yes| J[Load JSON or initialize empty object]

    J --> K[Set shodan.api_key only]
    K --> L[Write config atomically-ish via single JSON write]
    L --> M{Write success?}
    M -->|No| N[Show error and stop]
    M -->|Yes| O[Update last_used_at in keymaster DB]
    O --> P[Refresh row state and show success status]
```

Post-condition:

1. The newly applied key is used by future scans.
2. In-flight scans continue with the key value captured when their scan start was confirmed.

## 2) Window Launch Path

```mermaid
flowchart TD
    A[Dashboard Experimental button] --> B[Experimental dialog]
    B --> C[Keymaster tab Open Keymaster]
    C --> D[dashboard_experimental.open_keymaster]
    D --> E{Singleton alive?}
    E -->|Yes| F[Focus existing Keymaster window]
    E -->|No| G[Construct KeymasterWindow]
    G --> H[init_db and open sidecar]
    H --> I[Load key rows]
```

## 3) CRUD Persistence Path

```mermaid
flowchart TD
    A[Add/Edit/Delete action] --> B[Open sidecar connection]
    B --> C[Validate schema guard]
    C --> D[Run mutation]
    D --> E{Duplicate or readonly error?}
    E -->|Yes| F[Show targeted error message]
    E -->|No| G[Commit transaction]
    G --> H[Reload table view]
```
