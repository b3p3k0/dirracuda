# C1 Approved Plan: Shared Redirect-Safe HTTP Transport

Status: approved v5 by HI and Codex RA

## Objective

Replace the four target-facing `urllib.request.urlopen` paths with one
standard-library transport that disables ambient proxies and permits at most
three same-origin redirects.

## Confirmed Root Cause

Verifier, browser read/download, probe/listing through the verifier, and bulk
HTTP extraction each owned transport behavior independently. The default
urllib opener inherited proxy environment variables and followed redirects
without the required destination-identity policy.

## Non-Goals

- Do not implement the C2 recorded-IP/SNI connection adapter.
- Do not change TLS policy ownership; that is C3.
- Do not change body, file, or aggregate streaming limits.
- Do not change public CLI output, dependencies, schema, auth, or CI.
- Do not migrate unrelated upstream API clients.

## Required Interface

Add `shared/http_transport.py` with:

```python
http_open(
    *,
    connect_host: str,
    request_host: Optional[str],
    scheme: str,
    port: int,
    path: str,
    headers: Optional[dict],
    context: Optional[ssl.SSLContext],
    timeout: float,
)
```

The module also exposes stable `RedirectBlockedError` and
`RedirectLimitError` exceptions.

## Redirect Contract

1. Treat `(scheme, request_host or connect_host, effective port)` as the
   logical origin.
2. Normalize hostname comparisons using IP canonicalization or lowercase IDNA.
3. Resolve relative redirects against the current logical URL.
4. Reject scheme, normalized host, or effective-port changes.
5. Reject userinfo, unsupported schemes, invalid ports, malformed authority,
   and missing or empty `Location`.
6. Permit three redirects; reject the fourth.
7. Rewrite accepted redirects to `connect_host`, preserving only validated
   path parameters and query.
8. Preserve the logical `Host` header on every hop.
9. Disable proxy inheritance with `ProxyHandler({})`.
10. Drain and close redirect response objects on success and close them on all
    blocked or limit paths.

Support Python 3.8 by handling status 308 in the custom handler and mapping its
method-preserving behavior through the available 307 implementation.

## Caller Migration

- `commands/http/verifier.py`: use `connect_host=ip`; return
  `redirect_blocked` and `redirect_limit`.
- `shared/http_browser.py`: migrate `download_file` and `read_file`; retain
  `connect_host=request_host or ip` for current HTTPS/SNI compatibility until
  C2.
- `gui/utils/protocol_extract_runner.py`: migrate `_http_download_file` using
  its current active connection host.
- `gui/utils/http_probe_runner.py` and classifier paths remain indirect callers
  through `try_http_request`.

## Required Tests

Add local HTTP/HTTPS fixture tests for:

- relative and absolute same-origin redirects;
- cross-scheme, cross-host, cross-port, scheme-relative, credential-bearing,
  malformed, locationless, self-referential, and fourth-hop rejection;
- exactly three successful hops;
- logical-host validation with physical destination rewrite;
- path parameters and query preservation;
- response cleanup on blocked and limit paths;
- uppercase and lowercase HTTP/HTTPS proxy variables;
- IPv6 behavior where loopback is available;
- IDNA Unicode/punycode equivalence and invalid IDNA;
- non-ASCII `Location` quoting;
- 308 compatibility;
- response object interface compatibility.

Existing HTTP operation, browser, probe, and extract tests must remain green.

## Expected Files

- Add `shared/http_transport.py`.
- Add `shared/tests/test_http_transport.py`.
- Modify `commands/http/verifier.py`.
- Modify `shared/http_browser.py`.
- Modify `gui/utils/protocol_extract_runner.py`.

## Validation

```bash
./venv/bin/python -m pytest shared/tests/test_http_transport.py -q
./venv/bin/python -m pytest \
  shared/tests/test_http_operation.py \
  gui/tests/test_http_browser_window.py \
  gui/tests/test_http_probe.py \
  gui/tests/test_protocol_extract_runner.py -q
./venv/bin/python -m pytest \
  gui/tests/test_extract_runner_clamav.py \
  shared/tests/test_quarantine_postprocess.py -q
./venv/bin/python -m pytest \
  gui/tests/test_data_import_engine.py \
  gui/tests/test_ftp_browser.py \
  gui/tests/test_browser_viewer_keybindings.py -q
./venv/bin/python -m py_compile \
  shared/http_transport.py \
  commands/http/verifier.py \
  shared/http_browser.py \
  gui/utils/protocol_extract_runner.py
rg -n "urllib\\.request\\.urlopen" \
  commands/http shared/http_browser.py gui/utils/http_probe_runner.py \
  gui/utils/protocol_extract_runner.py
git diff --check
```

The static search is successful only when it returns no matches.

## Line-Count Risk

All expected source and test files remain below the 1,200-line excellent
threshold. No modularization stop is expected.

## Rollback

Revert the single C1 commit, removing the shared transport and restoring the
three migrated caller modules.
