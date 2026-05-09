# Web UI Security Specification for Dirracuda

## Goals and Threat Model

The proposed web service adds a network‑accessible surface to a tool that interacts with untrusted systems and sensitive data.  The main risks include:

* **Unauthorised access** – Attackers may attempt to connect to the web UI, discover endpoints and trigger scans or download collected files.  Although Dirracuda is designed for private networks, misconfiguration could expose the service to the internet.
* **Data tampering** – Concurrent access by multiple clients or processes could corrupt the SQLite database.  SQLite allows only one writer at a time and may return `database is locked` errors under high contention【672418071378949†L57-L63】.
* **Injection and cross‑site attacks** – Endpoints that accept user input (e.g., Shodan filters, file names) must be sanitised to prevent SQL injection, command injection or cross‑site scripting (XSS).
* **File compromise** – Downloaded files may contain malware; the web UI should not serve them indiscriminately.  Dirracuda uses a quarantine directory by default and can integrate with ClamAV for post‑download scanning【479495022112005†L1395-L1399】.

## Security Requirements

### Authentication and Authorisation

1. **Mandatory credentials** – The web service must require a username and password or a pre‑shared token for all endpoints, including the root.  Anonymous access is forbidden.
2. **Credential storage** – Credentials should be stored hashed and salted in a config file (`~/.dirracuda/conf/webui_creds.json`) with restrictive permissions (`chmod 600`).  Do not hard‑code credentials in the repository.
3. **Password policy** – Enforce a minimum length (e.g., 12 characters) and encourage random passwords.  Provide a CLI for generating and setting credentials.
4. **Session management** – Use stateless JWTs or server‑side sessions with CSRF protection.  Set cookies as `HttpOnly` and `Secure` when TLS is enabled.  Sessions should expire after a configurable timeout (e.g., 30 minutes).
5. **Role support** – For initial implementation, a single admin role is sufficient.  Future extensions may introduce read‑only roles; design authorisation checks accordingly.
6. **API tokens** – Support time‑limited API tokens for CLI or automated access.  Provide an endpoint to generate, revoke and rotate tokens.  Store tokens hashed (never in plaintext) in the configuration and require them as Bearer headers on API requests.  Tokens offer a more secure mechanism for non‑interactive access than basic authentication and should be subject to the same rate limits and lockout policies as normal credentials.

### Network Hardening

1. **Local binding by default** – Listen on `127.0.0.1` to prevent remote connections.  Exposing the service to a LAN or wider network must require an explicit config change and a warning.
2. **TLS support** – When the service is bound to a non‑localhost address, TLS should be enabled.  Provide an option to specify certificate and key files.  Generate self‑signed certificates in the installer if none are provided.
3. **Rate limiting and lockout** – Implement rate limiting per IP and lock out accounts after a configurable number of failed login attempts.  This mitigates brute‑force attacks.
4. **IP allowlisting** – Allow configuration of allowed IP ranges for remote access.  For example, restrict to RFC1918 ranges or a specific VPN subnet.

### Input Validation and Sanitisation

1. **Parameter validation** – Use FastAPI’s type annotations or input schemas to validate request bodies and query parameters.  Reject unexpected fields.
2. **SQL safety** – Always use parameterised queries.  Do not construct raw SQL strings from user input.  Consider using an ORM or query builder.
3. **Command execution** – When launching scans, do not pass user input directly to the shell.  Instead, construct argument lists programmatically and use built‑in Python modules (e.g., `subprocess.run` with `shell=False`).
4. **File names and paths** – Sanitize file names used in download/extraction endpoints.  Enforce that downloads come only from the quarantine directory.  Strip path traversal sequences (e.g., `../`).
5. **HTML encoding** – Escape all output that is rendered into templates to prevent XSS.

### Database Concurrency and Integrity

1. **Write serialisation** – Only one scan should write to the database at a time.  Implement a global lock or a task queue for scanning operations.  Reads (e.g., listing servers) may proceed concurrently.
2. **WAL mode** – Enable Write‑Ahead Logging in SQLite to permit concurrent readers during writes.  Set a generous `timeout` on connections to allow writes to complete【672418071378949†L57-L63】.
3. **Connection scope** – Create separate SQLite connections per thread or process and close them promptly.  Do not share connections across threads.
4. **Input limits** – Impose limits on user‑submitted scan parameters (e.g., max number of Shodan results) to prevent overwhelming the database.

### File Handling and Malware Protection

1. **Quarantine enforcement** – All downloaded files remain in the quarantine directory (`~/.dirracuda/data/quarantine`) and should not be directly executed or opened via the web UI.  The UI may allow downloading them to the client with clear warnings.
2. **ClamAV integration** – If ClamAV is enabled (`clamav.enabled=true`), automatically scan extracted files before making them available for download【479495022112005†L1395-L1399】.  Flag files that are identified as malicious.
3. **Content‑type headers** – When serving files, set `Content-Type: application/octet-stream` and `Content-Disposition: attachment` to prevent inline execution in browsers.
4. **File size limits** – Impose a maximum file size for downloads to prevent denial‑of‑service via large files.

### Logging and Auditing

