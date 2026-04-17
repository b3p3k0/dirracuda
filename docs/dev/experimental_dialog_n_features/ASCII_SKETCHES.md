# ASCII Sketches + Flowcharts

Date: 2026-04-17

## 1) Dashboard Header - Before / After

### Before

```text
+--------------------------------------------------------------------------------+
| Dirracuda      ><(((o>                                                        |
|                                                                                |
| [Start Scan] [Servers] [DB Tools] [Config] [About]                  [Theme]   |
+--------------------------------------------------------------------------------+
```

### After

```text
+--------------------------------------------------------------------------------+
| Dirracuda      ><(((o>                                                        |
|                                                                                |
| [Start Scan] [Servers] [DB Tools] [Experimental] [Config] [About]   [Theme]   |
+--------------------------------------------------------------------------------+
```

## 2) Start Scan Dialog - Before / After

### Before

```text
+-------------------------------- Start Scan ------------------------------------+
| ... scan parameters ...                                                        |
|                                                                                |
| [Reddit Grab (EXP)]                                            [Cancel][Start] |
+--------------------------------------------------------------------------------+
```

### After

```text
+-------------------------------- Start Scan ------------------------------------+
| ... scan parameters ...                                                        |
|                                                                                |
|                                                          [Cancel][Start]        |
+--------------------------------------------------------------------------------+
```

## 3) Server List Header - Before / After

### Before

```text
+------------------------------ Server List Browser ------------------------------+
| Server List                                           [Reddit Post DB (EXP)]   |
| ... filters/table ...                                                         |
+--------------------------------------------------------------------------------+
```

### After

```text
+------------------------------ Server List Browser ------------------------------+
| Server List                                                                    |
| ... filters/table ...                                                         |
+--------------------------------------------------------------------------------+
```

## 4) Experimental Dialog Sketch

```text
+--------------------------- Experimental Features -------------------------------+
| Tabs: [Reddit] [placeholder]                                                  |
|--------------------------------------------------------------------------------|
| [!] Experimental notice: features here are in active development and may be   |
|     flaky. [ ] Don't show this notice again                                   |
|--------------------------------------------------------------------------------|
| Reddit tab                                                                      |
|                                                                                |
|  Reddit ingestion + review tools                                                |
|                                                                                |
|  [Open Reddit Grab]   [Open Reddit Post DB]                                    |
|                                                                                |
|  Notes: Uses sidecar DB ~/.dirracuda/reddit_od.db                              |
|--------------------------------------------------------------------------------|
| placeholder tab                                                                |
|                                                                                |
|  Coming soon                                                                    |
|  This tab is a scaffold for future experimental modules.                       |
|                                                                                |
|                                                       [Close]                   |
+--------------------------------------------------------------------------------+
```

## 5) Workflow Flowchart - Reddit Grab

```text
Dashboard
  -> Experimental button
    -> Experimental Dialog (Reddit tab)
      -> Open Reddit Grab
        -> Existing Reddit Grab dialog
          -> Existing run_ingest worker path
            -> Completion popup + status log
```

## 6) Workflow Flowchart - Reddit Post DB

```text
Dashboard
  -> Experimental button
    -> Experimental Dialog (Reddit tab)
      -> Open Reddit Post DB
        -> Acquire/reuse Server List window context (for add-record callback)
          -> Open Reddit Browser window
            -> Review rows / open targets / optional Add to dirracuda DB
```

## 7) Future Feature Lifecycle (Tab-per-feature)

```text
Add feature module
  -> Register feature in registry.py
    -> Tab appears automatically
      -> Remove feature from registry
        -> Tab disappears without dialog surgery
```
