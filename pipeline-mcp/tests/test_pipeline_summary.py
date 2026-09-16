from __future__ import annotations

from pipeline_mcp.models import PipelineResult
from pipeline_mcp.models import SequenceRecord
from pipeline_mcp.models import TierResult
from pipeline_mcp.pipeline import _summary_json_payload


def test_pipeline_summary_omits_large_inline_af2_payloads() -> None:
    result = PipelineResult(
        run_id="run_1",
        output_dir="/tmp/run_1",
        msa_a3m_path=None,
        msa_filtered_a3m_path=None,
        msa_tsv_path=None,
        conservation_path=None,
        ligand_mask_path=None,
        surface_mask_path=None,
        tiers=[
            TierResult(
                tier=0.7,
                fixed_positions={"A": [1, 2]},
                proteinmpnn_native=None,
                proteinmpnn_samples=[SequenceRecord(id="seq1", sequence="ACDE")],
                af2={
                    "seq1": {
                        "best_plddt": 91.0,
                        "archive_base64": "abc123",
                        "ranked_0_pdb": "MODEL 1\nENDMDL\n",
                        "nested": {"out_dir_zip_b64": "def456"},
                    }
                },
            )
        ],
    )

    payload = _summary_json_payload(result)
    record = payload["tiers"][0]["af2"]["seq1"]

    assert record["best_plddt"] == 91.0
    assert record["archive_base64"]["omitted"] is True
    assert record["ranked_0_pdb"]["omitted"] is True
    assert record["nested"]["out_dir_zip_b64"]["omitted"] is True