1. **Audit trail** – Log all authentication attempts (success and failure), configuration changes, scan launches and file downloads.  Include timestamps, user IDs and source IPs.  Store logs in a separate file with restricted permissions.
2. **No sensitive data in logs** – Avoid logging secrets (API keys, passwords) or full file contents.  Obfuscate IP addresses in logs if necessary for privacy.
3. **Security events** – Report suspicious activity such as repeated login failures or scans launched from unknown IPs.  Provide hooks to integrate with SIEM or alerting systems.

### Process Isolation and Principle of Least Privilege

1. **Run as dedicated user and group** – The daemon should run under its own system account (e.g., `dirracuda`) with a matching group.  Its home directory (e.g., `/var/lib/dirracuda` or `/home/dirracuda`) should contain the `.dirracuda` configuration, database and quarantine directories.  These directories must be owned by the `dirracuda` user and group and have restrictive permissions (e.g., `chmod 750`).  The service account should have read/write access only to these directories and its own code.  Use `NoNewPrivileges=yes` and other hardening options in the systemd unit.
2. **File system isolation** – Use `ProtectSystem=full` and `ProtectHome=yes` in the systemd unit to prevent the process from reading sensitive paths.  On systems without systemd, emulate this by sandboxing (e.g., using `chroot` or container technologies).
3. **Seccomp/AppArmor profiles** – For extra defence, supply a seccomp or AppArmor profile limiting system calls.  This guards against vulnerabilities in Python or its dependencies.
4. **Directory ownership scheme** – To allow the CLI to access the same database while keeping the service isolated, make the `.dirracuda` configuration, database and quarantine directories owned by the primary user (e.g., `kevin`) with group `dirracuda` (e.g., `chown -R kevin:dirracuda ~/.dirracuda`) and permissions such as `770` or `750`.  The service user `dirracuda` is a member of the `dirracuda` group, giving it read/write access within these directories.  Avoid adding the service user to the primary user’s group; this prevents the daemon from accessing other files in the user’s home while still allowing both processes to share the database via group permissions.

### Configuration Management

1. **Centralised config** – Store web UI settings (`bind_address`, `port`, `tls_cert`, `tls_key`, `rate_limit`, `allowed_ips`, etc.) in `~/.dirracuda/conf/webui.json`.  Use a JSON schema to validate this file on load.
2. **Secure defaults** – Default to `bind_address=127.0.0.1`, `tls_enabled=false`, `rate_limit=10 req/s`, `lockout_threshold=5` failed logins.  Document each setting clearly.
3. **Configuration changes** – Changes via the UI or CLI should be written atomically and take effect only after validation.  Consider requiring an existing session to re‑authenticate before committing changes.

### Installation and Permissions

1. **Installer tasks** – The installer should create a dedicated system user (`dirracuda`) and a matching group (`dirracuda`).  It should set the `.dirracuda` directories (configuration, database, quarantine) to be owned by the primary user (e.g., `kevin`) with group `dirracuda` (via `chown -R kevin:dirracuda ~/.dirracuda`) and assign restrictive permissions such as `770` or `750` so that only the owner and group can access them.  The service user must belong to the `dirracuda` group so it can read and write within these directories.  Do **not** add the service user to the primary user’s group, as this could allow it to read unrelated files in the home directory.  Commands such as `sudo groupadd dirracuda`, `sudo useradd -r -s /usr/sbin/nologin -g dirracuda dirracuda` and `sudo chown -R $USER:dirracuda ~/.dirracuda` can be used during installation.
2. **Manual setup instructions** – For environments where the installer is not used, provide clear documentation describing how to create the service account and group, set up the `.dirracuda` directory ownership and permissions (owner `kevin`, group `dirracuda`, mode `770`), and update the systemd unit to run as `dirracuda`.  Emphasise that the `dirracuda` user should *only* belong to its own group and that the web service should not run under a personal account.  Include commands to change group ownership (`chown -R $USER:dirracuda ~/.dirracuda`) and set directory permissions (`chmod -R 770 ~/.dirracuda`).
3. **Safe directory location** – Either keep the `.dirracuda` directory in the primary user’s home with group ownership and permissions as described above, or relocate it into a dedicated system path such as `/var/lib/dirracuda` for stronger isolation.  In either case, adjust the CLI and service configuration to reference the correct directory via an environment variable or configuration setting.  Avoid storing the configuration and database in an unprotected location and ensure that sensitive data is not exposed.

## Response Handling and Error Reporting

* **Consistent status codes** – Use standard HTTP status codes (200 OK, 201 Created, 400 Bad Request, 401 Unauthorized, 403 Forbidden, 404 Not Found, 500 Internal Server Error).
* **Generic error messages** – Do not reveal internal paths, stack traces or sensitive information in error messages.  Provide generic responses to the client and log details internally.
* **Input echo** – Avoid reflecting user input directly in responses without escaping.

## Compliance and Ethical Use

Dirracuda is a security research tool.  The web UI must include a disclaimer reminding users to obtain permission before scanning any systems【479495022112005†L1414-L1418】.  Provide links to the README’s baseline precautions (VPN, VM isolation, etc.)【485255009257124†L384-L397】 and reinforce that the tool is for authorised research only【479495022112005†L1414-L1418】.

By adhering to this specification, the web interface can offer the convenience of browser‑based management without compromising the security of the user’s environment or the targets being scanned.