# Architecture Sketch (HTTP Integration Workstream)

## Summary

Reserved for HTTP-specific host-list/import architecture, including row identity and protocol-aware routing.

## Data Model (Draft)

```text
SMB side: smb_servers + host_user_flags + host_probe_cache + share_access
FTP side: ftp_servers + ftp_user_flags + ftp_probe_cache + ftp_access
HTTP side (planned): http_servers + http_user_flags + http_probe_cache + http_access
```

## Unified Host Browser Query (Draft)

```text
UNION ALL across protocol-specific tables with explicit host_type key
```

## Migration Flow (Invisible)

```text
App start -> run_migrations(db_path) -> additive/idempotent changes -> continue
```

## Action Routing Sketch

```text
Selected row host_type:
S -> SMB actions
F -> FTP actions
H -> HTTP actions
```

