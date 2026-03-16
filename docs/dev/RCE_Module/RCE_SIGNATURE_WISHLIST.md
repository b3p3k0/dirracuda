# SMB RCE Signature Wishlist

Quick reference for the first wave of heuristics we want to encode inside the upcoming `signatures/rce_smb` bundle. The goal is to keep a running shortlist of impactful CVEs, capture the signals we already collect during probes, and note the extra telemetry we would need before a heuristic is worth shipping.

## Prioritization Notes
- Bias toward RCEs that are still seen in the wild (wormable, ransomware-adjacent, or widely weaponized) and that expose signals we can infer from SMB banners, share access results, or lightweight RPC calls.
- Prefer detections that rely on configuration gaps (SMB dialects, missing patches, anonymous access) over exploit simulation; we want low-noise heuristics the GUI can explain clearly.
- When a CVE depends on adjacent services (Print Spooler, Netlogon, EFSRPC), document how we can observe precursors from the data we already gather so we know if an SMB-side check is even viable.

## Candidate Signatures

### CVE-2017-0144 — EternalBlue / MS17-010
- **Why it matters:** Still one of the most exploited SMB1 bugs; fuels WannaCry/NotPetya-style worms.
- **Signals we already capture:** SMB dialect negotiation (SMB1 vs SMB2/3), OS version/build when banners or `srvsvc` RPC replies are available, share accessibility without signing.
- **Additional telemetry:** Patch level hint (e.g., Hotfix KB4012212/KB4012215), precise Windows edition when only generic banners are returned.
- **Heuristic sketch:** `SMB1 enabled` + `OS <= Windows 7 / Server 2008 R2` + `no MS17-010 hotfix seen` ⇒ weight 40 (“High risk”). Flag anonymous access as a booster since wormable chains lean on guest auth.

### CVE-2017-0143/0146 — EternalRomance / EternalSynergy (MS17-010 family)
- **Why it matters:** Same patch family as EternalBlue but targets different code paths; useful to mark systems missing the full MS17-010 rollup.
- **Signals available:** Same as above plus whether the host allows insecure “trans2” requests (gleaned from probe error codes).
- **Next steps:** Reuse the MS17-010 rules but add a secondary weight when we detect legacy “NT Status Insufficient Resources” responses that hint at the EternalRomance code path.

### CVE-2020-0796 — SMBGhost (SMBv3 compression)
- **Why it matters:** Modern-ish wormable bug affecting Windows 10/Server 2019 when SMB compression is enabled.
- **Signals available:** SMB dialect negotiation already tells us if SMB 3.1.1 is in use; probe runner can observe whether compression is advertised in the negotiate context list.
- **Additional telemetry:** Need to record Windows build number ≥ 18362 (1903) / 18363 (1909) without March 2020 patches. Also need a flag for “compression disabled” to avoid false positives.
- **Heuristic sketch:** `SMB 3.1.1` + `compression supported` + `OS build in vulnerable range` + `missing KB4551762` ⇒ weight 35 (“Moderate risk”).

### CVE-2020-1206 — SMBleed (SMBGhost companion)
- **Why it matters:** Often paired with SMBGhost for information disclosure that leads to RCE.
- **Signals available:** Same negotiate contexts as SMBGhost; if we already detect compression support we can piggyback.
- **Additional telemetry:** Need to confirm large MTU / credit charge anomalies from probe responses; may require optional diagnostics mode.
- **Heuristic sketch:** Tag hosts that are SMBGhost-positive **and** accept large read credits without signing as “critical combo,” adding a small incremental weight (e.g., +10).

### CVE-2020-1472 — ZeroLogon (Netlogon privilege escalation)
- **Why it matters:** Lets attackers become Domain Admins from unauthenticated network access; even though it targets Netlogon, detection can start from SMB context because DCs double as SMB servers.
- **Signals available:** Already record when a host identifies as a Domain Controller via Shodan data or `srvsvc.NetShareEnum` results (presence of `SYSVOL`/`NETLOGON` shares).
- **Additional telemetry:** Need to know if the host enforces secure RPC (Schannel) on Netlogon. That may require a lightweight `rpcclient` status check gated behind the RCE option.
- **Heuristic sketch:** `Host exposes NETLOGON share` + `OS build < August 2020 patches` + `secure channel not enforced` ⇒ weight 30 (“Moderate risk”).

### CVE-2021-34527 — PrintNightmare (Print Spooler RCE)
- **Why it matters:** Attack path often starts with authenticated SMB access to drop malicious DLLs, followed by Printer RPC abuse; good candidate for blended heuristics.
- **Signals available:** We already know when the Print Spooler service port (\\PIPE\\spoolss) responds during share enumeration; sandbox mode can probe spooler RPC banners.
- **Additional telemetry:** Need spooler patch level (KB5004945+). Could store spooler build string from `rpcclient` calls when credentials permit.
- **Heuristic sketch:** `Spooler pipe accessible` + `host is server-class OS` + `patch level < July 2021` ⇒ weight 25 (“Low risk”) with a note that verification needs auth.

### CVE-2021-36942 — PetitPotam (EFSRPC coerced auth)
- **Why it matters:** Abuse of EFSRPC over SMB pipes to coerce NTLM authentication; relevant for lateral movement once probes find open pipes.
- **Signals available:** Probe runner can already attempt to open `\\PIPE\\lsarpc` / `efsrpc`; logging whether calls succeed anonymously is straightforward.
- **Additional telemetry:** Need to record if the server enforces Extended Protection (EPA) or has `DisableNTLM` policies, which may require reading registry keys via authenticated RPC (future work).
- **Heuristic sketch:** `EFSRPC pipe accessible without auth` + `host advertises domain controller role OR certificate services` ⇒ weight 20 (“Low risk”) but highlight as relay-friendly.

### CVE-2008-4250 — MS08-067 (Server Service overflow)
- **Why it matters:** Ancient but still observed on legacy networks; exploit rides the same ports SMBSeek already touches.
- **Signals available:** OS fingerprints telling us if the host is Windows XP/2003; we already see that via banners and Shodan metadata.
- **Additional telemetry:** None beyond confirming unpatched status; we might infer from uptime headers or absence of later hotfixes.
- **Heuristic sketch:** `Windows XP/2003` + `SMB1 only` + `no MS08-067 hotfix` ⇒ weight 15 (“Minimal risk”) mostly for legacy reporting.

## Open Questions for Future Agents
- **File layout**: Use one YAML per CVE family (e.g., `signatures/rce_smb/data/ms17_010.yaml`, `smbghost.yaml`, etc.) inside a logical directory tree so maintenance stays simple.
- **Probe cache**: Fancy freshness tracking can wait; today’s persistence model (latest snapshot only) is sufficient until we see drift issues.
- **Evidence threshold**: Start with “at least two supporting signals” before surfacing a rule. Adjust once field data proves we need stricter or looser criteria.

Add new entries here as threats emerge or as we discover practical detection signals during field work.
