"""Synthetic report data shared in shape with the report-window regression tests."""
from experimental.analyst.report_json import (
    Coverage,
    GroundedFact,
    HostRead,
    RunMeta,
    TopExposure,
    build_report_json,
)

def analyst_report():
    return build_report_json(
        RunMeta(
            run_id="a" * 32,
            report_label="host12",
            read_mode="quick",
            model_tag="qwen-test",
            model_digest="0" * 64,
            created_at_utc="2026-09-18T12:00:00Z",
            files_read=310,
            files_total=325,
            flagged_files=22,
        ),
        HostRead(
            host_summary=(
                "Small-business accounting server holding client tax and payroll files."
            ),
            likely_owner="Anytown Tax & Books LLC",
            contacts=("office@anytowntax.example", "(555) 123-4567"),
            risk_level="HIGH",
            top_exposures=(
                TopExposure(
                    rank=1,
                    severity="HIGH",
                    text="Client SSNs in 2023_returns.xlsx (48 rows)",
                ),
                TopExposure(
                    rank=2,
                    severity="MED",
                    text="Payroll bank accounts in payroll_q3.csv",
                ),
            ),
        ),
        (
            GroundedFact(
                kind="ssn",
                category="pii",
                quote="123-45-6789",
                file="2023_returns.xlsx",
                provenance="sheet 1 row 2",
                rank="HIGH",
                source="detector",
            ),
            GroundedFact(
                kind="bank_account",
                category="financial",
                quote="ending in 4321",
                file="payroll_q3.csv",
                provenance="row 8",
                rank="MED",
                source="model",
            ),
            GroundedFact(
                kind="email",
                category="contact",
                quote="office@anytowntax.example",
                file="contacts.vcf",
                provenance="line 4",
                rank="low",
                source="detector",
            ),
            GroundedFact(
                kind="age",
                category="demographic",
                quote="age 42",
                file="client.txt",
                provenance="line 1",
                rank="low",
                source="model",
            ),
        ),
        Coverage(
            discovered=325,
            terminal=325,
            no_text_layer=5,
            parse_failed=4,
            unsupported=6,
        ),
    )
