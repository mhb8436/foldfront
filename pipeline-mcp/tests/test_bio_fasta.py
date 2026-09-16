"""Tests for pipeline_mcp.bio.fasta — focused on FastaRecord.id uniqueness
when parsing UniProt standard-format headers."""
from __future__ import annotations

from pipeline_mcp.bio.fasta import FastaRecord, parse_fasta


def test_trembl_headers_produce_unique_ids():
    text = (
        ">tr|A0ABQ6CHT4|A0ABQ6CHT4_9HYPH Mannose-6-phosphate isomerase OS=Labrys\nACDEFG\n"
        ">tr|D6U627|D6U627_KTERA N-acylglucosamine 2-epimerase\nHIJKLM\n"
        ">tr|M0LQW9|M0LQW9_9EURY\nNOPQRS\n"
    )
    records = parse_fasta(text)
    ids = [r.id for r in records]
    assert ids == ["tr_A0ABQ6CHT4", "tr_D6U627", "tr_M0LQW9"]
    assert len(set(ids)) == 3


def test_swissprot_headers_produce_unique_ids():
    text = (
        ">sp|P12345|RENBP_HUMAN protein\nACDEFG\n"
        ">sp|Q67890|ENZYME_RAT another\nHIJKLM\n"
    )
    records = parse_fasta(text)
    assert [r.id for r in records] == ["sp_P12345", "sp_Q67890"]


def test_mixed_uniprot_and_custom_headers():
    text = (
        ">P17560|RENBP_PIG|Sus_scrofa|EC_unknown|validated\nACDEFG\n"
        ">tr|A0ABQ6CHT4|A0ABQ6CHT4_9HYPH\nHIJKLM\n"
        ">sp|P12345|RENBP_HUMAN\nNOPQRS\n"
        ">sample_1 score=1.0\nTUVWXY\n"
    )
    records = parse_fasta(text)
    ids = [r.id for r in records]
    assert ids == ["P17560", "tr_A0ABQ6CHT4", "sp_P12345", "sample_1"]
    assert len(set(ids)) == 4


def test_uniprot_prefix_case_insensitive():
    text = (
        ">TR|A0ABQ6CHT4|name\nACDEFG\n"
        ">SP|P12345|other\nHIJKLM\n"
    )
    records = parse_fasta(text)
    assert [r.id for r in records] == ["tr_A0ABQ6CHT4", "sp_P12345"]


def test_proteinmpnn_style_headers_keep_full_id():
    text = (
        ">sample_1 T=0.1, sample=1, score=1.04\nACDEFG\n"
        ">sample_2 T=0.1, sample=2, score=1.05\nHIJKLM\n"
    )
    records = parse_fasta(text)
    assert [r.id for r in records] == ["sample_1", "sample_2"]


def test_empty_header_falls_back():
    rec = FastaRecord(header="", sequence="ACDE")
    assert rec.id == "seq"


def test_uniprot_truncated_header_falls_back_gracefully():
    # Header that starts like UniProt but has no accession field (e.g. "tr" only)
    text = (
        ">tr\nACDEFG\n"
    )
    records = parse_fasta(text)
    # Without an accession, falls back to original behavior
    assert records[0].id == "tr"
