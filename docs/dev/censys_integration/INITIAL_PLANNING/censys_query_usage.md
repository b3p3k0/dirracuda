# Censys Query & Usage Guide for Dirracuda

This guide provides concrete examples of translating Dirracuda’s Shodan search patterns into **Censys Query Language (CenQL)**.  It also summarises recommended API usage patterns and field selections.

## 1. CenQL Basics

- **Field names:** Use `host.services.port`, `host.services.protocol`, `host.services.banner`, etc.  See Censys documentation for full field list【317031200555299†L590-L604】.
- **Nested filters:** Wrap multiple conditions in `host.services:( … )` to ensure they apply to a single service【317031200555299†L671-L687】.
- **String matching:** Strings are case‑insensitive.  Use `"value"` for exact match and `"*value*"` for substring.
- **Relative time:** Filter results by scan time using ranges such as `host.services.scan_time:[now-72h TO *]`.

## 2. Common Service Queries

### FTP (Port 21)

| Description | CenQL Query | Notes |
|---|---|---|
| Basic FTP service on port 21 | `host.services:(protocol=FTP and port=21)` | Returns hosts where the FTP protocol is observed on TCP 21. |
| FTP with welcome banner containing keyword | `host.services:(protocol=FTP and port=21 and banner:"welcome")` | Equivalent to Shodan’s `port:21 "welcome"`. |
| FTP with ProFTPD | `host.services:(protocol=FTP and banner:"ProFTPD")` | Use to target specific server software. |
| FTP scanned within last 24 h | `host.services:(protocol=FTP and port=21) and host.services.scan_time:[now-24h TO *]` | Ensures data freshness. |
| FTP with TLS support | `host.services:(protocol=FTP and port=21 and host.services.tls.implicit_tls=true)` | Checks for implicit FTPS【159627258083314†L8-L17】. |

### SMB (Port 445)

| Description | CenQL Query | Notes |
|---|---|---|
| SMB/CIFS service on port 445 | `host.services:(protocol=SMB and port=445)` | SMB is classified separately from FTP. |
| SMB with anonymous access | `host.services:(protocol=SMB and banner:"anonymous login allowed")` | Example placeholder; refine once tested. |
| SMB scanned within last 72 h | `host.services:(protocol=SMB and port=445) and host.services.scan_time:[now-72h TO *]` | Useful for data freshness. |

### HTTP (Ports 80 and 443)

| Description | CenQL Query | Notes |
|---|---|---|
| HTTP service on port 80 | `host.services:(protocol=HTTP and port=80)` | May return plain HTTP services. |
| HTTP service on port 443 (HTTPS) | `host.services:(protocol=HTTP and port=443)` | Excludes HTTP/2; use `transport_protocol=tcp` by default. |
| HTTP servers with a specific server header | `host.services:(protocol=HTTP and port=80 and host.services.http.response.headers.server:"Apache")` | Demonstrates nested subfield search.  Requires including `host.services.http.response.headers.server` in the `fields` list. |
| Web servers scanned within last 7 days | `host.services:(protocol=HTTP) and host.services.scan_time:[now-7d TO *]` | Accepts relative days. |

## 3. Selecting Fields

When calling the search API, specify a `fields` list to include only the data you need.  The following fields are recommended as a starting point:

- `host.ip` – IP address of the host.
- `host.services.port` – port number.
- `host.services.protocol` – protocol name (FTP, SMB, HTTP).
- `host.services.banner` – banner text returned by the service.
- `host.services.scan_time` – timestamp of the scan.
- `host.services.banner_hash_sha256` – hash of the banner for deduplication【612288861349681†L360-L366】.
- `host.services.tls.implicit_tls` – boolean for FTP implicit TLS【159627258083314†L8-L17】.
- `host.services.software.product` and `host.services.software.version` – if available.

Include additional nested fields (e.g., `host.services.http.response.headers.server`) when you plan to search or display them.

## 4. API Usage Pattern (Python SDK)

1. **Authentication:**
   ```python
   from censys.search import CensysHosts
   api = CensysHosts(api_key="YOUR_TOKEN")
   ```
2. **Build query:**
   ```python
   query = build_cenql(protocol="FTP", port=21, banner="welcome", hours=72)
   fields = ["host.ip", "host.services.port", "host.services.protocol", "host.services.banner", "host.services.scan_time"]
   ```
3. **Execute search with pagination:**
   ```python
   results = api.search(query=query, per_page=100, fields=fields)
   for page_num, result_page in enumerate(results):
       for hit in result_page:
           ip = hit["host"]["ip"]
           for svc in hit.get("services", []):
               if svc.get("protocol") == "FTP":
                   # Build Candidate
                   ...
       if page_num + 1 >= max_pages:
           break
   ```
4. **Credit management:** each page costs 5 credits (standard query).  Multiply `page_num + 1` by 5 and stop when it meets or exceeds the configured credit budget【300993859225417†L230-L301】.

## 5. Tips and Caveats

- **Case‑sensitive filters:** CenQL is case‑insensitive; avoid relying on exact casing.  Use quotes around multi‑word banner strings.
- **Multiple services per host:** Always check the `protocol` of each matched service.  Do not assume the first service in `matched_services` corresponds to the filter.
- **Stale data:** Use `scan_time` filters to mitigate stale results【317031200555299†L590-L604】.
- **IPv6 addresses:** IPv6 support is not needed at this stage but can be enabled later; adjust data models accordingly.

This guide should serve as a reference when implementing and testing Censys queries for Dirracuda.  Adjust queries based on real‑world observations and the evolving Censys dataset.
