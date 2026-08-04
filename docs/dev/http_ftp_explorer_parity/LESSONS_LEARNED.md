# HTTP/FTP Explorer Parity - Lessons Learned

## Carry Forward

1. Recursive protocol extract support is not the same as explorer support. When
   FTP/HTTP bulk extract gained recursive runners, the browser `Download to
   Quarantine` handlers still rejected directory rows first. Future parity work
   needs tests at both levels: protocol runner scope and UI button routing.
2. Selected-directory extract inputs must be explicit. Omitted starts preserve
   legacy root extraction; supplied starts must stay scoped to the selected
   files/folders and must not fall back to `/` on HTTP listing failure.
3. Stop enumeration as soon as file/size/time limits are reached. Continuing to
   list queued subdirectories after a limit is hit wastes remote requests and
   makes cancellation/status behavior harder to reason about.
4. Browser download paths must test tmpfs-active quarantine routing. A path that
   works with disk quarantine can still bypass in-memory quarantine if it does
   not refresh and resolve through the shared tmpfs runtime before allocation.
