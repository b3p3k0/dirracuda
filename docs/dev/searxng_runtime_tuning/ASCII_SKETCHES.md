# C11 ASCII Sketches

## Start Scan - SearXNG

```text
[x] SearXNG  Instance [http://halcyon:8090                 ]
              Query [intitle:"index of /"                  ]
            Results [1000]

            Request timeout   5s  [----o-------------]  60s   15s
            Short retry       5s  [--------o---------]  60s   30s
            Long retry       60s  [------o-----------] 300s  180s

            Long retry is skipped after 5 productive pages or 50 unique URLs.
            Retry-After takes precedence. Soft pacing remains automatic.
```

The rightmost value updates while dragging. The scale and value label become
disabled when SearXNG is unchecked.

## Runtime Decision Flow

```text
SearXNG response
      |
      +-- results present ---------------------------+
      |                                              |
      |   persist -> classify -> retain -> probe     |
      |                    |                         |
      |                    +-- productive page ------+--> update maturity
      |                                              |
      |   clean: normal pacing                       |
      |   warnings: fixed 10/20/30 soft pacing       |
      +----------------------------------------------+
      |
      +-- empty + clean -------------------------------> finish
      |
      +-- empty + throttle / HTTP 429
               |
               +-- mature? yes -> short retry -> finish partial if unresolved
               |
               +-- mature? no  -> short retry -> long retry -> finish/error
```

## Cancellation Flow

```text
Running Tasks / queue cancel
             |
             v
       set cancel event
             |
   +---------+----------+------------------+
   |                    |                  |
 pacing/cooldown   active request    page processing
 returns now       waits for timeout checks safe boundary
   |                    |                  |
   +--------------------+------------------+
                        |
             preserve committed results
                        |
             mark run CANCELLED
                        |
       existing one-time primary-table sync
                        |
      yellow completion line; no error popup
```

## Live Output

```text
BLUE    [status 12:00:00] Provider queue starting: SearXNG
BLUE    [status 12:00:00] Reachability: checking http://halcyon:8090...
GREEN   [status 12:00:00] Instance reachable
BLUE    [status 12:00:00] Querying SearXNG page 1...
WHITE   [status 12:00:01] Page 1: received 25 results, 25 new.
WHITE   [status 12:00:01] Page 1: stored 25 rows.
GREEN   [status 12:00:06] Page 1 complete: 12 open indexes retained.
YELLOW  [status 12:00:06] Upstream engine warnings; continuing with soft backoff.
RED     [status 12:00:20] SearXNG processing failed: database write failed.
```

