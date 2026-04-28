# Lessons Learned: Shodan Credit Optimization

1. A single API call does not guarantee a single query credit; result-page volume drives credit burn.
2. Budget caps must be explicit in runtime config and visible in scan UI before launch.
3. Benchmark strategy outcomes using the same query + exclusion + recent-host filters to avoid misleading comparisons.
4. Wallet-safe defaults are critical for first-run trust; adaptive expansion should be opt-in via budget settings.
5. Keep credit-estimate language approximate and transparent; show assumptions in the preflight summary.
6. Never show numeric scan-cost estimates from stale/local cache when live balance is unavailable; show explicit unavailable state instead.
7. Keep protocol parity for credit governance (SMB/FTP/HTTP) to prevent confusing spend differences between scan types.
