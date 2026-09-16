import base64
import gzip
import json
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

from pipeline_mcp.bio.pdb import ca_rmsd
from pipeline_mcp.bio.pdb import residues_by_chain
from pipeline_mcp.bio.pdb import sequence_by_chain
from pipeline_mcp.models import PipelineRequest
from pipeline_mcp.models import SequenceRecord
from pipeline_mcp.pipeline import _build_backbone_source_summaries
from pipeline_mcp.pipeline import _clear_stage_outputs_from
from pipeline_mcp.pipeline import _effective_rfd3_mode
from pipeline_mcp.pipeline import _filter_backbones_by_target_rmsd
from pipeline_mcp.pipeline import _preprocess_pdb_text
from pipeline_mcp.pipeline import _proteinmpnn_input_pdb_text
from pipeline_mcp.pipeline import _resolve_backbone_preprocess_options
from pipeline_mcp.pipeline import _resolve_pipeline_chain_strategy
from pipeline_mcp.pipeline import _target_record_from_pdb
from pipeline_mcp.pipeline import PipelineInputRequired
from pipeline_mcp.pipeline import PipelineRunner
from pipeline_mcp.tools import ToolDispatcher
from pipeline_mcp.tools import pipeline_request_from_args


@contextmanager
def _tmpdir():
    base = Path(__file__).resolve().parent / "_tmp"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"run_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    yield str(path)


def _simple_ca_backbone(offset: float) -> str:
    return (
        f"ATOM      1  CA  ALA A   1      {offset:8.3f}{0.000:8.3f}{0.000:8.3f}  1.00 20.00           C\n"
        f"ATOM      2  CA  GLY A   2      {offset + 1.000:8.3f}{0.000:8.3f}{0.000:8.3f}  1.00 20.00           C\n"
        f"ATOM      3  CA  SER A   3      {offset + 2.000:8.3f}{0.000:8.3f}{0.000:8.3f}  1.00 20.00           C\n"
        "END\n"
    )


def _pdb_atom(
    serial: int,
    atom_name: str,
    resname: str,
    chain_id: str,
    resseq: int,
    x: float,
    y: float,
    z: float,
    element: str,
) -> str:
    return (
        f"ATOM  {serial:5d} {atom_name:>4s} {resname:>3s} {chain_id}{resseq:4d}    "
        f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00 20.00           {element:>2s}\n"
    )


def _bent_ca_backbone(
    mid_y: float,
    third_y: float = 0.0,
    third_z: float = 0.0,
    tail_y: float = 0.0,
) -> str:
    coords = [
        (1, "ALA", 0.0, 0.0, 0.0),
        (2, "GLY", 1.0, mid_y, 0.0),
        (3, "SER", 2.0, third_y, third_z),
        (4, "TYR", 3.0, tail_y, 0.0),
    ]
    lines = [
        f"ATOM  {idx:5d}  CA  {resname} A{idx:4d}    {x:8.3f}{y:8.3f}{z:8.3f}  1.00 20.00           C"
        for idx, resname, x, y, z in coords
    ]
    lines.append("END")
    return "\n".join(lines) + "\n"


_DSSP_REFERENCE_FRAGMENT = (
    "ATOM    122  N   PRO A   8      24.436  65.567  18.208  1.00 14.61           N\n"
    "ATOM    123  CA  PRO A   8      23.672  66.249  19.267  1.00 19.75           C\n"
    "ATOM    124  C   PRO A   8      24.408  67.421  19.901  1.00 18.44           C\n"
    "ATOM    125  O   PRO A   8      25.615  67.350  20.201  1.00 17.32           O\n"
    "ATOM    129  N   ARG A   9      23.653  68.494  20.092  1.00 16.55           N\n"
    "ATOM    130  CA  ARG A   9      24.204  69.699  20.746  1.00 18.68           C\n"
    "ATOM    131  C   ARG A   9      23.296  69.977  21.968  1.00 16.26           C\n"
    "ATOM    132  O   ARG A   9      22.081  69.742  21.913  1.00 16.03           O\n"
    "ATOM    140  N   ASP A  10      23.888  70.487  23.047  1.00 12.55           N\n"
    "ATOM    141  CA  ASP A  10      23.093  70.768  24.233  1.00 14.08           C\n"
    "ATOM    142  C   ASP A  10      22.656  72.240  24.108  1.00 17.64           C\n"
    "ATOM    143  O   ASP A  10      23.494  73.119  24.217  1.00 14.77           O\n"
    "ATOM    148  N   TYR A  11      21.355  72.489  23.922  1.00 14.49           N\n"
    "ATOM    149  CA  TYR A  11      20.872  73.850  23.751  1.00 16.25           C\n"
    "ATOM    150  C   TYR A  11      20.350  74.358  25.081  1.00 16.15           C\n"
    "ATOM    151  O   TYR A  11      19.838  75.472  25.163  1.00 16.44           O\n"
    "ATOM    160  N   ASN A  12      20.443  73.544  26.128  1.00 16.20           N\n"
    "ATOM    161  CA  ASN A  12      19.912  73.987  27.424  1.00 14.90           C\n"
    "ATOM    162  C   ASN A  12      20.613  75.215  27.988  1.00 12.68           C\n"
    "ATOM    163  O   ASN A  12      19.962  76.005  28.663  1.00 21.94           O\n"
    "ATOM    168  N   PRO A  13      21.931  75.398  27.702  1.00 14.07           N\n"
    "ATOM    169  CA  PRO A  13      22.578  76.591  28.248  1.00 14.31           C\n"
    "ATOM    170  C   PRO A  13      21.955  77.846  27.628  1.00 19.90           C\n"
    "ATOM    171  O   PRO A  13      21.917  78.904  28.242  1.00 19.37           O\n"
    "ATOM    175  N   ILE A  14      21.510  77.742  26.388  1.00 10.68           N\n"
    "ATOM    176  CA  ILE A  14      20.834  78.887  25.774  1.00 16.74           C\n"
    "ATOM    177  C   ILE A  14      19.397  78.981  26.276  1.00 17.98           C\n"
    "ATOM    178  O   ILE A  14      18.923  80.051  26.733  1.00 11.21           O\n"
    "ATOM    183  N   SER A  15      18.649  77.873  26.247  1.00 14.52           N\n"
    "ATOM    184  CA  SER A  15      17.239  77.988  26.654  1.00 13.39           C\n"
    "ATOM    185  C   SER A  15      17.089  78.363  28.102  1.00 19.62           C\n"
    "ATOM    186  O   SER A  15      16.096  78.992  28.450  1.00 13.30           O\n"
    "ATOM    189  N   SER A  16      18.081  78.033  28.932  1.00 14.98           N\n"
    "ATOM    190  CA  SER A  16      17.968  78.363  30.356  1.00 15.64           C\n"
    "ATOM    191  C   SER A  16      18.093  79.873  30.582  1.00 15.36           C\n"
    "ATOM    192  O   SER A  16      17.760  80.385  31.663  1.00 15.60           O\n"
    "ATOM    195  N   THR A  17      18.524  80.598  29.559  1.00 11.88           N\n"
    "ATOM    196  CA  THR A  17      18.647  82.072  29.708  1.00 11.24           C\n"
    "ATOM    197  C   THR A  17      17.473  82.815  29.083  1.00 20.21           C\n"
    "ATOM    198  O   THR A  17      17.388  84.055  29.193  1.00 13.86           O\n"
    "END\n"
)


def _shift_residue_backbone(
    pdb_text: str, offsets: dict[int, tuple[float, float, float]]
) -> str:
    out: list[str] = []
    for raw in pdb_text.splitlines():
        if not raw.startswith("ATOM"):
            out.append(raw)
            continue
        resseq = int(raw[22:26].strip())
        delta = offsets.get(resseq)
        if delta is None:
            out.append(raw)
            continue
        x = float(raw[30:38]) + float(delta[0])
        y = float(raw[38:46]) + float(delta[1])
        z = float(raw[46:54]) + float(delta[2])
        out.append(f"{raw[:30]}{x:8.3f}{y:8.3f}{z:8.3f}{raw[54:]}")
    return "\n".join(out) + "\n"


