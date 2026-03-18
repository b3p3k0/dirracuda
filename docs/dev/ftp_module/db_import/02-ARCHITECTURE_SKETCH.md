# Architecture Sketch (Dual-Row S/F Model)

## Summary

Keep SMB and FTP host registries physically separate in DB, then provide a unified protocol list query for UI.

This avoids high-risk table collapse while delivering the required UX:

```text
S 1.2.3.4 ...
F 1.2.3.4 ...
```

## Data Model (Target MVP)

```text
SMB side
--------
smb_servers (id, ip_address, ...)
  -> host_user_flags   (server_id -> smb_servers.id)
  -> host_probe_cache  (server_id -> smb_servers.id)
  -> share_access      (server_id -> smb_servers.id)

FTP side
--------
ftp_servers (id, ip_address, port, banner, anon_accessible, ...)
  -> ftp_user_flags    (server_id -> ftp_servers.id)
  -> ftp_probe_cache   (server_id -> ftp_servers.id)
  -> ftp_access        (server_id -> ftp_servers.id)
```

## Unified Host Browser Query

```text
SELECT
  'S' AS host_type,
  s.id AS protocol_server_id,
  s.ip_address,
  s.country,
  s.country_code,
  s.auth_method,
  s.last_seen,
  ... SMB share summary fields ...,
  uf.favorite,
  uf.avoid,
  pc.status,
  pc.extracted
FROM smb_servers s
LEFT JOIN host_user_flags uf ON uf.server_id = s.id
LEFT JOIN host_probe_cache pc ON pc.server_id = s.id

UNION ALL

SELECT
  'F' AS host_type,
  f.id AS protocol_server_id,
  f.ip_address,
  f.country,
  f.country_code,
  'anonymous' AS auth_method,
  f.last_seen,
  ... FTP summary fields ...,
  fuf.favorite,
  fuf.avoid,
  fpc.status,
  fpc.extracted
FROM ftp_servers f
LEFT JOIN ftp_user_flags fuf ON fuf.server_id = f.id
LEFT JOIN ftp_probe_cache fpc ON fpc.server_id = f.id
```

Notes:
- Row identity is `(host_type, protocol_server_id)`, not just IP.
- Same IP across protocols intentionally yields two rows.

## Migration Flow (Invisible)

```text
App start
  -> run_migrations(db_path)
      -> CREATE TABLE IF NOT EXISTS ftp_user_flags
      -> CREATE TABLE IF NOT EXISTS ftp_probe_cache
      -> ALTER TABLE ftp_probe_cache add missing columns (idempotent)
      -> commit
  -> app continues normally
```

No destructive rewrites in MVP migration.

## Action Routing Sketch

```text
Selected row
  |
  +-- host_type = 'S' --> SMB browse/probe/extract/status methods
  |
  +-- host_type = 'F' --> FTP browse/probe/extract/status methods
```

## Deletion Sketch

```text
Delete selected row
  |
  +-- type='S' -> delete smb_servers row (and SMB cascades only)
  |
  +-- type='F' -> delete ftp_servers row (and FTP cascades only)
```
