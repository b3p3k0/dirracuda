# Shodan Credit Optimization

Status: in progress

## Goal

Improve discovery value-per-credit across SMB/FTP/HTTP while keeping scan spend predictable and visible before launch.

## Working Model

- Sidecar bench harness: `tools/smb_credit_lab.py` (SMB strategy lab)
- Production guardrail: per-protocol Shodan candidate caps
  - `query_cap.smb_max_shodan_results_per_scan`
  - `query_cap.ftp_max_shodan_results_per_scan`
  - `query_cap.http_max_shodan_results_per_scan`
- Compatibility guardrail: runtime scan overrides derive internal credit budgets from caps with `ceil(cap / 100)`.
- UX transparency: preflight shows live balance and estimated post-scan balance.
  - If live balance is unavailable, numeric estimates are suppressed and a dashboard link is shown.

## Strategy Matrix

- `strict_1_credit`: one-page SMB cap (wallet-safe default)
- `adaptive_2_credit`: allow two pages, stop early when target reached
- `adaptive_3_credit`: allow three pages, stop early when target reached
- `reference_current`: comparison baseline using configured max-results behavior

## Required Product Doc Touchpoints

- `README.md`
- `docs/TECHNICAL_REFERENCE.md`

Both must be reviewed and updated whenever the budget/estimate behavior changes.