class TestPipelineDryRun(unittest.TestCase):
    def test_filter_backbones_by_target_rmsd_uses_dssp_mask_by_default(self) -> None:
        shifted = _shift_residue_backbone(
            _DSSP_REFERENCE_FRAGMENT,
            {
                8: (11.0, 0.0, 0.0),
                9: (0.0, 11.0, 0.0),
                10: (0.0, 0.0, 11.0),
                17: (-11.0, 7.0, 0.0),
            },
        )
        backbones = [{"id": "candidate_1", "pdb_text": shifted}]
        accepted, summary = _filter_backbones_by_target_rmsd(
            backbones,
            reference_pdb_text=_DSSP_REFERENCE_FRAGMENT,
            chains=["A"],
            cutoff=2.0,
            source="rfd3",
        )
        self.assertEqual(len(accepted or []), 1)
        self.assertTrue(isinstance(summary, dict) and summary.get("mask_applied"))
        self.assertEqual(summary.get("mask_mode"), "dssp_non_loop_reference")

    def test_filter_backbones_by_target_rmsd_remaps_single_chain_candidate(self) -> None:
        reference = (
            _pdb_atom(1, "CA", "ALA", "B", 1, 0.0, 0.0, 0.0, "C")
            + _pdb_atom(2, "CA", "GLY", "B", 2, 1.0, 1.0, 0.0, "C")
            + _pdb_atom(3, "CA", "SER", "B", 3, 2.0, 0.0, 0.5, "C")
            + "END\n"
        )
        candidate = (
            reference.replace(" B   1", " A   1")
            .replace(" B   2", " A   2")
            .replace(" B   3", " A   3")
        )

        accepted, summary = _filter_backbones_by_target_rmsd(
            [{"id": "bioemu_001", "pdb_text": candidate}],
            reference_pdb_text=reference,
            chains=["B"],
            cutoff=0.1,
            source="bioemu",
            use_dssp_non_loop=False,
        )

        self.assertEqual(len(accepted or []), 1)
        self.assertAlmostEqual(
            float((accepted or [])[0]["target_rmsd"]),
            0.0,
            places=6,
        )
        self.assertEqual(
            (summary or {}).get("chain_remap_by_id"),
            {"bioemu_001": {"from": "A", "to": "B", "reason": "single_chain_candidate"}},
        )

    def test_effective_rfd3_mode_prefers_local_diversify_for_direct_input_pdb(
        self,
    ) -> None:
        req = PipelineRequest(
            target_fasta="",
            target_pdb="",
            dry_run=True,
        )
        self.assertEqual(
            _effective_rfd3_mode(req, input_files={"input.pdb": "/tmp/input.pdb"}),
            "local_diversify",
        )
        req_with_contig = PipelineRequest(
            target_fasta="",
            target_pdb="",
            dry_run=True,
            rfd3_contig="A1-2",
        )
        self.assertEqual(
            _effective_rfd3_mode(
                req_with_contig, input_files={"input.pdb": "/tmp/input.pdb"}
            ),
            "local_diversify",
        )
        req_with_length = PipelineRequest(
            target_fasta="",
            target_pdb="",
            dry_run=True,
            rfd3_length="20-40",
        )
        self.assertEqual(
            _effective_rfd3_mode(
                req_with_length, input_files={"input.pdb": "/tmp/input.pdb"}
            ),
            "enzyme",
        )

    def test_pipeline_rfd3_use_true_defaults_to_target_pdb_input(
        self,
    ) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=None,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                rfd3=None,
            )
            req = PipelineRequest(
                target_fasta="",
                target_pdb=pdb,
                dry_run=True,
                rfd3_use=True,
                rfd3_max_return_designs=3,
                num_seq_per_tier=1,
                conservation_tiers=[0.3],
                stop_after="rfd3",
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            inputs = json.loads(
                (out / "rfd3" / "inputs.json").read_text(encoding="utf-8")
            )
            spec = inputs.get("spec-1") or {}
            self.assertEqual(spec.get("input"), "input.pdb")
            designs = json.loads(
                (out / "rfd3" / "designs.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(designs), 3)

    def test_pipeline_rfd3_target_pdb_backbones_propagate_to_design_counts(
        self,
    ) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )

        class _MMseqsStub:
            def search(self, query_fasta, **kwargs):  # type: ignore[no-untyped-def]
                _ = query_fasta
                _ = kwargs
                a3m = ">query\nAG\n>hit1\nAG\n"
                a3m_b64 = base64.b64encode(gzip.compress(a3m.encode("utf-8"))).decode(
                    "ascii"
                )
                return {"tsv": "", "a3m_gz_b64": a3m_b64}

        class _RFD3Stub:
            def design(self, **kwargs):  # type: ignore[no-untyped-def]
                requested = int(kwargs.get("max_return_designs") or 0)
                on_job_id = kwargs.get("on_job_id")
                if callable(on_job_id):
                    on_job_id("rfd3_target_pdb_default_job")
                designs = [
                    {
                        "id": f"design_{idx}",
                        "pdb": _simple_ca_backbone(float(idx) * 5.0),
                        "score": None,
                        "source": "rfd3",
                    }
                    for idx in range(requested)
                ]
                return {"selected": designs[0], "designs": designs}

        class _ProteinMPNNStub:
            def __init__(self) -> None:
                self.calls: list[int] = []

            def design(self, **kwargs):  # type: ignore[no-untyped-def]
                count = int(kwargs.get("num_seq_per_target") or 0)
                self.calls.append(count)
                native = SequenceRecord(id="native", header="native", sequence="AG")
                samples = [
                    SequenceRecord(id=f"s{idx}", header=f"s{idx}", sequence="AG")
                    for idx in range(count)
                ]
                return native, samples, {}

        with _tmpdir() as tmp:
            proteinmpnn = _ProteinMPNNStub()
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=_MMseqsStub(),
                proteinmpnn=proteinmpnn,
                soluprot=None,
                af2=None,
                rfd3=_RFD3Stub(),
            )
            req = PipelineRequest(
                target_fasta=">q1\nAG\n",
                target_pdb=pdb,
                dry_run=False,
                rfd3_use=True,
                rfd3_max_return_designs=3,
                num_seq_per_tier=2,
                conservation_tiers=[0.3],
                stop_after="design",
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            designs_fasta = (out / "tiers" / "30" / "designs.fasta").read_text(
                encoding="utf-8"
            )
            design_headers = [
                line for line in designs_fasta.splitlines() if line.startswith(">")
            ]
            self.assertEqual(len(design_headers), 6)
            self.assertEqual(proteinmpnn.calls, [2, 2, 2])
            backbones = json.loads(
                (out / "tiers" / "30" / "proteinmpnn_backbones.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(len(backbones.get("backbones") or []), 3)

    def test_backbone_source_summary_marks_selected_only_when_observed_exceeds_used(
        self,
    ) -> None:
        req = PipelineRequest(
            target_fasta=">q1\nACDEFGHIK\n",
            target_pdb="",
            dry_run=True,
            rfd3_contig="A1-2",
            rfd3_input_pdb="ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\nEND\n",
            rfd3_max_return_designs=10,
        )
        summaries, mode = _build_backbone_source_summaries(
            req,
            backbone_entries=[
                {
                    "id": "rfd3_model_1",
                    "source": "rfd3",
                    "materialized": True,
                    "propagated": True,
                }
            ],
            observed_counts={"rfd3": 10},
            selected_ids={"rfd3": "rfd3_model_1"},
        )
        rfd3 = summaries.get("rfd3") if isinstance(summaries.get("rfd3"), dict) else {}
        self.assertEqual(rfd3.get("requested_count"), 10)
        self.assertEqual(rfd3.get("observed_count"), 10)
        self.assertEqual(rfd3.get("materialized_count"), 1)
        self.assertEqual(rfd3.get("propagated_count"), 1)
        self.assertEqual(rfd3.get("propagation_mode"), "selected_only")
        self.assertEqual(rfd3.get("selected_backbone_id"), "rfd3_model_1")
        self.assertIn("metadata-only", str(rfd3.get("note") or ""))
        self.assertEqual(mode, "selected_only")

    def test_backbone_source_summary_notes_bioemu_topology_only_materialization(
        self,
    ) -> None:
        req = PipelineRequest(
            target_fasta=">q1\nACDEFGHIK\n",
            target_pdb="",
            dry_run=True,
            bioemu_use=True,
            bioemu_num_samples=10,
            bioemu_max_return_structures=10,
        )
        summaries, mode = _build_backbone_source_summaries(
            req,
            backbone_entries=[
                {
                    "id": "bioemu_topology",
                    "source": "bioemu",
                    "materialized": True,
                    "propagated": True,
                }
            ],
            observed_counts={"bioemu": 1},
        )
        bioemu = (
            summaries.get("bioemu") if isinstance(summaries.get("bioemu"), dict) else {}
        )
        self.assertEqual(bioemu.get("requested_count"), 10)
        self.assertEqual(bioemu.get("observed_count"), 1)
        self.assertEqual(bioemu.get("materialized_count"), 1)
        self.assertEqual(bioemu.get("propagated_count"), 1)
        self.assertEqual(bioemu.get("propagation_mode"), "all_materialized")
        self.assertIn("topology_pdb", str(bioemu.get("note") or ""))
        self.assertEqual(mode, "all_materialized")

    def test_bioemu_zero_resseq_is_renumbered_not_dropped(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   0       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   1       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )

        strip_nonpositive, renumber_from_1, detail = (
            _resolve_backbone_preprocess_options(
                pdb_text=pdb,
                source="bioemu",
                strip_nonpositive_resseq=True,
                renumber_resseq_from_1=False,
            )
        )
        self.assertFalse(strip_nonpositive)
        self.assertTrue(renumber_from_1)
        self.assertEqual(detail, "bioemu_zero_resseq_renumbered_from_1")

        processed = _preprocess_pdb_text(
            pdb,
            chains=["A"],
            strip_nonpositive_resseq=strip_nonpositive,
            renumber_resseq_from_1=renumber_from_1,
        )
        self.assertEqual(sequence_by_chain(processed, chains=["A"]).get("A"), "AG")
        residues = residues_by_chain(processed, only_atom_records=True).get("A") or []
        self.assertEqual([res.resseq for res in residues], [1, 2])

    def test_pipeline_runs_and_writes_artifacts(self) -> None:
        fasta = ">q1\nACDEFGHIK\n"
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  CYS A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  ASP A   3       2.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      4  CA  GLU A   4       3.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      5  CA  PHE A   5       4.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      6  CA  GLY A   6       5.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      7  CA  HIS A   7       6.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      8  CA  ILE A   8       7.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      9  CA  LYS A   9       8.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )

        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None
            )
            req = PipelineRequest(
                target_fasta=fasta,
                target_pdb=pdb,
                dry_run=True,
                num_seq_per_tier=2,
                conservation_tiers=[0.3, 0.5],
            )
            res = runner.run(req)

            out = Path(res.output_dir)
            self.assertTrue((out / "request.json").exists())
            self.assertTrue((out / "status.json").exists())
            self.assertTrue((out / "events.jsonl").exists())
            self.assertTrue((out / "orchestration_trace.jsonl").exists())
            self.assertTrue((out / "msa" / "result.a3m").exists())
            self.assertTrue((out / "msa" / "quality.json").exists())
            self.assertTrue((out / "conservation.json").exists())
            self.assertTrue((out / "ligand_mask.json").exists())
            self.assertTrue((out / "query_pdb_alignment.json").exists())
            self.assertTrue((out / "agent_panel.jsonl").exists())
            self.assertTrue((out / "agent_panel_report.md").exists())
            trace_events = [
                json.loads(line)
                for line in (out / "orchestration_trace.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            self.assertTrue(
                any(
                    item.get("event_type") == "stage_status"
                    and item.get("plane") == "control"
                    for item in trace_events
                )
            )
            self.assertTrue(
                any(
                    item.get("event_type") == "agent_verdict"
                    and item.get("plane") == "evidence"
                    for item in trace_events
                )
            )

            self.assertEqual(len(res.tiers), 2)
            for tier_result in res.tiers:
                tier_dir = out / "tiers" / str(int(round(tier_result.tier * 100.0)))
                self.assertTrue((tier_dir / "fixed_positions.json").exists())
                self.assertTrue((tier_dir / "proteinmpnn.json").exists())
                self.assertTrue((tier_dir / "designs.fasta").exists())
                self.assertTrue((tier_dir / "fixed_positions_check.json").exists())
                self.assertTrue((tier_dir / "mutation_report.json").exists())
                self.assertTrue((tier_dir / "mutations_by_position.tsv").exists())
                self.assertTrue((tier_dir / "mutations_by_position.svg").exists())
                self.assertTrue((tier_dir / "mutations_by_sequence.tsv").exists())
                self.assertTrue((tier_dir / "soluprot.json").exists())
                self.assertTrue((tier_dir / "designs_filtered.fasta").exists())
                self.assertTrue((tier_dir / "af2_scores.json").exists())
                self.assertTrue((tier_dir / "af2_selected.fasta").exists())

    def test_pipeline_dry_run_writes_relax_artifacts_when_enabled(self) -> None:
        fasta = ">q1\nACDEFGHIK\n"
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  CYS A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  ASP A   3       2.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      4  CA  GLU A   4       3.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      5  CA  PHE A   5       4.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      6  CA  GLY A   6       5.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      7  CA  HIS A   7       6.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      8  CA  ILE A   8       7.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      9  CA  LYS A   9       8.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )

        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None
            )
            req = PipelineRequest(
                target_fasta=fasta,
                target_pdb=pdb,
                dry_run=True,
                stop_after="af2",
                num_seq_per_tier=2,
                conservation_tiers=[0.3],
                relax_enabled=True,
                relax_score_per_residue_cutoff=-3.0,
                af2_plddt_cutoff=0.0,
                af2_rmsd_cutoff=0.0,
                rfd3_use=False,
            )
            res = runner.run(req)

            tier_dir = Path(res.output_dir) / "tiers" / "30"
            relax_scores = json.loads(
                (tier_dir / "relax_scores.json").read_text(encoding="utf-8")
            )
            relax_metrics = json.loads(
                (tier_dir / "relax" / "target_30_s1" / "metrics.json").read_text(
                    encoding="utf-8"
                )
            )

            self.assertTrue((tier_dir / "relax_selected.fasta").exists())
            self.assertEqual(relax_scores.get("selected_ids"), ["target:30_s1"])
            self.assertEqual(relax_scores.get("mode"), "dry_run")
            self.assertEqual(res.tiers[0].relax_selected_ids, ["target:30_s1"])
            self.assertAlmostEqual(
                float(relax_metrics.get("score_per_residue") or 0.0), -3.5
            )
            self.assertAlmostEqual(
                float(relax_metrics.get("total_score") or 0.0), -31.5
            )
            self.assertAlmostEqual(
                float(relax_metrics.get("delta_total_score") or 0.0), -25.0
            )

    def test_pipeline_dry_run_scores_relax_for_all_af2_candidates_even_when_none_pass_af2_cutoffs(
        self,
    ) -> None:
        fasta = ">q1\nACDEFGHIK\n"
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  CYS A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  ASP A   3       2.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      4  CA  GLU A   4       3.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      5  CA  PHE A   5       4.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      6  CA  GLY A   6       5.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      7  CA  HIS A   7       6.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      8  CA  ILE A   8       7.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      9  CA  LYS A   9       8.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )

        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None
            )
            req = PipelineRequest(
                target_fasta=fasta,
                target_pdb=pdb,
                dry_run=True,
                stop_after="af2",
                num_seq_per_tier=2,
                conservation_tiers=[0.3],
                relax_enabled=True,
                relax_score_per_residue_cutoff=None,
                af2_plddt_cutoff=101.0,
                af2_rmsd_cutoff=2.0,
            )
            res = runner.run(req)

            tier_dir = Path(res.output_dir) / "tiers" / "30"
            relax_scores = json.loads(
                (tier_dir / "relax_scores.json").read_text(encoding="utf-8")
            )
            candidate_ids = list(res.tiers[0].passed_ids or [])

            self.assertEqual(relax_scores.get("candidate_ids"), candidate_ids)
            self.assertEqual(
                set((relax_scores.get("score_per_residue") or {}).keys()),
                set(candidate_ids),
            )
            self.assertEqual(relax_scores.get("selected_ids"), [])
            self.assertEqual(res.tiers[0].relax_selected_ids, [])
            for seq_id in candidate_ids:
                metrics_path = (
                    tier_dir / "relax" / seq_id.replace(":", "_") / "metrics.json"
                )
                self.assertTrue(metrics_path.exists())

    def test_runner_links_project_round_metadata_to_launched_run(self) -> None:
        fasta = ">q1\nACDEFGHIK\n"
        owner = {"username": "hana", "run_prefix": "hana", "role": "user"}
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None
            )
            dispatcher = ToolDispatcher(runner)
            dispatcher.call_tool(
                "pipeline.save_project",
                {
                    "project_id": "tev_campaign",
                    "name": "TEV campaign",
                    "description": "stability round-tracking",
                    "user": owner,
                },
            )
            dispatcher.call_tool(
                "pipeline.save_round",
                {
                    "project_id": "tev_campaign",
                    "round_id": "round_01",
                    "title": "Round 01",
                    "goal": "baseline stability screen",
                    "user": owner,
                },
            )

            req = PipelineRequest(
                target_fasta=fasta,
                target_pdb="",
                dry_run=True,
                num_seq_per_tier=1,
                conservation_tiers=[0.3],
                project_id="tev_campaign",
                round_id="round_01",
            )
            res = runner.run(req)

            out = Path(res.output_dir)
            request_payload = json.loads(
                (out / "request.json").read_text(encoding="utf-8")
            )
            self.assertEqual(request_payload.get("project_id"), "tev_campaign")
            self.assertEqual(request_payload.get("round_id"), "round_01")

            round_path = (
                Path(tmp)
                / "_workspace"
                / "projects"
                / "tev_campaign"
                / "rounds"
                / "round_01.json"
            )
            round_record = json.loads(round_path.read_text(encoding="utf-8"))
            self.assertEqual(round_record.get("linked_run_ids"), [res.run_id])

    def test_surface_and_pi_filters(self) -> None:
        fasta = ">q1\nDDDDDDDD\n"
        pdb = (
            "ATOM      1  CA  ASP A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  ASP A   2       2.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  ASP A   3       4.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      4  CA  ASP A   4       6.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      5  CA  ASP A   5       8.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      6  CA  ASP A   6      10.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None
            )
            req = PipelineRequest(
                target_fasta=fasta,
                target_pdb=pdb,
                dry_run=True,
                conservation_tiers=[0.3],
                surface_only=True,
                pi_max=6.0,
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            self.assertTrue((out / "surface_mask.json").exists())
            self.assertTrue((out / "tiers" / "30" / "pi_scores.json").exists())

    def test_pipeline_dry_run_generates_dummy_pdb_when_missing(self) -> None:
        fasta = ">q1\nACDEFGHIK\n"
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None
            )
            req = PipelineRequest(
                target_fasta=fasta,
                target_pdb="",
                dry_run=True,
                num_seq_per_tier=2,
                conservation_tiers=[0.3],
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            self.assertTrue((out / "ligand_mask.json").exists())

    def test_pipeline_dry_run_accepts_pdb_only(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None
            )
            req = PipelineRequest(
                target_fasta="",
                target_pdb=pdb,
                dry_run=True,
                num_seq_per_tier=2,
                conservation_tiers=[0.3],
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            self.assertTrue((out / "target.fasta").exists())
            self.assertTrue((out / "target.pdb").exists())
            self.assertTrue((out / "msa" / "result.a3m").exists())

    def test_pipeline_auto_recover_msa_without_mmseqs(self) -> None:
        fasta = ">q1\nACDEFGHIK\n"
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None
            )
            req = PipelineRequest(
                target_fasta=fasta,
                target_pdb="",
                dry_run=False,
                stop_after="msa",
                auto_recover=True,
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            self.assertTrue((out / "msa" / "result.a3m").exists())
            self.assertTrue((out / "agent_panel.jsonl").exists())

    def test_pipeline_pdb_preprocess_strips_nonpositive_resseq(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A  -1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   1       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None
            )
            req = PipelineRequest(
                target_fasta="",
                target_pdb=pdb,
                dry_run=True,
                num_seq_per_tier=2,
                conservation_tiers=[0.3],
                design_chains=["A"],
                pdb_strip_nonpositive_resseq=True,
                pdb_renumber_resseq_from_1=True,
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            self.assertTrue((out / "target.original.pdb").exists())
            self.assertTrue((out / "pdb_numbering.json").exists())

            target_fasta = (out / "target.fasta").read_text(encoding="utf-8")
            self.assertIn("\nG\n", target_fasta)

            processed_pdb = (out / "target.pdb").read_text(encoding="utf-8")
            self.assertNotIn("  -1", processed_pdb)

    def test_pipeline_rfd3_auto_strips_nonpositive_resseq(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A  -1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   1       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None
            )
            req = PipelineRequest(
                target_fasta="",
                target_pdb="",
                rfd3_input_pdb=pdb,
                rfd3_contig="A1-2",
                dry_run=True,
                num_seq_per_tier=2,
                conservation_tiers=[0.3],
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            input_pdb = (out / "rfd3" / "input_files" / "input.pdb").read_text(
                encoding="utf-8"
            )
            selected_pdb = (out / "rfd3" / "selected.pdb").read_text(encoding="utf-8")
            self.assertNotIn("  -1", input_pdb)
            self.assertNotIn("  -1", selected_pdb)

    def test_pipeline_dry_run_accepts_conservation_weighting_flag(self) -> None:
        fasta = ">q1\nACDEFGHIK\n"
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  CYS A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  ASP A   3       2.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      4  CA  GLU A   4       3.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      5  CA  PHE A   5       4.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      6  CA  GLY A   6       5.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      7  CA  HIS A   7       6.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      8  CA  ILE A   8       7.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      9  CA  LYS A   9       8.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None
            )
            req = PipelineRequest(
                target_fasta=fasta,
                target_pdb=pdb,
                dry_run=True,
                conservation_weighting="mmseqs_cluster",
                num_seq_per_tier=2,
                conservation_tiers=[0.3],
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            self.assertTrue((out / "conservation.json").exists())

    def test_pipeline_dry_run_rfd3_writes_selected_pdb(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=None,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                rfd3=None,
            )
            req = PipelineRequest(
                target_fasta="",
                target_pdb="",
                dry_run=True,
                rfd3_contig="A1-2",
                rfd3_input_pdb=pdb,
                num_seq_per_tier=1,
                conservation_tiers=[0.3],
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            self.assertTrue((out / "rfd3" / "selected.pdb").exists())
            self.assertTrue((out / "target.pdb").exists())

    def test_pipeline_rfd3_simple_inputs_written(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=None,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                rfd3=None,
            )
            req = PipelineRequest(
                target_fasta="",
                target_pdb="",
                dry_run=True,
                rfd3_contig="A1-2",
                rfd3_ligand="LIG",
                rfd3_select_unfixed_sequence="A1-2",
                rfd3_input_pdb=pdb,
                num_seq_per_tier=1,
                conservation_tiers=[0.3],
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            inputs = json.loads(
                (out / "rfd3" / "inputs.json").read_text(encoding="utf-8")
            )
            spec = inputs.get("spec-1") or {}
            self.assertEqual(spec.get("input"), "input.pdb")
            self.assertEqual(spec.get("contig"), "A2-2")
            self.assertEqual(spec.get("unindex"), "A1")
            self.assertEqual(spec.get("select_fixed_atoms"), {"A1": "ALL"})
            self.assertEqual(spec.get("ligand"), "LIG")
            self.assertEqual(spec.get("select_unfixed_sequence"), "A1-2")

    def test_pipeline_rfd3_legacy_contig_injects_request_default_partial_t(
        self,
    ) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=None,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                rfd3=None,
            )
            req = PipelineRequest(
                target_fasta="",
                target_pdb="",
                dry_run=True,
                rfd3_contig="A1-2",
                rfd3_input_pdb=pdb,
                num_seq_per_tier=1,
                conservation_tiers=[0.3],
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            inputs = json.loads(
                (out / "rfd3" / "inputs.json").read_text(encoding="utf-8")
            )
            spec = inputs.get("spec-1") or {}
            self.assertEqual(spec.get("partial_t"), 5.0)
            self.assertNotIn("partial_T", spec)

    def test_pipeline_rfd3_binder_leaves_fixed_atoms_unset_by_default(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=None,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                rfd3=None,
            )
            req = PipelineRequest(
                target_fasta="",
                target_pdb="",
                dry_run=True,
                rfd3_mode="binder",
                rfd3_contig="A1-2",
                rfd3_input_pdb=pdb,
                rfd3_partial_t=5.0,
                num_seq_per_tier=1,
                conservation_tiers=[0.3],
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            inputs = json.loads(
                (out / "rfd3" / "inputs.json").read_text(encoding="utf-8")
            )
            spec = inputs.get("spec-1") or {}
            self.assertEqual(spec.get("input"), "input.pdb")
            self.assertEqual(spec.get("contig"), "A1-2")
            self.assertEqual(spec.get("partial_t"), 5.0)
            self.assertNotIn("partial_T", spec)
            self.assertNotIn("select_fixed_atoms", spec)

    def test_pipeline_rfd3_local_diversify_partial_t_request_default_injected(
        self,
    ) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=None,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                rfd3=None,
            )
            req = PipelineRequest(
                target_fasta="",
                target_pdb="",
                dry_run=True,
                rfd3_mode="local_diversify",
                rfd3_input_pdb=pdb,
                num_seq_per_tier=1,
                conservation_tiers=[0.3],
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            inputs = json.loads(
                (out / "rfd3" / "inputs.json").read_text(encoding="utf-8")
            )
            spec = inputs.get("spec-1") or {}
            self.assertEqual(spec.get("input"), "input.pdb")
            self.assertEqual(spec.get("partial_t"), 5.0)
            self.assertNotIn("partial_T", spec)
            self.assertIn("select_fixed_atoms", spec)
            self.assertEqual(spec["select_fixed_atoms"], {"A1": "ALL"})
            self.assertEqual(spec["unindex"], "A1")

    def test_pipeline_rfd3_auto_mode_with_contig_shifts_first_residue_into_unindex(
        self,
    ) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  SER A   3       2.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=None,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                rfd3=None,
            )
            req = PipelineRequest(
                target_fasta="",
                target_pdb="",
                dry_run=True,
                rfd3_contig="A1-3",
                rfd3_input_pdb=pdb,
                num_seq_per_tier=1,
                conservation_tiers=[0.3],
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            inputs = json.loads(
                (out / "rfd3" / "inputs.json").read_text(encoding="utf-8")
            )
            spec = inputs.get("spec-1") or {}
            self.assertEqual(spec.get("input"), "input.pdb")
            self.assertEqual(spec.get("contig"), "A2-3")
            self.assertEqual(spec.get("unindex"), "A1")
            self.assertEqual(spec.get("select_fixed_atoms"), {"A1": "ALL"})

    def test_pipeline_rfd3_explicit_local_diversify_still_shifts_first_residue_into_unindex(
        self,
    ) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  SER A   3       2.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=None,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                rfd3=None,
            )
            req = PipelineRequest(
                target_fasta="",
                target_pdb="",
                dry_run=True,
                rfd3_mode="local_diversify",
                rfd3_contig="A1-3",
                rfd3_input_pdb=pdb,
                num_seq_per_tier=1,
                conservation_tiers=[0.3],
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            inputs = json.loads(
                (out / "rfd3" / "inputs.json").read_text(encoding="utf-8")
            )
            spec = inputs.get("spec-1") or {}
            self.assertEqual(spec.get("input"), "input.pdb")
            self.assertEqual(spec.get("contig"), "A2-3")
            self.assertEqual(spec.get("unindex"), "A1")
            self.assertEqual(spec.get("select_fixed_atoms"), {"A1": "ALL"})

    def test_pipeline_rfd3_local_diversify_passthroughs_unindex_and_fixed_atoms(
        self,
    ) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  SER A   3       2.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=None,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                rfd3=None,
            )
            req = PipelineRequest(
                target_fasta="",
                target_pdb="",
                dry_run=True,
                rfd3_mode="local_diversify",
                rfd3_input_pdb=pdb,
                rfd3_partial_t=5.0,
                rfd3_unindex="A2",
                rfd3_select_fixed_atoms={"A2": "ALL"},
                num_seq_per_tier=1,
                conservation_tiers=[0.3],
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            inputs = json.loads(
                (out / "rfd3" / "inputs.json").read_text(encoding="utf-8")
            )
            spec = inputs.get("spec-1") or {}
            self.assertEqual(spec.get("input"), "input.pdb")
            self.assertEqual(spec.get("partial_t"), 5.0)
            self.assertEqual(spec.get("unindex"), "A2")
            self.assertEqual(spec.get("select_fixed_atoms"), {"A2": "ALL"})
            self.assertNotIn("contig", spec)
            self.assertNotIn("partial_T", spec)

    def test_pipeline_rfd3_local_diversify_default_unindex_backfills_missing_contig(
        self,
    ) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  SER A   3       2.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=None,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                rfd3=None,
            )
            req = PipelineRequest(
                target_fasta="",
                target_pdb="",
                dry_run=True,
                rfd3_mode="local_diversify",
                rfd3_input_pdb=pdb,
                rfd3_unindex="A1",
                rfd3_select_fixed_atoms={"A1": "ALL"},
                num_seq_per_tier=1,
                conservation_tiers=[0.3],
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            inputs = json.loads(
                (out / "rfd3" / "inputs.json").read_text(encoding="utf-8")
            )
            spec = inputs.get("spec-1") or {}
            self.assertEqual(spec.get("contig"), "A2-3")
            self.assertEqual(spec.get("unindex"), "A1")
            self.assertEqual(spec.get("select_fixed_atoms"), {"A1": "ALL"})

    def test_pipeline_rfd3_explicit_local_diversify_contig_only_infers_fixed_residue(
        self,
    ) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  SER A   3       2.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=None,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                rfd3=None,
            )
            req = PipelineRequest(
                target_fasta="",
                target_pdb="",
                dry_run=True,
                rfd3_mode="local_diversify",
                rfd3_input_pdb=pdb,
                rfd3_contig="A1-3",
                num_seq_per_tier=1,
                conservation_tiers=[0.3],
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            inputs = json.loads(
                (out / "rfd3" / "inputs.json").read_text(encoding="utf-8")
            )
            spec = inputs.get("spec-1") or {}
            self.assertEqual(spec.get("contig"), "A2-3")
            self.assertEqual(spec.get("unindex"), "A1")
            self.assertEqual(spec.get("select_fixed_atoms"), {"A1": "ALL"})

    def test_pipeline_rfd3_local_diversify_shifts_explicit_overlap_with_unindex(
        self,
    ) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  SER A   3       2.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=None,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                rfd3=None,
            )
            req = PipelineRequest(
                target_fasta="",
                target_pdb="",
                dry_run=True,
                rfd3_mode="local_diversify",
                rfd3_input_pdb=pdb,
                rfd3_contig="A1-3",
                rfd3_unindex="A1",
                rfd3_select_fixed_atoms={"A1": "ALL"},
                num_seq_per_tier=1,
                conservation_tiers=[0.3],
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            inputs = json.loads(
                (out / "rfd3" / "inputs.json").read_text(encoding="utf-8")
            )
            spec = inputs.get("spec-1") or {}
            self.assertEqual(spec.get("contig"), "A2-3")
            self.assertEqual(spec.get("unindex"), "A1")
            self.assertEqual(spec.get("select_fixed_atoms"), {"A1": "ALL"})

    def test_pipeline_rfd3_local_diversify_clamps_stale_contig_to_current_input_range_before_inference(
        self,
    ) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  SER A   3       2.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=None,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                rfd3=None,
            )
            req = PipelineRequest(
                target_fasta="",
                target_pdb="",
                dry_run=True,
                rfd3_mode="local_diversify",
                rfd3_input_pdb=pdb,
                rfd3_contig="A1-9",
                num_seq_per_tier=1,
                conservation_tiers=[0.3],
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            inputs = json.loads(
                (out / "rfd3" / "inputs.json").read_text(encoding="utf-8")
            )
            spec = inputs.get("spec-1") or {}
            self.assertEqual(spec.get("contig"), "A2-3")
            self.assertEqual(spec.get("unindex"), "A1")
            self.assertEqual(spec.get("select_fixed_atoms"), {"A1": "ALL"})

    def test_pipeline_rfd3_auto_local_diversify_clamps_stale_contig_before_shift(
        self,
    ) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  SER A   3       2.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=None,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                rfd3=None,
            )
            req = PipelineRequest(
                target_fasta="",
                target_pdb="",
                dry_run=True,
                rfd3_input_pdb=pdb,
                rfd3_contig="A1-9",
                num_seq_per_tier=1,
                conservation_tiers=[0.3],
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            inputs = json.loads(
                (out / "rfd3" / "inputs.json").read_text(encoding="utf-8")
            )
            spec = inputs.get("spec-1") or {}
            self.assertEqual(spec.get("contig"), "A2-3")
            self.assertEqual(spec.get("unindex"), "A1")
            self.assertEqual(spec.get("select_fixed_atoms"), {"A1": "ALL"})

    def test_pipeline_rfd3_input_only_spec_leaves_fixed_atoms_unset_with_request_default_partial_t(
        self,
    ) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=None,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                rfd3=None,
            )
            req = PipelineRequest(
                target_fasta="",
                target_pdb="",
                dry_run=True,
                rfd3_inputs={
                    "spec-1": {
                        "input": "input.pdb",
                    }
                },
                rfd3_input_pdb=pdb,
                num_seq_per_tier=1,
                conservation_tiers=[0.3],
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            inputs = json.loads(
                (out / "rfd3" / "inputs.json").read_text(encoding="utf-8")
            )
            spec = inputs.get("spec-1") or {}
            self.assertEqual(spec.get("input"), "input.pdb")
            self.assertEqual(spec.get("partial_t"), 5.0)
            self.assertNotIn("partial_T", spec)
            self.assertNotIn("select_fixed_atoms", spec)

    def test_pipeline_rfd3_partial_t_respects_override(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=None,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                rfd3=None,
            )
            req = PipelineRequest(
                target_fasta="",
                target_pdb="",
                dry_run=True,
                rfd3_inputs={
                    "spec-1": {
                        "input": "input.pdb",
                        "contig": "A1-2",
                        "partial_t": 5,
                        "select_fixed_atoms": {"A2": "ALL"},
                    }
                },
                rfd3_input_pdb=pdb,
                num_seq_per_tier=1,
                conservation_tiers=[0.3],
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            inputs = json.loads(
                (out / "rfd3" / "inputs.json").read_text(encoding="utf-8")
            )
            spec = inputs.get("spec-1") or {}
            self.assertEqual(spec.get("partial_t"), 5)
            self.assertNotIn("partial_T", spec)
            self.assertEqual(spec.get("select_fixed_atoms"), {"A2": "ALL"})

    def test_pipeline_rfd3_normalizes_partial_T_to_partial_t(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=None,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                rfd3=None,
            )
            req = PipelineRequest(
                target_fasta="",
                target_pdb="",
                dry_run=True,
                rfd3_inputs={
                    "spec-1": {
                        "input": "input.pdb",
                        "contig": "A1-2",
                        "partial_T": 7,
                    }
                },
                rfd3_input_pdb=pdb,
                num_seq_per_tier=1,
                conservation_tiers=[0.3],
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            inputs = json.loads(
                (out / "rfd3" / "inputs.json").read_text(encoding="utf-8")
            )
            spec = inputs.get("spec-1") or {}
            self.assertEqual(spec.get("partial_t"), 7)
            self.assertNotIn("partial_T", spec)

    def test_pipeline_rfd3_duplicate_backbone_summary_written(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=None,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                rfd3=None,
            )
            req = PipelineRequest(
                target_fasta=">q1\nAG\n",
                target_pdb=pdb,
                dry_run=True,
                rfd3_mode="local_diversify",
                rfd3_input_pdb=pdb,
                rfd3_use_ensemble=True,
                rfd3_max_return_designs=4,
                num_seq_per_tier=1,
                conservation_tiers=[0.3],
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            diversity = json.loads(
                (out / "rfd3" / "diversity_summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(diversity.get("input_count"), 4)
            self.assertEqual(diversity.get("unique_count"), 1)
            self.assertEqual(diversity.get("duplicate_count"), 3)

            backbones = json.loads((out / "backbones.json").read_text(encoding="utf-8"))
            source = (backbones.get("sources") or {}).get("rfd3") or {}
            self.assertEqual(source.get("unique_count"), 1)
            self.assertEqual(source.get("duplicate_count"), 3)
            self.assertTrue(bool(source.get("deduplicated")))

    def test_pipeline_rfd3_auto_retry_persists_raw_designs_and_recovers_unique_backbones(
        self,
    ) -> None:
        pdb = _simple_ca_backbone(0.0)

        class _MMseqsStub:
            def search(self, query_fasta, **kwargs):  # type: ignore[no-untyped-def]
                _ = kwargs
                _ = query_fasta
                a3m = ">query\nAGS\n>hit1\nAGS\n"
                a3m_b64 = base64.b64encode(gzip.compress(a3m.encode("utf-8"))).decode(
                    "ascii"
                )
                return {"tsv": "", "a3m_gz_b64": a3m_b64}

        class _RFD3Stub:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def design(self, **kwargs):  # type: ignore[no-untyped-def]
                self.calls.append(dict(kwargs))
                on_job_id = kwargs.get("on_job_id")
                if callable(on_job_id):
                    on_job_id(f"rfd3_retry_job_{len(self.calls)}")
                if int(kwargs.get("max_return_designs") or 0) > 1:
                    duplicate = _simple_ca_backbone(0.0)
                    return {
                        "selected": {
                            "id": "inputs_spec-1_0_model_0",
                            "pdb": duplicate,
                            "cif_gz_name": "inputs_spec-1_0_model_0.cif.gz",
                            "json_name": "inputs_spec-1_0_model_0.json",
                        },
                        "designs": [
                            {
                                "id": f"inputs_spec-1_0_model_{idx}",
                                "pdb": duplicate,
                                "cif_gz_name": f"inputs_spec-1_0_model_{idx}.cif.gz",
                                "json_name": f"inputs_spec-1_0_model_{idx}.json",
                            }
                            for idx in range(3)
                        ],
                    }
                unique = _simple_ca_backbone(float(len(self.calls)))
                return {
                    "selected": {
                        "id": "inputs_spec-1_0_model_0",
                        "pdb": unique,
                        "cif_gz_name": "inputs_spec-1_0_model_0.cif.gz",
                        "json_name": "inputs_spec-1_0_model_0.json",
                    },
                    "designs": [
                        {
                            "id": "inputs_spec-1_0_model_0",
                            "pdb": unique,
                            "cif_gz_name": "inputs_spec-1_0_model_0.cif.gz",
                            "json_name": "inputs_spec-1_0_model_0.json",
                        }
                    ],
                }

        with _tmpdir() as tmp:
            rfd3 = _RFD3Stub()
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=_MMseqsStub(),
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                rfd3=rfd3,
            )
            req = PipelineRequest(
                target_fasta=">q1\nAGS\n",
                target_pdb=pdb,
                dry_run=False,
                stop_after="rfd3",
                rfd3_mode="local_diversify",
                rfd3_input_pdb=pdb,
                rfd3_max_return_designs=3,
                conservation_tiers=[0.3],
                num_seq_per_tier=1,
            )
            runner.run(req, run_id="rfd3_duplicate_retry")
            out = Path(tmp) / "rfd3_duplicate_retry"
            self.assertEqual(len(rfd3.calls), 3)
            self.assertEqual(int(rfd3.calls[0].get("max_return_designs") or 0), 3)
            self.assertEqual(int(rfd3.calls[1].get("max_return_designs") or 0), 1)
            self.assertEqual(int(rfd3.calls[2].get("max_return_designs") or 0), 1)

            raw_designs = json.loads(
                (out / "rfd3" / "raw_designs.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(raw_designs), 5)
            self.assertEqual(len(list((out / "rfd3" / "raw_designs").glob("*.pdb"))), 5)
            self.assertEqual(len(list((out / "rfd3" / "designs").glob("*.pdb"))), 3)

            diversity = json.loads(
                (out / "rfd3" / "diversity_summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(diversity.get("input_count"), 5)
            self.assertEqual(diversity.get("unique_count"), 3)
            self.assertEqual(diversity.get("duplicate_count"), 2)

            debug = json.loads(
                (out / "rfd3" / "debug_summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual(debug.get("sampling_strategy_requested"), "auto")
            self.assertEqual(debug.get("sampling_strategy_effective"), "auto")
            self.assertTrue(bool(debug.get("independent_retry_performed")))
            self.assertEqual(debug.get("independent_retry_attempt_count"), 2)
            self.assertEqual(debug.get("requested_count"), 3)
            self.assertEqual(debug.get("final_unique_count"), 3)

    def test_pipeline_rfd3_target_rmsd_gate_retries_until_requested_count_is_filled(
        self,
    ) -> None:
        target_pdb = _bent_ca_backbone(0.0)
        off_target_batch = [
            _bent_ca_backbone(15.0),
            _bent_ca_backbone(16.0, third_y=0.5),
            _bent_ca_backbone(17.0, third_z=0.5),
        ]
        accepted_batch = [
            _bent_ca_backbone(0.10),
            _bent_ca_backbone(0.20, third_y=0.05),
            _bent_ca_backbone(0.15, third_z=0.05, tail_y=0.02),
        ]

        class _MMseqsStub:
            def search(self, query_fasta, **kwargs):  # type: ignore[no-untyped-def]
                _ = kwargs
                _ = query_fasta
                a3m = ">query\nAGSY\n>hit1\nAGSY\n"
                a3m_b64 = base64.b64encode(gzip.compress(a3m.encode("utf-8"))).decode(
                    "ascii"
                )
                return {"tsv": "", "a3m_gz_b64": a3m_b64}

        class _RFD3Stub:
            def __init__(self) -> None:
                self.calls: list[int] = []

            def design(self, **kwargs):  # type: ignore[no-untyped-def]
                requested = int(kwargs.get("max_return_designs") or 0)
                self.calls.append(requested)
                on_job_id = kwargs.get("on_job_id")
                if callable(on_job_id):
                    on_job_id(f"rfd3_target_gate_job_{len(self.calls)}")
                batch = off_target_batch if len(self.calls) == 1 else accepted_batch
                return {
                    "selected": {
                        "id": f"inputs_spec-1_0_model_{len(self.calls)}_0",
                        "pdb": batch[0],
                        "json_name": f"inputs_spec-1_0_model_{len(self.calls)}_0.json",
                    },
                    "designs": [
                        {
                            "id": f"inputs_spec-1_0_model_{len(self.calls)}_{idx}",
                            "pdb": pdb_text,
                            "json_name": f"inputs_spec-1_0_model_{len(self.calls)}_{idx}.json",
                        }
                        for idx, pdb_text in enumerate(batch[:requested])
                    ],
                }

        with _tmpdir() as tmp:
            rfd3 = _RFD3Stub()
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=_MMseqsStub(),
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                rfd3=rfd3,
            )
            req = PipelineRequest(
                target_fasta=">q1\nAGSY\n",
                target_pdb=target_pdb,
                dry_run=False,
                stop_after="rfd3",
                rfd3_mode="local_diversify",
                rfd3_input_pdb=target_pdb,
                rfd3_max_return_designs=3,
                rfd3_target_rmsd_cutoff=1.0,
                rfd3_max_attempted_designs=6,
                conservation_tiers=[0.3],
                num_seq_per_tier=1,
            )
            runner.run(req, run_id="rfd3_target_gate_retry")

            out = Path(tmp) / "rfd3_target_gate_retry"
            debug = json.loads(
                (out / "rfd3" / "debug_summary.json").read_text(encoding="utf-8")
            )
            raw_designs = json.loads(
                (out / "rfd3" / "raw_designs.json").read_text(encoding="utf-8")
            )
            selected_pdb = (out / "rfd3" / "selected.pdb").read_text(encoding="utf-8")

            self.assertEqual(rfd3.calls, [3, 3])
            self.assertEqual(len(raw_designs), 6)
            self.assertEqual(len(list((out / "rfd3" / "designs").glob("*.pdb"))), 3)
            self.assertEqual(debug.get("off_target_reject_count"), 3)
            self.assertEqual(debug.get("final_unique_count"), 3)
            self.assertAlmostEqual(float(debug.get("target_rmsd_cutoff") or 0.0), 1.0)
            self.assertLess(
                float(ca_rmsd(target_pdb, selected_pdb, chains=["A"]) or 99.0), 1.0
            )

    def test_pipeline_rfd3_target_rmsd_gate_falls_back_when_zero_backbones_pass(
        self,
    ) -> None:
        target_pdb = _bent_ca_backbone(0.0)

        class _MMseqsStub:
            def search(self, query_fasta, **kwargs):  # type: ignore[no-untyped-def]
                _ = kwargs
                _ = query_fasta
                a3m = ">query\nAGSY\n>hit1\nAGSY\n"
                a3m_b64 = base64.b64encode(gzip.compress(a3m.encode("utf-8"))).decode(
                    "ascii"
                )
                return {"tsv": "", "a3m_gz_b64": a3m_b64}

        class _RFD3Stub:
            def __init__(self) -> None:
                self.calls = 0

            def design(self, **kwargs):  # type: ignore[no-untyped-def]
                self.calls += 1
                requested = int(kwargs.get("max_return_designs") or 1)
                on_job_id = kwargs.get("on_job_id")
                if callable(on_job_id):
                    on_job_id(f"rfd3_target_gate_fail_job_{self.calls}")
                batch = [
                    _bent_ca_backbone(12.0 + float(self.calls) + idx, third_y=0.2 * idx)
                    for idx in range(requested)
                ]
                return {
                    "selected": {
                        "id": f"inputs_spec-1_0_model_{self.calls}_0",
                        "pdb": batch[0],
                        "json_name": f"inputs_spec-1_0_model_{self.calls}_0.json",
                    },
                    "designs": [
                        {
                            "id": f"inputs_spec-1_0_model_{self.calls}_{idx}",
                            "pdb": pdb_text,
                            "json_name": f"inputs_spec-1_0_model_{self.calls}_{idx}.json",
                        }
                        for idx, pdb_text in enumerate(batch)
                    ],
                }

        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=_MMseqsStub(),
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                rfd3=_RFD3Stub(),
            )
            req = PipelineRequest(
                target_fasta=">q1\nAGSY\n",
                target_pdb=target_pdb,
                dry_run=False,
                stop_after="rfd3",
                rfd3_mode="local_diversify",
                rfd3_input_pdb=target_pdb,
                rfd3_max_return_designs=2,
                rfd3_target_rmsd_cutoff=1.0,
                rfd3_max_attempted_designs=6,
                conservation_tiers=[0.3],
                num_seq_per_tier=1,
            )
            result = runner.run(req, run_id="rfd3_target_gate_fail")
            out = Path(result.output_dir)
            selected_pdb = (out / "rfd3" / "selected.pdb").read_text(encoding="utf-8")
            selected_meta = json.loads(
                (out / "rfd3" / "selected.json").read_text(encoding="utf-8")
            )
            recovery = json.loads(
                (out / "rfd3" / "recovery.json").read_text(encoding="utf-8")
            )

            self.assertEqual(selected_meta.get("source"), "fallback")
            self.assertEqual(selected_pdb, target_pdb)
            self.assertIn(
                "no acceptable backbones", str(recovery.get("error") or "").lower()
            )

    def test_pipeline_rfd3_uses_spec_chain_for_gate_and_input_preprocessing(
        self,
    ) -> None:
        multichain_pdb = (
            "ATOM      1  CA  ALA B   1      10.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY B   2      11.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  SER B   3      12.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      4  CA  TYR B   4      13.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      5  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      6  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      7  CA  SER A   3       2.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      8  CA  TYR A   4       3.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )

        def _ca_chain_lengths(pdb_text: str) -> dict[str, int]:
            out: dict[str, set[tuple[int, str]]] = {}
            for raw in pdb_text.splitlines():
                if not raw.startswith("ATOM"):
                    continue
                if raw[12:16].strip() != "CA":
                    continue
                chain_id = (raw[21] or " ").strip() or "_"
                resseq = int(raw[22:26])
                icode = raw[26].strip()
                out.setdefault(chain_id, set()).add((resseq, icode))
            return {chain_id: len(items) for chain_id, items in out.items()}

        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=None,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                rfd3=None,
            )
            req = PipelineRequest(
                target_fasta="",
                target_pdb=multichain_pdb,
                dry_run=True,
                stop_after="rfd3",
                rfd3_mode="binder",
                rfd3_contig="A1-4",
                rfd3_input_pdb=multichain_pdb,
                rfd3_max_return_designs=2,
                conservation_tiers=[0.3],
                num_seq_per_tier=1,
            )
            res = runner.run(req, run_id="rfd3_spec_chain_hint")
            out = Path(res.output_dir)

            mode_payload = json.loads(
                (out / "rfd3" / "mode.json").read_text(encoding="utf-8")
            )
            input_pdb = (out / "rfd3" / "input_files" / "input.pdb").read_text(
                encoding="utf-8"
            )
            dry_selected = (out / "rfd3" / "selected.pdb").read_text(encoding="utf-8")

            self.assertEqual(mode_payload.get("target_gate_design_chains"), ["A"])
            self.assertEqual(_ca_chain_lengths(input_pdb), {"A": 4})
            self.assertEqual(_ca_chain_lengths(dry_selected), {"A": 4})

    def test_pipeline_rfd3_strict_duplicate_mode_fails_after_retries(self) -> None:
        pdb = _simple_ca_backbone(0.0)

        class _MMseqsStub:
            def search(self, query_fasta, **kwargs):  # type: ignore[no-untyped-def]
                _ = kwargs
                _ = query_fasta
                a3m = ">query\nAGS\n>hit1\nAGS\n"
                a3m_b64 = base64.b64encode(gzip.compress(a3m.encode("utf-8"))).decode(
                    "ascii"
                )
                return {"tsv": "", "a3m_gz_b64": a3m_b64}

        class _RFD3Stub:
            def __init__(self) -> None:
                self.calls = 0

            def design(self, **kwargs):  # type: ignore[no-untyped-def]
                self.calls += 1
                on_job_id = kwargs.get("on_job_id")
                if callable(on_job_id):
                    on_job_id(f"rfd3_strict_job_{self.calls}")
                duplicate = _simple_ca_backbone(0.0)
                max_return_designs = int(kwargs.get("max_return_designs") or 1)
                return {
                    "selected": {
                        "id": "inputs_spec-1_0_model_0",
                        "pdb": duplicate,
                        "cif_gz_name": "inputs_spec-1_0_model_0.cif.gz",
                        "json_name": "inputs_spec-1_0_model_0.json",
                    },
                    "designs": [
                        {
                            "id": f"inputs_spec-1_0_model_{idx}",
                            "pdb": duplicate,
                            "cif_gz_name": f"inputs_spec-1_0_model_{idx}.cif.gz",
                            "json_name": f"inputs_spec-1_0_model_{idx}.json",
                        }
                        for idx in range(max_return_designs)
                    ],
                }

        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=_MMseqsStub(),
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                rfd3=_RFD3Stub(),
            )
            req = PipelineRequest(
                target_fasta=">q1\nAGS\n",
                target_pdb=pdb,
                dry_run=False,
                stop_after="rfd3",
                rfd3_mode="local_diversify",
                rfd3_input_pdb=pdb,
                rfd3_max_return_designs=3,
                rfd3_fail_on_duplicate_backbones=True,
                conservation_tiers=[0.3],
                num_seq_per_tier=1,
            )
            with self.assertRaisesRegex(RuntimeError, "duplicate backbone"):
                runner.run(req, run_id="rfd3_duplicate_strict_fail")

    def test_pipeline_rfd3_reuses_cached_selected_on_rerun(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )

        class _MMseqsStub:
            def search(self, query_fasta, **kwargs):  # type: ignore[no-untyped-def]
                _ = kwargs
                _ = query_fasta
                a3m = ">query\nAG\n>hit1\nAG\n"
                a3m_b64 = base64.b64encode(gzip.compress(a3m.encode("utf-8"))).decode(
                    "ascii"
                )
                return {"tsv": "", "a3m_gz_b64": a3m_b64}

        class _RFD3Stub:
            def __init__(self, selected_pdb: str) -> None:
                self.selected_pdb = selected_pdb
                self.calls = 0

            def design(self, **kwargs):  # type: ignore[no-untyped-def]
                self.calls += 1
                on_job_id = kwargs.get("on_job_id")
                if callable(on_job_id):
                    on_job_id(f"rfd3_job_{self.calls}")
                return {
                    "selected": {
                        "id": "inputs_spec-1_0_model_0",
                        "pdb": self.selected_pdb,
                        "cif_gz_name": "inputs_spec-1_0_model_0.cif.gz",
                        "json_name": "inputs_spec-1_0_model_0.json",
                    },
                    "designs": [
                        {
                            "id": "inputs_spec-1_0_model_0",
                            "pdb": self.selected_pdb,
                            "cif_gz_name": "inputs_spec-1_0_model_0.cif.gz",
                            "json_name": "inputs_spec-1_0_model_0.json",
                        }
                    ],
                }

        with _tmpdir() as tmp:
            rfd3 = _RFD3Stub(pdb)
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=_MMseqsStub(),
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                rfd3=rfd3,
            )
            req = PipelineRequest(
                target_fasta=">q1\nAG\n",
                target_pdb=pdb,
                dry_run=False,
                stop_after="rfd3",
                rfd3_input_pdb=pdb,
                rfd3_contig="A1-2",
                rfd3_use_ensemble=True,
                rfd3_max_return_designs=1,
                conservation_tiers=[0.3],
                num_seq_per_tier=1,
            )

            run_id = "resume_rfd3_cache"
            runner.run(req, run_id=run_id)
            run_root = Path(tmp) / run_id
            selected_meta = json.loads(
                (run_root / "rfd3" / "selected.json").read_text(encoding="utf-8")
            )
            self.assertEqual(selected_meta.get("id"), "rfd3_spec-1_0_model_0")
            self.assertEqual(
                selected_meta.get("upstream_id"), "inputs_spec-1_0_model_0"
            )
            self.assertEqual(
                selected_meta.get("json_name"), "rfd3_spec-1_0_model_0.json"
            )
            designs_meta = json.loads(
                (run_root / "rfd3" / "designs.json").read_text(encoding="utf-8")
            )
            self.assertEqual(designs_meta[0].get("id"), "rfd3_spec-1_0_model_0")
            self.assertTrue(
                (run_root / "rfd3" / "designs" / "rfd3_spec-1_0_model_0.pdb").exists()
            )
            runpod_job_path = run_root / "rfd3" / "runpod_job.json"
            runpod_meta = json.loads(runpod_job_path.read_text(encoding="utf-8"))
            runpod_meta["job_id"] = "cancelled_job"
            runpod_job_path.write_text(json.dumps(runpod_meta), encoding="utf-8")

            runner.run(req, run_id=run_id)

            self.assertEqual(rfd3.calls, 1)
            events = [
                json.loads(line)
                for line in (run_root / "events.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            rfd3_completed = [
                e
                for e in events
                if e.get("stage") == "rfd3" and e.get("state") == "completed"
            ]
            self.assertTrue(rfd3_completed)
            self.assertEqual(rfd3_completed[-1].get("detail"), "cached")

    def test_pipeline_bioemu_reuses_cached_outputs_on_rerun(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )

        class _MMseqsStub:
            def search(self, query_fasta, **kwargs):  # type: ignore[no-untyped-def]
                _ = kwargs
                _ = query_fasta
                a3m = ">query\nAG\n>hit1\nAG\n"
                a3m_b64 = base64.b64encode(gzip.compress(a3m.encode("utf-8"))).decode(
                    "ascii"
                )
                return {"tsv": "", "a3m_gz_b64": a3m_b64}

        class _BioEmuStub:
            def __init__(self, sample_pdb: str) -> None:
                self.sample_pdb = sample_pdb
                self.calls = 0

            def sample(self, **kwargs):  # type: ignore[no-untyped-def]
                self.calls += 1
                on_job_id = kwargs.get("on_job_id")
                if callable(on_job_id):
                    on_job_id(f"bioemu_job_{self.calls}")
                return {
                    "sample_pdbs": [
                        {
                            "id": "bioemu_topology",
                            "pdb": self.sample_pdb,
                            "frame_index": 0,
                        }
                    ],
                    "topology_pdb": self.sample_pdb,
                }

        with _tmpdir() as tmp:
            bioemu = _BioEmuStub(pdb)
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=_MMseqsStub(),
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                bioemu=bioemu,
            )
            req = PipelineRequest(
                target_fasta=">q1\nAG\n",
                target_pdb=pdb,
                dry_run=False,
                stop_after="bioemu",
                bioemu_use=True,
                bioemu_num_samples=2,
                bioemu_max_return_structures=1,
                conservation_tiers=[0.3],
                num_seq_per_tier=1,
            )

            run_id = "resume_bioemu_cache"
            runner.run(req, run_id=run_id)
            run_root = Path(tmp) / run_id
            runpod_job_path = run_root / "bioemu" / "runpod_job.json"
            runpod_meta = json.loads(runpod_job_path.read_text(encoding="utf-8"))
            runpod_meta["job_id"] = "cancelled_job"
            runpod_job_path.write_text(json.dumps(runpod_meta), encoding="utf-8")

            runner.run(req, run_id=run_id)

            self.assertEqual(bioemu.calls, 1)
            events = [
                json.loads(line)
                for line in (run_root / "events.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            bioemu_completed = [
                e
                for e in events
                if e.get("stage") == "bioemu" and e.get("state") == "completed"
            ]
            self.assertTrue(bioemu_completed)
            self.assertIn("cached", str(bioemu_completed[-1].get("detail") or ""))

    def test_pipeline_bioemu_target_rmsd_gate_retries_until_requested_count_is_filled(
        self,
    ) -> None:
        target_pdb = _bent_ca_backbone(0.0)
        first_batch = [
            _bent_ca_backbone(0.10),
            _bent_ca_backbone(0.40, third_y=0.05),
            _bent_ca_backbone(0.90, third_z=0.05),
            _bent_ca_backbone(1.50, tail_y=0.10),
            _bent_ca_backbone(5.00),
            _bent_ca_backbone(5.50, third_y=0.20),
            _bent_ca_backbone(6.00, third_z=0.10),
            _bent_ca_backbone(7.00, tail_y=0.20),
            _bent_ca_backbone(8.00, third_y=0.10, third_z=0.10),
            _bent_ca_backbone(9.00, tail_y=0.30),
        ]
        retry_batch = [
            _bent_ca_backbone(0.20, third_y=0.02),
            _bent_ca_backbone(0.30, third_z=0.02),
            _bent_ca_backbone(0.60, tail_y=0.05),
            _bent_ca_backbone(1.10, third_y=0.03, third_z=0.01),
            _bent_ca_backbone(1.70, tail_y=0.04),
            _bent_ca_backbone(1.80, third_y=0.02, tail_y=0.03),
        ]

        class _MMseqsStub:
            def search(self, query_fasta, **kwargs):  # type: ignore[no-untyped-def]
                _ = kwargs
                _ = query_fasta
                a3m = ">query\nAGSY\n>hit1\nAGSY\n"
                a3m_b64 = base64.b64encode(gzip.compress(a3m.encode("utf-8"))).decode(
                    "ascii"
                )
                return {"tsv": "", "a3m_gz_b64": a3m_b64}

        class _BioEmuStub:
            def __init__(self) -> None:
                self.calls: list[dict[str, int | None]] = []

            def sample(self, **kwargs):  # type: ignore[no-untyped-def]
                requested_return = int(kwargs.get("max_return_sample_pdbs") or 0)
                call = {
                    "num_samples": int(kwargs.get("num_samples") or 0),
                    "max_return_sample_pdbs": requested_return,
                    "min_return_sample_pdbs": int(
                        kwargs.get("min_return_sample_pdbs") or 0
                    ),
                    "base_seed": int(kwargs.get("base_seed"))
                    if kwargs.get("base_seed") is not None
                    else None,
                }
                self.calls.append(call)
                on_job_id = kwargs.get("on_job_id")
                if callable(on_job_id):
                    on_job_id(f"bioemu_target_gate_job_{len(self.calls)}")
                batch = first_batch if len(self.calls) == 1 else retry_batch
                return {
                    "sample_pdbs": [
                        {
                            "id": f"bioemu_{len(self.calls)}_{idx}",
                            "pdb": pdb_text,
                            "frame_index": idx,
                        }
                        for idx, pdb_text in enumerate(batch[:requested_return])
                    ],
                    "topology_pdb": batch[0],
                }

        with _tmpdir() as tmp:
            bioemu = _BioEmuStub()
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=_MMseqsStub(),
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                bioemu=bioemu,
            )
            req = PipelineRequest(
                target_fasta=">q1\nAGSY\n",
                target_pdb=target_pdb,
                dry_run=False,
                stop_after="bioemu",
                bioemu_use=True,
                bioemu_sequence="AGSY",
                bioemu_num_samples=20,
                bioemu_max_return_structures=10,
                bioemu_target_rmsd_cutoff=2.0,
                bioemu_max_attempted_structures=16,
                conservation_tiers=[0.3],
                num_seq_per_tier=1,
            )

            runner.run(req, run_id="bioemu_target_gate_retry")

            out = Path(tmp) / "bioemu_target_gate_retry"
            debug = json.loads(
                (out / "bioemu" / "debug_summary.json").read_text(encoding="utf-8")
            )
            raw_samples = json.loads(
                (out / "bioemu" / "raw_samples.json").read_text(encoding="utf-8")
            )
            sample_meta = json.loads(
                (out / "bioemu" / "sample_pdbs.json").read_text(encoding="utf-8")
            )

            self.assertEqual(
                bioemu.calls,
                [
                    {
                        "num_samples": 20,
                        "max_return_sample_pdbs": 10,
                        "min_return_sample_pdbs": 10,
                        "base_seed": None,
                    },
                    {
                        "num_samples": 12,
                        "max_return_sample_pdbs": 6,
                        "min_return_sample_pdbs": 6,
                        "base_seed": None,
                    },
                ],
            )
            self.assertEqual(len(raw_samples), 16)
            self.assertEqual(len(sample_meta.get("samples") or []), 10)
            self.assertEqual(len(list((out / "bioemu" / "designs").glob("*.pdb"))), 10)
            self.assertTrue(bool(debug.get("retry_performed")))
            self.assertEqual(debug.get("retry_attempt_count"), 1)
            self.assertEqual(debug.get("off_target_reject_count"), 6)
            self.assertEqual(debug.get("final_accepted_count"), 10)
            self.assertAlmostEqual(float(debug.get("target_rmsd_cutoff") or 0.0), 2.0)
            for entry in sample_meta.get("samples") or []:
                if not isinstance(entry, dict):
                    continue
                sample_id = str(entry.get("id") or "").strip()
                self.assertTrue(sample_id)
                pdb_path = out / "bioemu" / "designs" / f"{sample_id}.pdb"
                self.assertTrue(pdb_path.exists())
                rmsd = ca_rmsd(
                    target_pdb, pdb_path.read_text(encoding="utf-8"), chains=["A"]
                )
                self.assertIsNotNone(rmsd)
                self.assertLessEqual(float(rmsd or 99.0), 2.0)

    def test_pipeline_bioemu_target_rmsd_gate_fails_when_retry_budget_is_exhausted(
        self,
    ) -> None:
        target_pdb = _bent_ca_backbone(0.0)

        class _MMseqsStub:
            def search(self, query_fasta, **kwargs):  # type: ignore[no-untyped-def]
                _ = kwargs
                _ = query_fasta
                a3m = ">query\nAGSY\n>hit1\nAGSY\n"
                a3m_b64 = base64.b64encode(gzip.compress(a3m.encode("utf-8"))).decode(
                    "ascii"
                )
                return {"tsv": "", "a3m_gz_b64": a3m_b64}

        class _BioEmuStub:
            def __init__(self) -> None:
                self.calls: list[dict[str, int | None]] = []

            def sample(self, **kwargs):  # type: ignore[no-untyped-def]
                requested_return = int(kwargs.get("max_return_sample_pdbs") or 0)
                self.calls.append(
                    {
                        "num_samples": int(kwargs.get("num_samples") or 0),
                        "max_return_sample_pdbs": requested_return,
                        "min_return_sample_pdbs": int(
                            kwargs.get("min_return_sample_pdbs") or 0
                        ),
                        "base_seed": int(kwargs.get("base_seed"))
                        if kwargs.get("base_seed") is not None
                        else None,
                    }
                )
                on_job_id = kwargs.get("on_job_id")
                if callable(on_job_id):
                    on_job_id(f"bioemu_target_gate_fail_job_{len(self.calls)}")
                batch = [
                    _bent_ca_backbone(
                        5.0 + float(len(self.calls)) + idx, third_y=0.1 * idx
                    )
                    for idx in range(requested_return)
                ]
                return {
                    "sample_pdbs": [
                        {
                            "id": f"bioemu_fail_{len(self.calls)}_{idx}",
                            "pdb": pdb_text,
                            "frame_index": idx,
                        }
                        for idx, pdb_text in enumerate(batch)
                    ],
                    "topology_pdb": batch[0] if batch else "",
                }

        with _tmpdir() as tmp:
            bioemu = _BioEmuStub()
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=_MMseqsStub(),
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                bioemu=bioemu,
            )
            req = PipelineRequest(
                target_fasta=">q1\nAGSY\n",
                target_pdb=target_pdb,
                dry_run=False,
                stop_after="bioemu",
                bioemu_use=True,
                bioemu_sequence="AGSY",
                bioemu_num_samples=20,
                bioemu_max_return_structures=10,
                bioemu_target_rmsd_cutoff=2.0,
                bioemu_max_attempted_structures=20,
                conservation_tiers=[0.3],
                num_seq_per_tier=1,
            )

            with self.assertRaisesRegex(RuntimeError, "BioEmu target RMSD gate"):
                runner.run(req, run_id="bioemu_target_gate_fail")

            self.assertEqual(
                bioemu.calls,
                [
                    {
                        "num_samples": 20,
                        "max_return_sample_pdbs": 10,
                        "min_return_sample_pdbs": 10,
                        "base_seed": None,
                    },
                    {
                        "num_samples": 20,
                        "max_return_sample_pdbs": 10,
                        "min_return_sample_pdbs": 10,
                        "base_seed": None,
                    },
                ],
            )

    def test_pipeline_bioemu_processed_stage_pdb_is_synced_for_compare(self) -> None:
        target_pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        bioemu_pdb = (
            "ATOM      1  CA  ALA A   0       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   1       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )

        class _MMseqsStub:
            def search(self, query_fasta, **kwargs):  # type: ignore[no-untyped-def]
                _ = kwargs
                _ = query_fasta
                a3m = ">query\nAG\n>hit1\nAG\n"
                a3m_b64 = base64.b64encode(gzip.compress(a3m.encode("utf-8"))).decode(
                    "ascii"
                )
                return {"tsv": "", "a3m_gz_b64": a3m_b64}

        class _BioEmuStub:
            def sample(self, **kwargs):  # type: ignore[no-untyped-def]
                on_job_id = kwargs.get("on_job_id")
                if callable(on_job_id):
                    on_job_id("bioemu_job_sync")
                return {"topology_pdb": bioemu_pdb}

        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=_MMseqsStub(),
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                bioemu=_BioEmuStub(),
            )
            req = PipelineRequest(
                target_fasta=">q1\nAG\n",
                target_pdb=target_pdb,
                dry_run=False,
                bioemu_use=True,
                bioemu_sequence="AG",
                bioemu_max_return_structures=1,
                stop_after="design",
                num_seq_per_tier=1,
                conservation_tiers=[0.3],
                pdb_strip_nonpositive_resseq=True,
                pdb_renumber_resseq_from_1=False,
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            stage_pdb = (out / "bioemu" / "designs" / "bioemu_topology.pdb").read_text(
                encoding="utf-8"
            )
            self.assertIn("ALA A   1", stage_pdb)
            self.assertNotIn("ALA A   0", stage_pdb)
            output_payload = json.loads(
                (out / "bioemu" / "output.json").read_text(encoding="utf-8")
            )
            self.assertIn("ALA A   1", str(output_payload.get("topology_pdb") or ""))
            self.assertNotIn("ALA A   0", str(output_payload.get("topology_pdb") or ""))

    def test_pipeline_fails_when_rfd3_requested_ensemble_pdbs_are_missing(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )

        class _MMseqsStub:
            def search(self, query_fasta, **kwargs):  # type: ignore[no-untyped-def]
                _ = kwargs
                _ = query_fasta
                a3m = ">query\nAG\n>hit1\nAG\n"
                a3m_b64 = base64.b64encode(gzip.compress(a3m.encode("utf-8"))).decode(
                    "ascii"
                )
                return {"tsv": "", "a3m_gz_b64": a3m_b64}

        class _RFD3Stub:
            def design(self, **kwargs):  # type: ignore[no-untyped-def]
                on_job_id = kwargs.get("on_job_id")
                if callable(on_job_id):
                    on_job_id("rfd3_job_missing_ensemble")
                return {
                    "selected": {
                        "id": "inputs_spec-1_0_model_0",
                        "pdb": pdb,
                        "cif_gz_name": "inputs_spec-1_0_model_0.cif.gz",
                        "json_name": "inputs_spec-1_0_model_0.json",
                    },
                    "designs": [
                        {
                            "id": "inputs_spec-1_0_model_0",
                            "cif_gz_name": "inputs_spec-1_0_model_0.cif.gz",
                            "json_name": "inputs_spec-1_0_model_0.json",
                        },
                        {
                            "id": "inputs_spec-1_1_model_0",
                            "cif_gz_name": "inputs_spec-1_1_model_0.cif.gz",
                            "json_name": "inputs_spec-1_1_model_0.json",
                        },
                    ],
                }

        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=_MMseqsStub(),
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                rfd3=_RFD3Stub(),
            )
            req = PipelineRequest(
                target_fasta=">q1\nAG\n",
                target_pdb=pdb,
                dry_run=False,
                stop_after="rfd3",
                rfd3_input_pdb=pdb,
                rfd3_contig="A1-2",
                rfd3_use_ensemble=True,
                rfd3_max_return_designs=2,
                conservation_tiers=[0.3],
                num_seq_per_tier=1,
            )
            with self.assertRaisesRegex(
                RuntimeError, "RFD3 returned only 1 design PDB"
            ):
                runner.run(req, run_id="rfd3_missing_design_pdbs")

    def test_pipeline_fails_when_bioemu_requested_sample_pdbs_are_missing(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )

        class _MMseqsStub:
            def search(self, query_fasta, **kwargs):  # type: ignore[no-untyped-def]
                _ = kwargs
                _ = query_fasta
                a3m = ">query\nAG\n>hit1\nAG\n"
                a3m_b64 = base64.b64encode(gzip.compress(a3m.encode("utf-8"))).decode(
                    "ascii"
                )
                return {"tsv": "", "a3m_gz_b64": a3m_b64}

        class _BioEmuStub:
            def sample(self, **kwargs):  # type: ignore[no-untyped-def]
                on_job_id = kwargs.get("on_job_id")
                if callable(on_job_id):
                    on_job_id("bioemu_job_missing_samples")
                return {"topology_pdb": pdb}

        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=_MMseqsStub(),
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                bioemu=_BioEmuStub(),
            )
            req = PipelineRequest(
                target_fasta=">q1\nAG\n",
                target_pdb=pdb,
                dry_run=False,
                stop_after="bioemu",
                bioemu_use=True,
                bioemu_sequence="AG",
                bioemu_num_samples=10,
                bioemu_max_return_structures=10,
                conservation_tiers=[0.3],
                num_seq_per_tier=1,
            )
            with self.assertRaisesRegex(
                RuntimeError, "BioEmu returned only 1 structure"
            ):
                runner.run(req, run_id="bioemu_missing_sample_pdbs")

    def test_pipeline_bioemu_uses_oversampled_generation_count_for_strict_filtered_returns(
        self,
    ) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )

        class _BioEmuStub:
            def __init__(self, sample_pdb: str) -> None:
                self.sample_pdb = sample_pdb
                self.kwargs = None

            def sample(self, **kwargs):  # type: ignore[no-untyped-def]
                self.kwargs = kwargs
                on_job_id = kwargs.get("on_job_id")
                if callable(on_job_id):
                    on_job_id("bioemu_job_oversampled")
                return {
                    "sample_pdbs": [
                        {
                            "id": f"bioemu_{i:03d}",
                            "pdb": self.sample_pdb,
                            "frame_index": i,
                        }
                        for i in range(10)
                    ],
                    "topology_pdb": self.sample_pdb,
                }

        with _tmpdir() as tmp:
            bioemu = _BioEmuStub(pdb)
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=None,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                bioemu=bioemu,
            )
            req = pipeline_request_from_args(
                {
                    "target_fasta": ">q1\nAG\n",
                    "target_pdb": pdb,
                    "dry_run": False,
                    "stop_after": "bioemu",
                    "bioemu_use": True,
                    "bioemu_max_return_structures": 10,
                    "bioemu_filter_samples": True,
                    "conservation_tiers": [0.3],
                    "num_seq_per_tier": 1,
                }
            )
            runner.run(req, run_id="bioemu_oversampled_defaults")
            self.assertIsNotNone(bioemu.kwargs)
            self.assertEqual(bioemu.kwargs.get("num_samples"), 50)
            self.assertEqual(bioemu.kwargs.get("max_return_sample_pdbs"), 10)
            self.assertEqual(bioemu.kwargs.get("min_return_sample_pdbs"), 10)

    def test_pipeline_wt_diff_reuses_cached_outputs_on_rerun(self) -> None:
        fasta = ">q1\nACDEFGHIK\n"
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  CYS A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  ASP A   3       2.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      4  CA  GLU A   4       3.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      5  CA  PHE A   5       4.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      6  CA  GLY A   6       5.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      7  CA  HIS A   7       6.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      8  CA  ILE A   8       7.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      9  CA  LYS A   9       8.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )

        class _MMseqsStub:
            def __init__(self) -> None:
                self.novelty_calls = 0

            def search(self, query_fasta, **kwargs):  # type: ignore[no-untyped-def]
                _ = query_fasta
                if bool(kwargs.get("return_a3m")):
                    a3m = ">query\nACDEFGHIK\n>hit1\nACDEFGHIK\n"
                    a3m_b64 = base64.b64encode(
                        gzip.compress(a3m.encode("utf-8"))
                    ).decode("ascii")
                    return {"tsv": "", "a3m_gz_b64": a3m_b64}
                self.novelty_calls += 1
                return {"tsv": "q1\thit1\t99.0\n"}

        with _tmpdir() as tmp:
            mmseqs = _MMseqsStub()
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=mmseqs,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
            )
            req = PipelineRequest(
                target_fasta=fasta,
                target_pdb=pdb,
                dry_run=True,
                novelty_enabled=True,
                conservation_tiers=[0.3],
                num_seq_per_tier=2,
                soluprot_cutoff=0.0,
                af2_plddt_cutoff=0.0,
                af2_rmsd_cutoff=0.0,
            )

            run_id = "resume_novelty_cache"
            runner.run(req, run_id=run_id)
            runner.run(req, run_id=run_id)

            self.assertEqual(mmseqs.novelty_calls, 0)
            run_root = Path(tmp) / run_id
            events = [
                json.loads(line)
                for line in (run_root / "events.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            novelty_completed = [
                e
                for e in events
                if e.get("stage") == "novelty_30" and e.get("state") == "completed"
            ]
            self.assertTrue(novelty_completed)
            self.assertEqual(novelty_completed[-1].get("detail"), "cached")

    def test_pipeline_dry_run_merges_rfd3_and_bioemu_backbones(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=None,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                rfd3=None,
            )
            req = PipelineRequest(
                target_fasta="",
                target_pdb="",
                dry_run=True,
                rfd3_contig="A1-2",
                rfd3_input_pdb=pdb,
                rfd3_use_ensemble=True,
                rfd3_max_return_designs=2,
                bioemu_use=True,
                bioemu_num_samples=3,
                bioemu_max_return_structures=3,
                num_seq_per_tier=1,
                conservation_tiers=[0.3],
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            payload = json.loads((out / "backbones.json").read_text(encoding="utf-8"))
            backbones = payload.get("backbones") or []
            sources = [
                str(item.get("source") or "")
                for item in backbones
                if isinstance(item, dict)
            ]
            self.assertEqual(sources.count("rfd3"), 1)
            self.assertEqual(sources.count("bioemu"), 3)
            rfd3_ids = [
                str(item.get("id") or "")
                for item in backbones
                if isinstance(item, dict) and item.get("source") == "rfd3"
            ]
            self.assertTrue(all(rid.startswith("rfd3_") for rid in rfd3_ids))
            self.assertFalse(any("inputs_spec" in rid for rid in rfd3_ids))

            source_summary = (
                payload.get("sources")
                if isinstance(payload.get("sources"), dict)
                else {}
            )
            rfd3_summary = (
                source_summary.get("rfd3")
                if isinstance(source_summary.get("rfd3"), dict)
                else {}
            )
            bioemu_summary = (
                source_summary.get("bioemu")
                if isinstance(source_summary.get("bioemu"), dict)
                else {}
            )
            self.assertEqual(rfd3_summary.get("requested_count"), 2)
            self.assertEqual(rfd3_summary.get("observed_count"), 2)
            self.assertEqual(rfd3_summary.get("materialized_count"), 1)
            self.assertEqual(rfd3_summary.get("propagated_count"), 1)
            self.assertEqual(rfd3_summary.get("unique_count"), 1)
            self.assertEqual(rfd3_summary.get("duplicate_count"), 1)
            self.assertEqual(rfd3_summary.get("propagation_mode"), "selected_only")
            self.assertEqual(rfd3_summary.get("selected_backbone_id"), rfd3_ids[0])
            self.assertEqual(bioemu_summary.get("requested_count"), 3)
            self.assertEqual(bioemu_summary.get("observed_count"), 3)
            self.assertEqual(bioemu_summary.get("materialized_count"), 3)
            self.assertEqual(bioemu_summary.get("propagated_count"), 3)
            self.assertEqual(bioemu_summary.get("propagation_mode"), "all_materialized")
            self.assertEqual(payload.get("propagation_mode"), "partial")

            primary = backbones[0] if isinstance(backbones[0], dict) else {}
            self.assertTrue(primary.get("primary"))
            self.assertTrue(primary.get("selected"))
            self.assertTrue(primary.get("propagated"))
            self.assertTrue(primary.get("materialized"))
            self.assertEqual(primary.get("origin_stage"), "rfd3")

            first_bioemu = next(
                item
                for item in backbones
                if isinstance(item, dict) and item.get("source") == "bioemu"
            )
            self.assertTrue(first_bioemu.get("propagated"))
            self.assertTrue(first_bioemu.get("materialized"))
            self.assertEqual(first_bioemu.get("origin_stage"), "bioemu")
            self.assertIsInstance(first_bioemu.get("frame_index"), int)

            tier_payload = json.loads(
                (out / "tiers" / "30" / "proteinmpnn_backbones.json").read_text(
                    encoding="utf-8"
                )
            )
            tier_backbones = (
                tier_payload.get("backbones")
                if isinstance(tier_payload.get("backbones"), list)
                else []
            )
            self.assertEqual(len(tier_backbones), 4)
            self.assertTrue(
                all(
                    isinstance(item, dict) and item.get("propagated")
                    for item in tier_backbones
                )
            )
            self.assertTrue(
                all(
                    isinstance(item, dict) and item.get("source") in {"rfd3", "bioemu"}
                    for item in tier_backbones
                )
            )
            self.assertTrue(
                all(
                    isinstance(item, dict)
                    and isinstance(item.get("sequence_count"), int)
                    and int(item.get("sequence_count") or 0) >= 1
                    for item in tier_backbones
                )
            )

    def test_pipeline_dry_run_generates_120_sequences_for_10_rfd3_and_10_bioemu_backbones(
        self,
    ) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=None,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                rfd3=None,
            )
            req = PipelineRequest(
                target_fasta="",
                target_pdb="",
                dry_run=True,
                stop_after="design",
                rfd3_contig="A1-2",
                rfd3_input_pdb=pdb,
                rfd3_use_ensemble=True,
                rfd3_max_return_designs=10,
                bioemu_use=True,
                bioemu_num_samples=10,
                bioemu_max_return_structures=10,
                num_seq_per_tier=2,
                conservation_tiers=[0.3, 0.5, 0.7],
            )
            res = runner.run(req)
            out = Path(res.output_dir)

            self.assertEqual(len(res.tiers), 3)
            self.assertTrue(
                all(len(tier.proteinmpnn_samples) == 22 for tier in res.tiers)
            )
            self.assertEqual(
                sum(len(tier.proteinmpnn_samples) for tier in res.tiers), 66
            )

            payload = json.loads((out / "backbones.json").read_text(encoding="utf-8"))
            source_summary = (
                payload.get("sources")
                if isinstance(payload.get("sources"), dict)
                else {}
            )
            rfd3_summary = (
                source_summary.get("rfd3")
                if isinstance(source_summary.get("rfd3"), dict)
                else {}
            )
            bioemu_summary = (
                source_summary.get("bioemu")
                if isinstance(source_summary.get("bioemu"), dict)
                else {}
            )
            self.assertEqual(rfd3_summary.get("propagated_count"), 1)
            self.assertEqual(rfd3_summary.get("duplicate_count"), 9)
            self.assertEqual(bioemu_summary.get("propagated_count"), 10)

            for tier_key in ("30", "50", "70"):
                tier_payload = json.loads(
                    (out / "tiers" / tier_key / "proteinmpnn_backbones.json").read_text(
                        encoding="utf-8"
                    )
                )
                tier_backbones = (
                    tier_payload.get("backbones")
                    if isinstance(tier_payload.get("backbones"), list)
                    else []
                )
                self.assertEqual(len(tier_backbones), 11)
                self.assertTrue(
                    all(
                        isinstance(item, dict)
                        and int(item.get("sequence_count") or 0) == 2
                        for item in tier_backbones
                    )
                )

    def test_pipeline_includes_fixed_positions_extra(self) -> None:
        fasta = ">q1\nACDEFGHIK\n"
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  CYS A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  ASP A   3       2.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      4  CA  GLU A   4       3.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      5  CA  PHE A   5       4.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      6  CA  GLY A   6       5.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      7  CA  HIS A   7       6.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      8  CA  ILE A   8       7.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      9  CA  LYS A   9       8.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None
            )
            req = PipelineRequest(
                target_fasta=fasta,
                target_pdb=pdb,
                dry_run=True,
                num_seq_per_tier=2,
                conservation_tiers=[0.3],
                fixed_positions_extra={"A": [9]},
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            fixed = json.loads(
                (out / "tiers" / "30" / "fixed_positions.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIn(9, fixed.get("A", []))

    def test_pipeline_projects_original_ligand_mask_to_rfd3_backbone(self) -> None:
        target_pdb_with_ligand = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  CYS A   2       2.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  ASP A   3       4.000   0.000   0.000  1.00 20.00           C\n"
            "HETATM    4  C1  LIG Z   1       2.200   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        rfd3_input_pdb = (
            "ATOM      1  CA  ALA A   1       0.000   1.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  CYS A   2       2.000   1.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  ASP A   3       4.000   1.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=None,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                rfd3=None,
            )
            req = PipelineRequest(
                target_fasta=">q1\nACD\n",
                target_pdb=target_pdb_with_ligand,
                rfd3_input_pdb=rfd3_input_pdb,
                rfd3_contig="A1-3",
                ligand_mask_use_original_target=True,
                dry_run=True,
                num_seq_per_tier=1,
                conservation_tiers=[0.3],
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            projected = json.loads(
                (out / "ligand_mask_original_target.json").read_text(encoding="utf-8")
            )
            query_by_chain = projected.get("query_positions_by_chain") or {}
            projected_positions = query_by_chain.get("A") or []
            self.assertTrue(projected_positions)

            fixed = json.loads(
                (out / "tiers" / "30" / "fixed_positions.json").read_text(
                    encoding="utf-8"
                )
            )
            fixed_a = set(fixed.get("A") or [])
            self.assertTrue(set(int(p) for p in projected_positions).issubset(fixed_a))

    def test_pipeline_requires_fixed_positions_extra_for_sequence_only(self) -> None:
        fasta = ">q1\nACDEFGHIK\n"
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None
            )
            req = PipelineRequest(
                target_fasta=fasta,
                target_pdb="",
                dry_run=False,
                conservation_tiers=[0.3],
                rfd3_use=False,
            )
            run_id = "sequence_only_requires_fixed_positions_extra"
            with self.assertRaises(Exception) as ctx:
                runner.run(req, run_id=run_id)
            self.assertIn("fixed_positions_extra", str(ctx.exception))
            status = json.loads(
                (Path(tmp) / run_id / "status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(status.get("stage"), "needs_fixed_positions_extra")
            self.assertEqual(status.get("state"), "failed")

    def test_pipeline_limits_af2_candidates_per_tier_by_soluprot_rank(self) -> None:
        fasta = ">q1\nACDEFGHIK\n"
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  CYS A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  ASP A   3       2.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      4  CA  GLU A   4       3.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      5  CA  PHE A   5       4.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      6  CA  GLY A   6       5.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      7  CA  HIS A   7       6.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      8  CA  ILE A   8       7.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      9  CA  LYS A   9       8.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None
            )
            req = PipelineRequest(
                target_fasta=fasta,
                target_pdb=pdb,
                dry_run=True,
                num_seq_per_tier=6,
                conservation_tiers=[0.3],
                soluprot_cutoff=0.0,
                af2_max_candidates_per_tier=1,
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            af2_scores = json.loads(
                (out / "tiers" / "30" / "af2_scores.json").read_text(encoding="utf-8")
            )
            candidate_ids = af2_scores.get("candidate_ids") or []
            self.assertEqual(len(candidate_ids), 1)
            self.assertTrue(bool(af2_scores.get("candidate_budget_applied")))
            self.assertEqual(int(af2_scores.get("max_candidates_per_tier") or 0), 1)
            self.assertGreater(
                int(af2_scores.get("candidate_count_before_budget") or 0), 1
            )

    def test_chain_strategy_forces_single_chain_in_monomer_mode(self) -> None:
        fasta = ">q1\nACD\n"
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  CYS A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  ASP A   3       2.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      4  CA  GLU B   1       0.000   1.000   0.000  1.00 20.00           C\n"
            "ATOM      5  CA  PHE B   2       1.000   1.000   0.000  1.00 20.00           C\n"
            "ATOM      6  CA  GLY B   3       2.000   1.000   0.000  1.00 20.00           C\n"
            "END\n"
        )

        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None
            )
            req = PipelineRequest(
                target_fasta=fasta,
                target_pdb=pdb,
                design_chains=["A", "B"],
                af2_model_preset="monomer",
                dry_run=True,
                num_seq_per_tier=2,
                conservation_tiers=[0.3],
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            payload = json.loads(
                (out / "tiers" / "30" / "proteinmpnn.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["request"]["pdb_path_chains"], ["A"])

    def test_target_record_from_pdb_ignores_x_only_nonprotein_first_chain(
        self,
    ) -> None:
        pdb = "".join(
            [
                _pdb_atom(1, "P", "DA", "E", 1, 0.0, 0.0, 0.0, "P"),
                _pdb_atom(2, "P", "DT", "E", 2, 1.0, 0.0, 0.0, "P"),
                _pdb_atom(3, "P", "DG", "E", 3, 2.0, 0.0, 0.0, "P"),
                _pdb_atom(4, "CA", "ALA", "A", 1, 0.0, 1.0, 0.0, "C"),
                _pdb_atom(5, "CA", "CYS", "A", 2, 1.0, 1.0, 0.0, "C"),
                "END\n",
            ]
        )

        record = _target_record_from_pdb(pdb, design_chains=None)

        self.assertEqual(record.header, "pdb_chain_A")
        self.assertEqual(record.sequence, "AC")

    def test_chain_strategy_ignores_x_only_query_when_target_fasta_empty(
        self,
    ) -> None:
        pdb = "".join(
            [
                _pdb_atom(1, "P", "DA", "E", 1, 0.0, 0.0, 0.0, "P"),
                _pdb_atom(2, "P", "DT", "E", 2, 1.0, 0.0, 0.0, "P"),
                _pdb_atom(3, "P", "DG", "E", 3, 2.0, 0.0, 0.0, "P"),
                _pdb_atom(4, "CA", "ALA", "A", 1, 0.0, 1.0, 0.0, "C"),
                _pdb_atom(5, "CA", "CYS", "A", 2, 1.0, 1.0, 0.0, "C"),
                "END\n",
            ]
        )

        strategy = _resolve_pipeline_chain_strategy(
            pdb_text=pdb,
            request_design_chains=None,
            target_fasta_text="",
            target_record=SequenceRecord(id="pdb_chain_E", sequence="XXX"),
            af2_model_preset_requested="auto",
        )

        self.assertEqual(strategy[2], ["A"])
        self.assertEqual(strategy[3], ["A"])
        self.assertEqual(strategy[5], "monomer")

    def test_empty_target_fasta_selects_protein_chain_not_first_atom_chain(
        self,
    ) -> None:
        pdb = "".join(
            [
                _pdb_atom(1, "P", "DA", "E", 1, 0.0, 0.0, 0.0, "P"),
                _pdb_atom(2, "P", "DT", "E", 2, 1.0, 0.0, 0.0, "P"),
                _pdb_atom(3, "P", "DG", "E", 3, 2.0, 0.0, 0.0, "P"),
                _pdb_atom(4, "CA", "ALA", "A", 1, 0.0, 1.0, 0.0, "C"),
                _pdb_atom(5, "CA", "CYS", "A", 2, 1.0, 1.0, 0.0, "C"),
                _pdb_atom(6, "CA", "ASP", "A", 3, 2.0, 1.0, 0.0, "C"),
                _pdb_atom(7, "CA", "GLU", "A", 4, 3.0, 1.0, 0.0, "C"),
                "END\n",
            ]
        )

        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None
            )
            req = PipelineRequest(
                target_fasta="",
                target_pdb=pdb,
                dry_run=True,
                stop_after="design",
                num_seq_per_tier=2,
                conservation_tiers=[0.3],
            )
            res = runner.run(req)
            out = Path(res.output_dir)
            strategy = json.loads(
                (out / "chain_strategy.json").read_text(encoding="utf-8")
            )
            target_fasta = (out / "target.fasta").read_text(encoding="utf-8")
            proteinmpnn = json.loads(
                (out / "tiers" / "30" / "proteinmpnn.json").read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(strategy["auto_selected_design_chains"], ["A"])
            self.assertEqual(strategy["design_chains_used"], ["A"])
            self.assertEqual(strategy["af2_model_preset"], "monomer")
            self.assertIn("\nACDE\n", target_fasta)
            self.assertNotIn("\nXXX\n", target_fasta)
            self.assertEqual(proteinmpnn["request"]["pdb_path_chains"], ["A"])

    def test_proteinmpnn_input_pdb_text_strips_partner_chains_for_monomer_af2(
        self,
    ) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  CYS A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  ASP B   1       0.000   1.000   0.000  1.00 20.00           C\n"
            "ATOM      4  CA  GLU B   2       1.000   1.000   0.000  1.00 20.00           C\n"
            "END\n"
        )

        processed = _proteinmpnn_input_pdb_text(
            pdb,
            design_chains=["A"],
            af2_model_preset="monomer",
        )

        self.assertEqual(
            set(residues_by_chain(processed, only_atom_records=True).keys()), {"A"}
        )
        self.assertNotIn(" B ", processed)

    def test_proteinmpnn_input_pdb_text_keeps_partner_chains_for_multimer_af2(
        self,
    ) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  CYS A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  ASP B   1       0.000   1.000   0.000  1.00 20.00           C\n"
            "ATOM      4  CA  GLU B   2       1.000   1.000   0.000  1.00 20.00           C\n"
            "END\n"
        )

        processed = _proteinmpnn_input_pdb_text(
            pdb,
            design_chains=["A"],
            af2_model_preset="multimer",
        )

        self.assertEqual(
            set(residues_by_chain(processed, only_atom_records=True).keys()), {"A", "B"}
        )
        self.assertIn(" B ", processed)

    def test_pipeline_rejects_unsafe_partial_rerun_when_design_inputs_change(
        self,
    ) -> None:
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None
            )
            run_id = "partial_rerun_guard"
            initial = PipelineRequest(
                target_fasta=">q1\nACDEFGHIK\n",
                target_pdb="",
                dry_run=True,
                stop_after="af2",
                conservation_tiers=[0.3],
            )
            runner.run(initial, run_id=run_id)

            rerun = PipelineRequest(
                target_fasta=">q1\nYYYYYYYYY\n",
                target_pdb="",
                dry_run=True,
                start_from="af2",
                stop_after="af2",
                conservation_tiers=[0.3],
            )
            with self.assertRaises(PipelineInputRequired) as ctx:
                runner.run(rerun, run_id=run_id)
            self.assertIn("target_fasta", str(ctx.exception))
            self.assertIn("start_from='design'", str(ctx.exception))

    def test_pipeline_design_boundary_rerun_refreshes_design_and_msa_fingerprints(
        self,
    ) -> None:
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None
            )
            run_id = "partial_rerun_design"
            initial = PipelineRequest(
                target_fasta=">q1\nACDEFGHIK\n",
                target_pdb="",
                dry_run=True,
                stop_after="design",
                conservation_tiers=[0.3],
            )
            runner.run(initial, run_id=run_id)

            rerun = PipelineRequest(
                target_fasta=">q1\nYYYYYYYYY\n",
                target_pdb="",
                dry_run=True,
                start_from="design",
                stop_after="design",
                conservation_tiers=[0.3],
            )
            res = runner.run(rerun, run_id=run_id)
            out = Path(res.output_dir)

            designs_fasta = (out / "tiers" / "30" / "designs.fasta").read_text(
                encoding="utf-8"
            )
            self.assertIn("YYYYYYYYY", designs_fasta)
            self.assertNotIn("ACDEFGHIK", designs_fasta)

            msa_meta = json.loads(
                (out / "msa" / "request_meta.json").read_text(encoding="utf-8")
            )
            self.assertTrue(str(msa_meta.get("request_hash") or "").strip())

            proteinmpnn_payload = json.loads(
                (out / "tiers" / "30" / "proteinmpnn.json").read_text(encoding="utf-8")
            )
            self.assertTrue(str(proteinmpnn_payload.get("request_hash") or "").strip())
            self.assertTrue(str(proteinmpnn_payload.get("input_hash") or "").strip())

    def test_pipeline_af2_partial_rerun_allows_af2_only_changes(self) -> None:
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None
            )
            run_id = "partial_rerun_af2"
            initial = PipelineRequest(
                target_fasta=">q1\nACDEFGHIK\n",
                target_pdb="",
                dry_run=True,
                stop_after="af2",
                conservation_tiers=[0.3],
                af2_plddt_cutoff=85.0,
            )
            runner.run(initial, run_id=run_id)

            rerun = PipelineRequest(
                target_fasta=">q1\nACDEFGHIK\n",
                target_pdb="",
                dry_run=True,
                start_from="af2",
                stop_after="af2",
                conservation_tiers=[0.3],
                af2_plddt_cutoff=70.0,
            )
            res = runner.run(rerun, run_id=run_id)
            request_payload = json.loads(
                (Path(res.output_dir) / "request.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                float(request_payload.get("af2_plddt_cutoff") or 0.0), 70.0
            )

    def test_pipeline_rerun_same_id_retries_previous_af2_missing_pdb_failures(
        self,
    ) -> None:
        fasta = ">q1\nACDE\n"
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  CYS A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  ASP A   3       2.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      4  CA  GLU A   4       3.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        af2_error = (
            "AlphaFold2 endpoint reported an error:\n"
            "RuntimeError: colabfold_batch completed but no PDB outputs were found. "
            "Check your localcolabfold installation, databases, and colabfold_args."
        )

        class _FailingColabFoldStub:
            def __init__(self) -> None:
                self.calls = 0

            def predict(self, sequences, **kwargs):  # type: ignore[no-untyped-def]
                self.calls += 1
                on_job_id = kwargs.get("on_job_id")
                if callable(on_job_id):
                    for idx, seq in enumerate(sequences, start=1):
                        on_job_id(seq.id, f"failed_job_{self.calls}_{idx}")
                raise RuntimeError(af2_error)

        class _SuccessfulColabFoldStub:
            def __init__(self, ranked_pdb: str) -> None:
                self.calls = 0
                self.resume_history: list[dict[str, str] | None] = []
                self.ranked_pdb = ranked_pdb

            def predict(self, sequences, **kwargs):  # type: ignore[no-untyped-def]
                self.calls += 1
                raw_resume = kwargs.get("resume_job_ids")
                if isinstance(raw_resume, dict):
                    self.resume_history.append(
                        {str(k): str(v) for k, v in raw_resume.items()}
                    )
                else:
                    self.resume_history.append(None)
                on_job_id = kwargs.get("on_job_id")
                if callable(on_job_id):
                    for idx, seq in enumerate(sequences, start=1):
                        on_job_id(seq.id, f"fresh_job_{self.calls}_{idx}")
                return {
                    str(seq.id): {
                        "best_model": "model_1",
                        "best_plddt": 91.0,
                        "ranking_debug": {
                            "order": ["model_1"],
                            "plddts": {"model_1": 91.0},
                        },
                        "ranked_0_pdb": self.ranked_pdb,
                    }
                    for seq in sequences
                }

        req = PipelineRequest(
            target_fasta=fasta,
            target_pdb=pdb,
            dry_run=False,
            stop_after="af2",
            conservation_tiers=[0.3],
            num_seq_per_tier=1,
            soluprot_cutoff=0.0,
            af2_plddt_cutoff=0.0,
            af2_rmsd_cutoff=0.0,
        )

        with _tmpdir() as tmp:
            run_id = "rerun_af2_missing_pdb"
            failing = _FailingColabFoldStub()
            first_runner = PipelineRunner(
                output_root=tmp,
                mmseqs=None,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                colabfold=failing,
            )
            first_runner.run(req, run_id=run_id)

            run_root = Path(tmp) / run_id
            first_wt_metrics = json.loads(
                (run_root / "wt" / "metrics.json").read_text(encoding="utf-8")
            )
            self.assertTrue(bool(((first_wt_metrics.get("af2") or {}).get("skipped"))))
            first_tier_af2 = json.loads(
                (run_root / "tiers" / "30" / "af2_scores.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(bool(first_tier_af2.get("recovered")))

            success = _SuccessfulColabFoldStub(pdb)
            second_runner = PipelineRunner(
                output_root=tmp,
                mmseqs=None,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                colabfold=success,
            )
            second_runner.run(req, run_id=run_id)

            self.assertEqual(success.calls, 2)
            self.assertIsNone(success.resume_history[0])
            self.assertIsNone(success.resume_history[1])

            wt_metrics = json.loads(
                (run_root / "wt" / "metrics.json").read_text(encoding="utf-8")
            )
            self.assertFalse(bool(((wt_metrics.get("af2") or {}).get("skipped"))))
            self.assertTrue((run_root / "wt" / "af2" / "ranked_0.pdb").exists())

            tier_af2 = json.loads(
                (run_root / "tiers" / "30" / "af2_scores.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertFalse(bool(tier_af2.get("recovered")))
            self.assertTrue(bool(tier_af2.get("selected_ids")))

    def test_pipeline_af2_missing_pdb_failure_does_not_recover_entire_tier_when_other_candidates_succeed(
        self,
    ) -> None:
        fasta = ">q1\nACDE\n"
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  CYS A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  ASP A   3       2.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      4  CA  GLU A   4       3.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        af2_error = (
            "AlphaFold2 endpoint reported an error:\n"
            "RuntimeError: colabfold_batch completed but no PDB outputs were found. "
            "Check your localcolabfold installation, databases, and colabfold_args."
        )

        class _PartiallyFailingColabFoldStub:
            def __init__(self, ranked_pdb: str) -> None:
                self.calls: list[list[str]] = []
                self.ranked_pdb = ranked_pdb

            def predict(self, sequences, **kwargs):  # type: ignore[no-untyped-def]
                seq_ids = [str(seq.id) for seq in sequences]
                self.calls.append(seq_ids)
                on_job_id = kwargs.get("on_job_id")
                if callable(on_job_id):
                    for idx, seq_id in enumerate(seq_ids, start=1):
                        on_job_id(seq_id, f"job_{len(self.calls)}_{idx}")
                if any(
                    seq_id.endswith("fallback_001") or seq_id.endswith(":1")
                    for seq_id in seq_ids
                ):
                    raise RuntimeError(af2_error)
                return {
                    seq_id: {
                        "best_model": "model_1",
                        "best_plddt": 91.0,
                        "ranking_debug": {
                            "order": ["model_1"],
                            "plddts": {"model_1": 91.0},
                        },
                        "ranked_0_pdb": self.ranked_pdb,
                    }
                    for seq_id in seq_ids
                }

        req = PipelineRequest(
            target_fasta=fasta,
            target_pdb=pdb,
            dry_run=False,
            stop_after="af2",
            conservation_tiers=[0.3],
            num_seq_per_tier=2,
            soluprot_cutoff=0.0,
            af2_plddt_cutoff=0.0,
            af2_rmsd_cutoff=0.0,
            rfd3_use=False,
        )

        with _tmpdir() as tmp:
            stub = _PartiallyFailingColabFoldStub(pdb)
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=None,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                colabfold=stub,
            )
            res = runner.run(req, run_id="partial_af2_missing_pdb")

            run_root = Path(res.output_dir)
            tier_af2 = json.loads(
                (run_root / "tiers" / "30" / "af2_scores.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(stub.calls[1], ["target:fallback_001"])
            self.assertEqual(stub.calls[2], ["target:fallback_002"])
            self.assertFalse(bool(tier_af2.get("recovered")))
            self.assertTrue(bool(tier_af2.get("selected_ids")))
            self.assertIn("target:fallback_001", tier_af2.get("failed_ids") or [])
            self.assertIn(
                "target:fallback_001", tier_af2.get("prediction_errors") or {}
            )
            self.assertTrue(
                (
                    run_root
                    / "tiers"
                    / "30"
                    / "af2"
                    / "target_fallback_002"
                    / "ranked_0.pdb"
                ).exists()
            )

    def test_pipeline_af2_rmsd_uses_parent_backbone_for_multi_backbone_candidates(
        self,
    ) -> None:
        fasta = ">q1\nAGSY\n"
        target_pdb = _bent_ca_backbone(0.0)
        bioemu_pdb = _bent_ca_backbone(1.5, third_y=0.3, third_z=0.5, tail_y=0.2)

        class _BioEmuStub:
            def sample(self, **kwargs):  # type: ignore[no-untyped-def]
                return {
                    "sample_pdbs": [
                        {
                            "id": "bioemu_000",
                            "frame_index": 0,
                            "pdb": bioemu_pdb,
                        }
                    ]
                }

        class _ColabFoldStub:
            def predict(self, sequences, **kwargs):  # type: ignore[no-untyped-def]
                out = {}
                for seq in sequences:
                    seq_id = str(seq.id)
                    ranked_pdb = (
                        bioemu_pdb if seq_id.startswith("bioemu_000:") else target_pdb
                    )
                    out[seq_id] = {
                        "best_model": "model_1",
                        "best_plddt": 91.0,
                        "ranking_debug": {
                            "order": ["model_1"],
                            "plddts": {"model_1": 91.0},
                        },
                        "ranked_0_pdb": ranked_pdb,
                    }
                return out

        req = PipelineRequest(
            target_fasta=fasta,
            target_pdb=target_pdb,
            dry_run=False,
            bioemu_use=True,
            bioemu_num_samples=1,
            bioemu_max_return_structures=1,
            stop_after="af2",
            conservation_tiers=[0.3],
            num_seq_per_tier=1,
            soluprot_cutoff=0.0,
            af2_plddt_cutoff=0.0,
            af2_rmsd_cutoff=0.0,
        )

        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=None,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                colabfold=_ColabFoldStub(),
                bioemu=_BioEmuStub(),
            )
            res = runner.run(req, run_id="multi_backbone_parent_rmsd")

            tier_af2 = json.loads(
                (Path(res.output_dir) / "tiers" / "30" / "af2_scores.json").read_text(
                    encoding="utf-8"
                )
            )
            rmsd_scores = tier_af2.get("rmsd_scores") or {}
            target_rmsd_scores = tier_af2.get("target_rmsd_scores") or {}

            self.assertAlmostEqual(
                float(rmsd_scores.get("target:fallback_001") or 0.0), 0.0, places=6
            )
            self.assertAlmostEqual(
                float(rmsd_scores.get("bioemu_000:fallback_001") or 0.0), 0.0, places=6
            )
            self.assertGreater(
                float(target_rmsd_scores.get("bioemu_000:fallback_001") or 0.0), 0.1
            )
            self.assertEqual(
                str(tier_af2.get("rmsd_reference_mode") or ""), "parent_backbone"
            )

    def test_pipeline_wt_and_target_rmsd_use_processed_target_reference(self) -> None:
        fasta = ">q1\nAGSY\n"
        raw_target_pdb = (
            "ATOM      1  CA  GLY A  -2       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  SER A  -1       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  ALA A   1       0.000   1.000   0.000  1.00 20.00           C\n"
            "ATOM      4  CA  GLY A   2       1.000   1.000   0.000  1.00 20.00           C\n"
            "ATOM      5  CA  SER A   3       2.000   1.000   0.000  1.00 20.00           C\n"
            "ATOM      6  CA  TYR A   4       3.000   1.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        processed_target_pdb = (
            "ATOM      1  CA  ALA A   1       0.000   1.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   1.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  SER A   3       2.000   1.000   0.000  1.00 20.00           C\n"
            "ATOM      4  CA  TYR A   4       3.000   1.000   0.000  1.00 20.00           C\n"
            "END\n"
        )

        class _BioEmuStub:
            def sample(self, **kwargs):  # type: ignore[no-untyped-def]
                return {
                    "sample_pdbs": [
                        {
                            "id": "bioemu_000",
                            "frame_index": 0,
                            "pdb": processed_target_pdb,
                        }
                    ]
                }

        class _ColabFoldStub:
            def predict(self, sequences, **kwargs):  # type: ignore[no-untyped-def]
                return {
                    str(seq.id): {
                        "best_model": "model_1",
                        "best_plddt": 91.0,
                        "ranking_debug": {
                            "order": ["model_1"],
                            "plddts": {"model_1": 91.0},
                        },
                        "ranked_0_pdb": processed_target_pdb,
                    }
                    for seq in sequences
                }

        req = PipelineRequest(
            target_fasta=fasta,
            target_pdb=raw_target_pdb,
            dry_run=False,
            bioemu_use=True,
            bioemu_num_samples=1,
            bioemu_max_return_structures=1,
            wt_compare=True,
            pdb_strip_nonpositive_resseq=True,
            stop_after="af2",
            conservation_tiers=[0.3],
            num_seq_per_tier=1,
            soluprot_cutoff=0.0,
            af2_plddt_cutoff=0.0,
            af2_rmsd_cutoff=0.0,
        )

        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp,
                mmseqs=None,
                proteinmpnn=None,
                soluprot=None,
                af2=None,
                colabfold=_ColabFoldStub(),
                bioemu=_BioEmuStub(),
            )
            res = runner.run(req, run_id="processed_target_reference")
            out = Path(res.output_dir)

            wt_metrics = json.loads(
                (out / "wt" / "metrics.json").read_text(encoding="utf-8")
            )
            tier_af2 = json.loads(
                (out / "tiers" / "30" / "af2_scores.json").read_text(encoding="utf-8")
            )
            target_rmsd_scores = tier_af2.get("target_rmsd_scores") or {}

            self.assertAlmostEqual(float(wt_metrics["af2"]["rmsd_ca"]), 0.0, places=6)
            self.assertAlmostEqual(
                float(target_rmsd_scores.get("target:fallback_001") or 0.0),
                0.0,
                places=6,
            )
            self.assertAlmostEqual(
                float(target_rmsd_scores.get("bioemu_000:fallback_001") or 0.0),
                0.0,
                places=6,
            )

    def test_pipeline_selected_tiers_limits_outputs_to_requested_tier(self) -> None:
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None
            )
            req = PipelineRequest(
                target_fasta=">q1\nACDEFGHIK\n",
                target_pdb="",
                dry_run=True,
                stop_after="af2",
                conservation_tiers=[0.3, 0.5, 0.7],
                selected_tiers=[0.5],
            )
            res = runner.run(req)
            out = Path(res.output_dir)

            self.assertFalse((out / "tiers" / "30").exists())
            self.assertTrue((out / "tiers" / "50" / "af2_scores.json").exists())
            self.assertFalse((out / "tiers" / "70").exists())

            request_payload = json.loads(
                (out / "request.json").read_text(encoding="utf-8")
            )
            self.assertEqual(request_payload.get("selected_tiers"), [0.5])

    def test_pipeline_partial_rerun_scopes_cleanup_to_selected_tiers(self) -> None:
        with _tmpdir() as tmp:
            runner = PipelineRunner(
                output_root=tmp, mmseqs=None, proteinmpnn=None, soluprot=None, af2=None
            )
            run_id = "partial_rerun_selected_tier"
            initial = PipelineRequest(
                target_fasta=">q1\nACDEFGHIK\n",
                target_pdb="",
                dry_run=True,
                stop_after="af2",
                conservation_tiers=[0.3, 0.5],
            )
            res = runner.run(initial, run_id=run_id)
            out = Path(res.output_dir)

            tier30_novelty = out / "tiers" / "30" / "novelty.json"
            tier50_novelty = out / "tiers" / "50" / "novelty.json"
            tier30_novelty.write_text("keep", encoding="utf-8")
            tier50_novelty.write_text("drop", encoding="utf-8")

            rerun = PipelineRequest(
                target_fasta=">q1\nACDEFGHIK\n",
                target_pdb="",
                dry_run=True,
                start_from="af2",
                stop_after="af2",
                conservation_tiers=[0.3, 0.5],
                selected_tiers=[0.5],
            )
            runner.run(rerun, run_id=run_id)

            self.assertTrue(tier30_novelty.exists())
            self.assertFalse(tier50_novelty.exists())

    def test_clear_stage_outputs_removes_korean_reports_and_wt(self) -> None:
        with _tmpdir() as tmp:
            root = Path(tmp) / "cleanup_case"
            root.mkdir(parents=True, exist_ok=True)
            (root / "report_ko.md").write_text("ko", encoding="utf-8")
            (root / "agent_panel_report_ko.md").write_text("agent-ko", encoding="utf-8")
            (root / "wt").mkdir(parents=True, exist_ok=True)
            removed = _clear_stage_outputs_from(root, start_from="af2")
            self.assertFalse((root / "report_ko.md").exists())
            self.assertFalse((root / "agent_panel_report_ko.md").exists())
            self.assertFalse((root / "wt").exists())
            self.assertIn("report_ko.md", removed)
            self.assertIn("agent_panel_report_ko.md", removed)
            self.assertIn("wt", removed)


if __name__ == "__main__":
    unittest.main()
