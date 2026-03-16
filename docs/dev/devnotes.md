# Development Notes

This file documents significant architectural decisions, feature removals, and lessons learned during SMBSeek development.

## 2026-01-01: Sandboxed Explore Feature Removal

### Decision
Removed the Sandboxed Explore feature entirely from the codebase (GUI buttons, backend sandbox_manager module, and all integration code).

### Rationale

The Sandboxed Explore workflow aimed to provide safe remote SMB share browsing by launching a GUI file manager (pcmanfm) inside a Podman/Docker container. While theoretically sound, the implementation encountered multiple reliability issues:

1. **Display Server Compatibility**: X11 vs Wayland detection and socket binding proved fragile across distributions
2. **Container Runtime Variability**: Podman and Docker handle display forwarding differently, requiring platform-specific workarounds
3. **GUI Integration Complexity**: Launching pcmanfm+GVFS inside containers required extensive package installation and configuration
4. **Error Handling**: Failures were often silent or cryptic, leaving users uncertain why the feature wasn't working
5. **Maintenance Burden**: Supporting multiple display servers, container runtimes, and Linux distributions created too many code paths

### What Worked

- The core `SandboxManager` abstraction cleanly detected available runtimes (Podman/Docker)
- Container-based isolation concept was sound from a security perspective
- Session logging to `~/.smbseek/logs/sandbox_sessions/` provided good audit trails

### What Failed

- Display server binding was unreliable, especially on Wayland systems
- GVFS/pcmanfm integration required too many runtime dependencies
- Platform-specific workarounds (macOS via Colima, Windows via WSL2) were untested and theoretical
- Feature had low adoption relative to complexity cost

### Artifacts Preserved

- `docs/SANDBOXED_EXPLORER_GUIDE.md` - kept for historical reference but delinked from user-facing docs
- Session logs directory structure (`~/.smbseek/logs/sandbox_sessions/`) - no longer created but not actively removed if present

### Future Considerations

If this feature returns, consider:
- Simpler approach: web-based file browser instead of native GUI
- Focus on CLI-only container interface rather than GUI integration
- Better runtime detection and graceful degradation
- Clearer user messaging when prerequisites aren't met
- Start with single-platform support (e.g., Ubuntu + X11) and expand gradually

### Related Files

Removed in this cleanup:
- `gui/utils/sandbox_manager.py` - core sandbox runtime management
- Explore button and handlers in `gui/components/server_list_window/details.py`
- Explore button and handlers in `gui/components/server_list_window/window.py`
- All Podman/Docker setup documentation from README.md

Modified:
- `README.md` - removed all sandbox/Explore references
- `docs/XSMBSEEK_CHANGELOG.md` - added removal note to [1.2.1]
- `CLAUDE.md` - updated (created) without sandbox references
