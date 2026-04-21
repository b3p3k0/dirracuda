# Mini Dash v2 Mockups and Flows

Date: 2026-04-21

## Compact Dashboard
```text
+--------------------------------------------------------------------------------+
| Dirracuda      ><(((°>                                           [☀/🌙 Toggle] |
|                                                                                |
| [▶ Start Scan]        [📋 Servers]         [🗄 DB Tools]                      |
| [⚗ Experimental]      [⚙ Config]           [❔ About]                         |
|                                                                                |
| ClamAV:  ...                                                                   |
| tmpfs:   ...                                                                   |
| Status:  ...                                                                   |
| Updated: ...                                               [Running Tasks (0)] |
|---------------------------------------------------------------[External Status]|
+--------------------------------------------------------------------------------+
```

## Running Tasks
```text
+--------------------------------------------------------------------------------+
| Running Tasks                                                             [X] |
|-------------------------------------------------------------------------------|
| Type      Name                     State       Progress         Started       |
| Scan      SMB Scan (US)            Running     42%             14:22:01      |
| Probe     Post-scan Probe Batch    Running     12/80 targets   14:24:17      |
| Extract   Post-scan Extract Batch  Queued      waiting         14:24:19      |
|-------------------------------------------------------------------------------|
| Double-click row to reopen its monitor dialog                                |
+--------------------------------------------------------------------------------+
```

## Shutdown Flow
```text
WM_CLOSE
  -> active scan OR queued scan OR running task?
      -> no: normal close
      -> yes: confirm stop-and-exit
           -> cancel: keep app open
           -> yes:
                request cancel (scan + queue + tasks)
                wait briefly
                retry once with force terminate
                close app
```

