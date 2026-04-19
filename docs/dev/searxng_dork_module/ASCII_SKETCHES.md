# SearXNG Dork Module - ASCII Sketches

Date: 2026-04-18

## 1) Experimental Tabs - Before / After

### Before

```text
+--------------------------- Experimental Features ---------------------------+
| Tabs: [Reddit] [placeholder]                                              |
+---------------------------------------------------------------------------+
```

### After

```text
+--------------------------- Experimental Features ---------------------------+
| Tabs: [Reddit] [SearXNG Dorking]                                           |
+---------------------------------------------------------------------------+
```

## 2) SearXNG Dorking Tab (v1)

```text
+--------------------------- Experimental Features ---------------------------+
| Tabs: [Reddit] [SearXNG Dorking]                                           |
|---------------------------------------------------------------------------|
| SearXNG Dorking                                                            |
|                                                                           |
| Instance URL: [ http://192.168.1.20:8090                    ] [Test]     |
| Instance status: PASS (config ok, json search ok)                        |
|                                                                           |
| Query: [ site:* intitle:"index of /"                         ]            |
| Max Results: [ 100 ]     Verify with HTTP probe path: [x]                |
|                                                                           |
| [Run Dork Search] [Stop] [Open Dork Results DB]                          |
|---------------------------------------------------------------------------|
| Last run: fetched=11 deduped=10 verified=10                              |
| verdicts: OPEN_INDEX=4  MAYBE=1  NOISE=3  ERROR=2                        |
+---------------------------------------------------------------------------+
```

## 3) Instance Test Error UX (format policy)

```text
Instance status: FAIL - instance_format_forbidden
Detail: /search?format=json returned 403
Hint: enable search.formats to include json in settings.yml
```

## 4) Results Browser Window

```text
+--------------------------- SearXNG Dork Results ----------------------------+
| Filter: [OPEN_INDEX v]  Search text: [__________]                        |
|---------------------------------------------------------------------------|
| Verdict     | URL                            | Source | Reason            |
| OPEN_INDEX  | http://222.143.34.1/           | google | index_title+links |
| NOISE       | https://reddit.com/...         | google | not_index_page    |
| ERROR       | https://target/...             | bing   | verify_timeout    |
|---------------------------------------------------------------------------|
| [Open URL] [Copy URL] [Promote/Add Record] [Close]                       |
+---------------------------------------------------------------------------+
```

## 5) Runtime Flow

```text
SearXNG Dorking tab
  -> Test Instance
     -> GET /config
     -> GET /search?q=hello&format=json
     -> pass/fail + reason

  -> Run Dork Search
     -> GET /search?q=<dork>&format=json
     -> normalize + dedupe URLs
     -> verify/classify each URL via existing HTTP path
     -> persist run/results to ~/.dirracuda/se_dork.db
     -> show summary + open results browser
```

## 6) Verification Subflow (Reuse Existing Logic)

```text
candidate URL
  -> parse scheme/host/port/path
  -> try_http_request(...)
  -> validate_index_page(body, status)
  -> verdict mapping:
       true  -> OPEN_INDEX
       false -> NOISE or MAYBE
       error -> ERROR
```

