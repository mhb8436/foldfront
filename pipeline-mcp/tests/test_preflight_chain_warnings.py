from __future__ import annotations

from types import SimpleNamespace

from pipeline_mcp.models import PipelineRequest
from pipeline_mcp.preflight import preflight_request


def _runner():
    # Stub every model client preflight may probe; None = "not configured".
    return SimpleNamespace(
        af2=None, bioemu=None, colabfold=None, diffdock=None, mmseqs=None,
        proteinmpnn=None, rfd3=None, rosetta_relax=None, soluprot=None,
        output_root="/tmp",
    )


_TWO_CHAIN = (
    "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C\n"
    "ATOM      2  CA  GLY A   2       3.800   0.000   0.000  1.00  0.00           C\n"
    "ATOM      3  CA  LEU A   3       7.600   0.000   0.000  1.00  0.00           C\n"
    "ATOM      4  CA  ALA B   1       0.000   3.800   0.000  1.00  0.00           C\n"
    "ATOM      5  CA  GLY B   2       3.800   3.800   0.000  1.00  0.00           C\n"
    "ATOM      6  CA  LEU B   3       7.600   3.800   0.000  1.00  0.00           C\n"
    "END\n"
)

_TWO_MODEL = (
    "MODEL        1\n"
    "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00           C\n"
    "ATOM      2  CA  GLY A   2       3.800   0.000   0.000  1.00  0.00           C\n"
    "ENDMDL\n"
    "MODEL        2\n"
    "ATOM      1  CA  ALA A   1       0.100   0.000   0.000  1.00  0.00           C\n"
    "ATOM      2  CA  GLY A   2       3.900   0.000   0.000  1.00  0.00           C\n"
    "ENDMDL\n"
    "END\n"
)


def test_preflight_warns_on_multichain_without_design_chains():
    req = PipelineRequest(target_fasta="", target_pdb=_TWO_CHAIN, stop_after="msa")
    out = preflight_request(req, _runner())
    warns = " ".join(out.get("warnings", []))
    assert "2 chains" in warns and "design_chains" in warns


def test_preflight_no_multichain_warning_when_design_chains_set():
    req = PipelineRequest(target_fasta="", target_pdb=_TWO_CHAIN, design_chains=["A"], stop_after="msa")
    out = preflight_request(req, _runner())
    warns = " ".join(out.get("warnings", []))
    assert "chains)" not in warns or "auto-selected" not in warns


def test_preflight_warns_on_multimodel_nmr():
    req = PipelineRequest(target_fasta="", target_pdb=_TWO_MODEL, stop_after="msa")
    out = preflight_request(req, _runner())
    warns = " ".join(out.get("warnings", []))
    assert "2 models" in warns


def test_strip_to_first_model_keeps_only_model_1():
    from pipeline_mcp.bio.pdb import strip_to_first_model, residues_by_chain
    out = strip_to_first_model(_TWO_MODEL)
    # only model 1's atoms remain -> chain A has 2 residues, not 4
    res = residues_by_chain(out)
    assert len(res.get("A", [])) == 2
    assert out.count("ATOM") == 2


def test_strip_to_first_model_passthrough_single_model():
    from pipeline_mcp.bio.pdb import strip_to_first_model
    assert strip_to_first_model(_TWO_CHAIN) == _TWO_CHAIN
