# ClamAV Integration ASCII Sketches

Date: 2026-03-27

## 1) Phase 1 Flow (Bulk Extract Only)

```text
[Bulk Extract Entry Point]
   |  (dashboard post-scan bulk OR server-list batch extract)
   v
[extract_runner.run_extract]
   |
   +--> download file -> quarantine/<host>/<date>/<share>/...
   |
   +--> if clamav.enabled == false ----------------------------+
   |                                                           |
   |                                                           v
   |                                                    [existing summary]
   |
   +--> if clamav.enabled == true
           |
           v
      [scan file]
         | clean ---------> move -> extracted/<host>/<date>/<share>/...
         | infected ------> move -> quarantine/known_bad/<host>/<date>/<share>/...
         | error ---------> keep -> original quarantine path

After all files:
   v
[compose extract summary + clamav summary]
   |
   +--> if session-muted: no popup
   +--> else: show ClamAV Results dialog
```

## 2) Backend Selection (auto)

```text
auto mode
   |
   +--> clamdscan available? ---- yes --> use clamdscan
   |                                |
   |                                +--> exit 0 clean
   |                                +--> exit 1 infected
   |                                +--> exit 2 error
   |
   +--> no --> clamscan available? - yes --> use clamscan
                                    |
                                    +--> exit 0 clean
                                    +--> exit 1 infected
                                    +--> exit 2 error

if none available:
   -> scanner_error("no supported scanner found")
   -> fail_open => keep files in quarantine, continue extract
```

## 3) Directory Layout

```text
~/.dirracuda/
  quarantine/
    203.0.113.10/
      20260327/
        public/
          photo.jpg               (scan error -> remains)

    known_bad/
      203.0.113.10/
        20260327/
          public/
            invoice.docx          (infected -> moved here)

  extracted/
    203.0.113.10/
      20260327/
        public/
          readme.txt              (clean -> moved here)
          report.pdf              (clean -> moved here)
```

## 4) App Config Dialog (Expanded Controls)

```text
+--------------------------------------------------------------------------------+
| Dirracuda - Application Configuration                                          |
+--------------------------------------------------------------------------------+
| Core Paths                                                                     |
|  ...                                                                           |
|                                                                                |
| Runtime Settings                                                               |
|  Shodan API Key:             [*********************]                           |
|  Quarantine Directory:       [~/.dirracuda/quarantine ] [Browse]              |
|  Pry Wordlist Path:          [/path/wordlist.txt      ] [Browse]              |
|                                                                                |
| ClamAV Integration                                                          |
|  [x] Enable ClamAV scan downloaded files                                 |
|  Scanner backend:            [Auto v]                                          |
|  Scanner timeout (seconds):  [60      ]                                        |
|  Clean destination:          [~/.dirracuda/extracted] [Browse]                |
|  Known-bad subfolder:        [known_bad]                                       |
|  [x] Show ClamAV result dialogs                                                |
+--------------------------------------------------------------------------------+
|                                                        [Cancel]  [Save]        |
+--------------------------------------------------------------------------------+
```

## 5) ClamAV Results Dialog (Session Mute)

```text
+--------------------------------------------------------------------------------+
| ClamAV Scan Summary                                                            |
+--------------------------------------------------------------------------------+
| Operation: Post-scan Bulk Extract (12 hosts)                                   |
| Backend: clamscan                                                              |
|                                                                                |
| Files scanned: 120   Clean: 114   Promoted: 114   Infected: 4   Errors: 2     |
|                                                                                |
| Infected / Error Details                                                       |
| +----------------------+-----------+---------------------------+---------------+
| | Host                 | Verdict   | Signature / Error         | Destination   |
| +----------------------+-----------+---------------------------+---------------+
| | 203.0.113.10/public  | infected  | Eicar-Test-Signature      | known_bad     |
| | 203.0.113.22/docs    | error     | scanner timeout           | quarantine    |
| +----------------------+-----------+---------------------------+---------------+
|                                                                                |
| [ ] Mute ClamAV result dialogs until app restart                               |
|                                                                                |
|                                                [Export CSV]  [Close]           |
+--------------------------------------------------------------------------------+
```

## 6) Long-Term Hook (Future Browser Downloads)

```text
today (phase 1):
  bulk extract -> QuarantinePostProcessor -> scanner + placement

future (phase 2+):
  browser download -> QuarantinePostProcessor -> scanner + placement

same contract, new caller
```

