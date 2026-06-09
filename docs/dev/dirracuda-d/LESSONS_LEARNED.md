# Dirracuda Daemon Lessons Learned

1. The existing detached controller kept stderr connected to a short-lived
   reader thread. A persistent file or service-manager journal is required for
   a real CLI daemon.
2. Boolean running state was insufficient for safe stop/restart. Process
   ownership and HTTP health must be represented independently.
3. A user unit must be treated as an ownership boundary. Automatic backend
   selection prevents direct and systemd launches from competing for the port.
4. `systemctl --user enable` does not guarantee pre-login boot startup. Avoiding
   lingering changes keeps v1 user-scoped and unsurprising.
5. Reusing public auth/config APIs kept daemon work independent from password
   hashing and request-time security implementation.
6. Focused GUI tests allowed backend UX changes without further growing the
   oversized experimental dialog test module.
7. Detached log rotation belongs in the long-lived server. Rotating before
   launch does not constrain a process that runs for days.
8. A rate limiter is itself a storage attack surface. Hash untrusted subjects,
   combine account/IP and IP-wide controls, and cap total rows.
9. Security posture can remain operationally successful while still carrying
   warnings. Stable daemon exit codes and additive JSON details preserve
   automation compatibility.
10. Wildcard listeners require a separate Host trust policy. Treat IP literals
    as addresses and configured DNS names as explicit operator intent.
