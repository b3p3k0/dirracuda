# Keymaster Decisions Log

Date: 2026-04-25

Status: all initial questions resolved by HI.

## Q1) Provider Scope for v1

Decision:
1. Scope is strictly Shodan in v1.
2. Keep a lightweight generic provider contract in storage/model layers so future providers can be added without a full redesign.

## Q2) Apply Target Semantics

Decision:
1. Apply writes and persists `shodan.api_key` in active config only.
2. Running scans continue with the key active at scan start; key changes apply to future scans.

## Q3) Key Display Policy

Decision:
1. Show first four and last four characters with asterisks in the middle (e.g. `ABCD********WXYZ`).
2. This enables quick visual identification without exposing full keys.

## Q4) Delete Confirmation Mute

Decision:
1. Keep simple delete confirmation only.
2. No mute/suppression option in v1.
