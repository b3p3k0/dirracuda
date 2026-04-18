# Reddit OD Module: V3 ASCII Sketches + Flowcharts

Date: 2026-04-17

## 1) Reddit Grab Dialog - Before / After

### Before

```text
+----------------------------- Reddit Grab ----------------------------------+
| Sort: [new|top]                                                          |
| Max posts: [50]                                                          |
| [x] Parse body                                                           |
| [ ] Include NSFW                                                         |
| [ ] Replace cache                                                        |
|                                                       [Cancel] [Run Grab] |
+----------------------------------------------------------------------------+
```

### After (V3 target)

```text
+----------------------------- Reddit Grab ----------------------------------+
| Mode: [Feed] [Search] [User]                                              |
| Subreddit: opendirectories (fixed)                                        |
| Sort: [new|top]   Top Window: [hour|day|week|month|year|all]             |
|                                                                            |
| Search Query: [______________________________]   (Search mode only)       |
| Username:     [______________________________]   (User mode only)         |
|                                                                            |
| [x] Parse body   [ ] Include NSFW   [ ] Replace cache   Max posts: [50]   |
|                                                                            |
|                                                       [Cancel] [Run Grab] |
+----------------------------------------------------------------------------+
```

## 2) Top Window Request Flow

```text
Dashboard
  -> Experimental
    -> Reddit tab
      -> Open Reddit Grab
        -> Mode=Feed, Sort=top, Top Window=<t>
          -> client builds /r/opendirectories/top.json?t=<t>
            -> fetch pages (<= max_pages)
              -> parse targets
                -> dedupe write to sidecar
                  -> save ingest state key top:<t>
                    -> return IngestResult to UI
```

## 3) Search Mode Flow (Subreddit-Scoped)

```text
Dashboard
  -> Experimental
    -> Reddit tab
      -> Open Reddit Grab
        -> Mode=Search, Query=<q>, Sort=<new|top>
          -> validate non-empty query
            -> client builds
               /r/opendirectories/search.json?q=<q>&restrict_sr=1&sort=<...>[&t=<...>]
              -> fetch pages (<= max_pages)
                -> parse targets
                  -> dedupe write to sidecar
                    -> save scrape state metadata
                      -> return IngestResult to UI
```

## 4) User Submitted Mode Flow

```text
Dashboard
  -> Experimental
    -> Reddit tab
      -> Open Reddit Grab
        -> Mode=User, Username=<name>, Sort=<new|top>
          -> validate username format
            -> client builds /user/<name>/submitted.json?sort=<...>[&t=<...>]
              -> fetch pages (<= max_pages)
                -> parse targets
                  -> dedupe write to sidecar
                    -> save scrape state metadata
                      -> return IngestResult to UI
```

## 5) Ingest State Compatibility Flow

```text
Feed top + window=week
  -> read state key top:week
    -> if missing, fallback read legacy key top
      -> run dedupe ingestion
        -> write key top:week

Feed top + window!=week
  -> read/write key top:<window> only
```

## 6) Feature-by-Feature Delivery Map

```text
Card V3-1: Top window only
  -> no search/user UI yet

Card V3-2: Search mode
  -> query field + endpoint + tests

Card V3-3: User submitted mode
  -> username field + endpoint + tests

Card V3-4: persistence + regressions + closeout
```
