import json
from pathlib import Path

from a_share_agent.backtest.data_integrity import sha256_file
from a_share_agent.backtest.trusted_research_sources import audit_trading_rule_provenance
from a_share_agent.backtest.universe_source_semantics import validate_universe_source_semantics


def test_source_wide_semantics_passes_only_with_bound_physical_evidence(tmp_path: Path):
    snapshot_dir = tmp_path / "data" / "backtest" / "official_universe_snapshots"
    raw_dir = snapshot_dir / "raw_registers"
    evidence_dir = tmp_path / "data" / "backtest" / "evidence"
    raw_dir.mkdir(parents=True)
    evidence_dir.mkdir(parents=True)

    sources = {}
    for source_file, raw_field, token in (
        ("szse_delisted_register.csv", "终止上市日期", "szse"),
        ("sse_delisted_register.csv", "摘牌日期", "sse"),
        ("bse_delisted_register.csv", "终止上市日期", "bse"),
    ):
        artifact = evidence_dir / f"{token}-semantics.txt"
        artifact.write_text(f"official {token} source-wide date-field documentation", encoding="utf-8")
        sources[source_file] = {
            "raw_date_field": raw_field,
            "normalized_field": "delisting_date",
            "semantic_status": "SOURCE_WIDE_VERIFIED",
            "allow_as_delisting_date": True,
            "source_wide_evidence": {
                "source_document_id_or_url": f"https://exchange.example/{token}/delisting-rules",
                "raw_source_path": f"data/backtest/evidence/{artifact.name}",
                "raw_source_hash": sha256_file(artifact),
            },
        }

    semantics_path = raw_dir / "source_semantics.json"
    semantics_path.write_text(json.dumps({"sources": sources}, ensure_ascii=False), encoding="utf-8")

    verification_path = raw_dir / "sse_delisting_date_verification.csv"
    verification_path.write_text(
        "symbol,raw_date,verified_delisting_date,verification_status,official_announcement_url\n"
        "600001.SH,2026-01-08,2026-01-08,MATCH,https://exchange.example/sse/notice-1\n",
        encoding="utf-8",
    )

    (snapshot_dir / "manifest.json").write_text(
        json.dumps(
            {
                "source_semantics": {
                    "file": "raw_registers/source_semantics.json",
                    "sha256": sha256_file(semantics_path),
                    "sse_verification_sha256": sha256_file(verification_path),
                }
            }
        ),
        encoding="utf-8",
    )

    audit = validate_universe_source_semantics(raw_dir)
    assert audit["sse_sample_evidence_valid"] is True
    assert audit["sse_sample_evidence_hash_bound"] is True
    assert audit["manifest_binding_audit"]["semantics_hash_match"] is True
    assert audit["unverified_source_wide_evidence"] == []
    assert audit["source_wide_verified"] is True
    assert audit["ready"] is True


def test_trading_rule_provenance_passes_with_physical_official_artifact(tmp_path: Path):
    backtest = tmp_path / "data" / "backtest"
    evidence = backtest / "evidence"
    evidence.mkdir(parents=True)
    rule_doc = evidence / "official-rules.txt"
    rule_doc.write_text("official exchange trading rule document", encoding="utf-8")
    digest = sha256_file(rule_doc)

    (backtest / "trading_rules_provenance.json").write_text(
        json.dumps(
            {
                "source_type": "INDEPENDENT_OFFICIAL_RULE_DOCUMENTS",
                "source_dataset_id": "official-rules-test",
                "rule_checks": {
                    "check_a": {
                        "rule_reference": "Article 1",
                        "source_document_id_or_url": "https://exchange.example/rules#article-1",
                        "raw_source_path": "data/backtest/evidence/official-rules.txt",
                        "raw_source_hash": digest,
                    },
                    "check_b": {
                        "rule_reference": "Article 2",
                        "source_document_id_or_url": "https://exchange.example/rules#article-2",
                        "raw_source_path": "data/backtest/evidence/official-rules.txt",
                        "raw_source_hash": digest,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    audit = audit_trading_rule_provenance(tmp_path, ["check_a", "check_b"])
    assert audit["verified"] is True
    assert audit["missing_checks"] == []
    assert audit["invalid_checks"] == []
