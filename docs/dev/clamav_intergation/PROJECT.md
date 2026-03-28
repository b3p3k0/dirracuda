Here’s a concrete plan for adding optional ClamAV scanning to Dirracuda’s bulk‑extraction flow.  The goal is to scan each downloaded file before it leaves quarantine and handle any detections gracefully without blocking the overall extraction.


### 1. Understand the current flow

* **Quarantine creation** – `shared/quarantine.py` sanitizes a server label and creates a directory in `.smbseek/quarantine` for each host.  There is no antivirus scanning here.
* **HTTP download thread** – `_download_thread_fn` in `gui/components/http_browser_window.py` writes each downloaded file to the quarantine directory and logs the event, then returns success or error messages.  No scanning occurs after downloads.
* **Bulk extraction** – the “Bulk Extract” option in `ScanDialog` toggles whether `extract_runner.run_extract` is invoked.  `extract_runner.run_extract` uses `impacket.smbconnection` to pull files from each accessible share; it writes each file into the quarantine directory and logs an event but does not scan the files.
* **Configuration** – the UI already has options for enabling bulk extraction and skipping extraction when malware indicators are present; similar settings could enable/disable ClamAV scanning.

### 2. Select a ClamAV integration method

There are two widely‑used Python libraries for ClamAV:

1. **`python‑clamd`** – uses the ClamAV `clamd` daemon.  Example usage: create a `ClamdUnixSocket` or `ClamdNetworkSocket` object, call `scan(file_path)` to scan a file or `instream(BytesIO(stream))` for a stream, and get a result such as `('FOUND', 'Eicar‑Test‑Signature')`.
2. **`clamav‑client`** – a newer, portable wrapper that supports both the `clamd` daemon and the `clamscan` command.  It provides a high‑level scanner accessible via `clamav_client.get_scanner()`.

Both libraries require a running ClamAV engine; `python‑clamd` depends on `clamd` listening on a local socket or TCP port (e.g., 3310), while `clamav‑client` can call `clamscan` directly if no daemon is running.  In the TO THE NEW blog example, they show how to install ClamAV, start `clamd`, and then connect via `ClamdNetworkSocket` to scan each file in a directory.  Another example demonstrates scanning a stream using `BytesIO`.

### 3. Decide how to run ClamAV

* **Daemon vs. stand‑alone** – Running `clamd` as a daemon provides faster scans and avoids repeated initialization; this is likely the best choice for bulk extraction.  For systems where installing a daemon is not feasible, `clamav‑client` can call `clamscan` directly, though scanning will be slower.
* **Configuration parameters** – Expose in the UI:

  * `clamav_mode`: options such as `disabled`, `clamd_socket`, `clamd_tcp`, or `clamscan`.
  * `clamd_socket_path` or `clamd_host`/`clamd_port` for connecting to the daemon.
  * `clamscan_path` for specifying the `clamscan` binary if using command‑line scanning.
  * `scan_timeout` to avoid hanging on large files.
  * `scan_enabled`: boolean to enable scanning after download.

### 4. Integrate scanning into the extraction pipeline

1. **Hook after download** – In `_download_thread_fn` and `extract_runner.run_extract`, after successfully writing a file to the quarantine directory, invoke the ClamAV scanner.  For example:

   ```python
   scanner = clamav_client.get_scanner(...)  # or clamd.ClamdUnixSocket(...)
   result = scanner.scan_file(file_path)     # returns status and signature
   if result['status'] == 'FOUND':
       # mark as infected, log event, optionally move to infected subdir
   ```

   When scanning streams (e.g., decompressing archives), use the `instream` method with a `BytesIO` object.
2. **Asynchronous scanning** – Because scans may be slow on large files, run them in separate threads or asynchronously with a thread pool.  This prevents the UI from freezing and allows multiple files to be scanned in parallel.
3. **Handle results** – Extend `log_quarantine_event` to record the scan status and signature; consider storing infected files in a separate `infected` subdirectory.  Provide the user with a summary of infected items at the end of extraction (e.g., number of files scanned, number infected, signatures found).
4. **Error handling** – If the scanner cannot connect to `clamd` or returns an error, log the failure and continue extraction without scanning rather than halting the entire operation.

### 5. Update the user interface and configuration

* **Configuration panel** – Add fields in the scan configuration dialog for enabling ClamAV scanning and specifying connection details.  Use tooltips to explain that ClamAV must be installed and running.  Persist these settings in the existing preferences storage.
* **Bulk extract dialog** – Include a checkbox “Scan files with ClamAV after download”.  If enabled, show additional fields for `clamav_mode` and connection parameters.  When scanning is enabled, disable the “skip extraction on malware indicators” option or clarify that both can work together.
* **Feedback to the user** – During extraction, display a status indicator such as “Scanning file X (n of m) …”; highlight infected files in the results list.  After extraction, provide a summary and allow the user to view or delete infected files.

### 6. Dependency management and installation

* **Dependency** – Add `clamav-client` (preferred for cross‑platform support) or `python‑clamd` to the project’s `requirements.txt`.
* **Installation instructions** – Update documentation to explain that users must install ClamAV and either start the `clamd` daemon or provide the `clamscan` path.  Provide sample commands such as `sudo apt-get install clamav-daemon && sudo systemctl start clamav-daemon`.
* **Automatic detection** – On first run or when enabling scanning, attempt to detect a running `clamd` on the default socket or port; if not found, prompt the user to specify the path or to install ClamAV.

### 7. Considerations and potential pitfalls

* **Performance** – Scanning every file will slow down extraction; provide an option to scan only files over a certain size or with risky extensions.  Use the `skip_indicator_extract_var` logic to avoid scanning when hosts are already flagged as high‑risk.
* **File types** – ClamAV can scan archives and compressed files, but scanning large archives may require enabling the appropriate ClamAV config options (like `--recursive` or `--max-scansize`).  Expose a configuration option to limit scanning depth or file size.
* **Cross‑platform support** – `clamd` sockets differ by OS (Unix domain sockets on Linux/macOS, TCP on Windows).  Provide sensible defaults and allow the user to override them.
* **False positives and user trust** – Provide an option to quarantine infected files instead of deleting them automatically.  Allow advanced users to view scan results and decide what to do.

By following this plan, Dirracuda’s existing quarantine‑after‑download pipeline can incorporate ClamAV scanning in a configurable way.  Leveraging a Python wrapper like `clamav‑client` or `python‑clamd`, starting a `clamd` daemon, and integrating asynchronous scanning after each download will provide real‑time malware detection without severely impacting the user experience.
