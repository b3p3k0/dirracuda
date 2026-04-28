# Shodan Credit Optimization

Status: in progress

## Goal

Improve discovery value-per-credit across SMB/FTP/HTTP while keeping scan spend predictable and visible before launch.

## Working Model

- Sidecar bench harness: `tools/smb_credit_lab.py` (SMB strategy lab)
- Production guardrail: per-protocol discovery credit budget caps
  - `smb_max_query_credits_per_scan`
  - `ftp_max_query_credits_per_scan`
  - `http_max_query_credits_per_scan`
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
