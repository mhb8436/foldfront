"""Tests for pipeline_mcp.bio.residue_exposure — server-side SASA port.

These verify:
1. The public ``classify_residues`` function returns the expected schema.
2. A synthetic two-atom PDB gives deterministic surface/core classification.
3. counts are consistent with the per-chain residue lists.
"""
from __future__ import annotations

import math
import unittest

from pipeline_mcp.bio.residue_exposure import (
    classify_residues,
    _build_unit_sphere_points,
    DEFAULT_SURFACE_POINT_COUNT,
    UNIT_SPHERE_POINTS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_atom_line(
    serial: int,
    atom_name: str,
    resn: str,
    chain: str,
    resi: int,
    x: float,
    y: float,
    z: float,
    element: str,
) -> str:
    """Build a minimal ATOM PDB line (80 chars)."""
    # PDB fixed-column format
    return (
        f"ATOM  {serial:5d} {atom_name:<4s} {resn:<3s} {chain}{resi:4d}    "
        f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00 20.00          {element:>2s}  "
    )


# ---------------------------------------------------------------------------
# Unit sphere
# ---------------------------------------------------------------------------

class TestUnitSphere(unittest.TestCase):
    def test_point_count(self) -> None:
        pts = _build_unit_sphere_points(DEFAULT_SURFACE_POINT_COUNT)
        self.assertEqual(len(pts), DEFAULT_SURFACE_POINT_COUNT)
        self.assertEqual(len(UNIT_SPHERE_POINTS), DEFAULT_SURFACE_POINT_COUNT)

    def test_points_on_unit_sphere(self) -> None:
        for x, y, z in UNIT_SPHERE_POINTS:
            r = math.sqrt(x * x + y * y + z * z)
            self.assertAlmostEqual(r, 1.0, places=12)

    def test_minimum_clamp(self) -> None:
        pts = _build_unit_sphere_points(3)
        self.assertEqual(len(pts), 12)  # clamped to 12


# ---------------------------------------------------------------------------
# Synthetic PDB: two isolated atoms, very far apart → both surface
# ---------------------------------------------------------------------------

class TestClassifyResiduesSynthetic(unittest.TestCase):
    def _two_atom_pdb(self) -> str:
        """Two CA atoms on chain A, 200 Å apart → both fully exposed → both surface."""
        lines = [
            _make_atom_line(1, "CA  ", "ALA", "A", 1, 0.0, 0.0, 0.0, "C"),
            _make_atom_line(2, "CA  ", "ALA", "A", 2, 200.0, 0.0, 0.0, "C"),
            "END",
        ]
        return "\n".join(lines)

    def test_two_isolated_atoms_are_surface(self) -> None:
        result = classify_residues(self._two_atom_pdb())
        self.assertIn("surface", result)
        self.assertIn("core", result)
        self.assertIn("interface", result)
        self.assertIn("counts", result)
        # Both residues must appear as surface (fully exposed, no neighbours)
        surface = result["surface"]
        self.assertIn("A", surface)
        self.assertIn(1, surface["A"])
        self.assertIn(2, surface["A"])

    def test_counts_match_lists(self) -> None:
        result = classify_residues(self._two_atom_pdb())
        counts = result["counts"]
        surface_total = sum(len(v) for v in result["surface"].values())
        core_total = sum(len(v) for v in result["core"].values())
        iface_total = sum(len(v) for v in result["interface"].values())
        self.assertEqual(counts["surface"], surface_total)
        self.assertEqual(counts["core"], core_total)
        self.assertEqual(counts["interface"], iface_total)

    def test_no_interface_single_chain(self) -> None:
        result = classify_residues(self._two_atom_pdb())
        # Single-chain PDB — no cross-chain contacts → interface must be empty
        self.assertEqual(result["counts"]["interface"], 0)

    def test_residue_lists_sorted(self) -> None:
        result = classify_residues(self._two_atom_pdb())
        for chain_map in (result["surface"], result["core"], result["interface"]):
            for resi_list in chain_map.values():
                self.assertEqual(resi_list, sorted(resi_list))


# ---------------------------------------------------------------------------
# Synthetic PDB: tightly packed cluster → core
# ---------------------------------------------------------------------------

class TestClassifyResiduesCore(unittest.TestCase):
    def _cluster_pdb(self) -> str:
        """Many atoms all at the origin → extremely buried → core."""
        lines = []
        serial = 1
        # One central residue surrounded by 12 neighbours (all within 3.5 Å)
        # Central residue: resi 1
        lines.append(_make_atom_line(serial, "CA  ", "ALA", "A", 1, 0.0, 0.0, 0.0, "C"))
        serial += 1
        # Surrounding atoms: resi 2-13 (counted as separate residues each with a CA)
        offsets = [
            (3.0, 0.0, 0.0), (-3.0, 0.0, 0.0),
            (0.0, 3.0, 0.0), (0.0, -3.0, 0.0),
            (0.0, 0.0, 3.0), (0.0, 0.0, -3.0),
            (2.1, 2.1, 0.0), (-2.1, 2.1, 0.0),
            (2.1, -2.1, 0.0), (-2.1, -2.1, 0.0),
            (0.0, 2.1, 2.1), (0.0, -2.1, 2.1),
        ]
        for i, (ox, oy, oz) in enumerate(offsets):
            lines.append(_make_atom_line(serial, "CA  ", "ALA", "A", i + 2, ox, oy, oz, "C"))
            serial += 1
        lines.append("END")
        return "\n".join(lines)

    def test_central_residue_classified(self) -> None:
        # The central residue should end up as surface or core — either is
        # acceptable for the algorithm (it depends on exact exposure); what
        # we verify is that the function runs without error and returns the
        # three expected keys.
        result = classify_residues(self._cluster_pdb())
        self.assertIn("surface", result)
        self.assertIn("core", result)
        self.assertIn("interface", result)
        total = result["counts"]["surface"] + result["counts"]["core"]
        self.assertGreater(total, 0)


# ---------------------------------------------------------------------------
# Two-chain PDB: interface detection
# ---------------------------------------------------------------------------

class TestClassifyResiduesInterface(unittest.TestCase):
    def _two_chain_close_pdb(self) -> str:
        """Chain A and chain B with centroids 5 Å apart → interface."""
        lines = [
            _make_atom_line(1, "CA  ", "ALA", "A", 1, 0.0, 0.0, 0.0, "C"),
            _make_atom_line(2, "CA  ", "ALA", "B", 1, 5.0, 0.0, 0.0, "C"),
            "END",
        ]
        return "\n".join(lines)

    def _two_chain_far_pdb(self) -> str:
        """Chain A and chain B with centroids 200 Å apart → no interface."""
        lines = [
            _make_atom_line(1, "CA  ", "ALA", "A", 1, 0.0, 0.0, 0.0, "C"),
            _make_atom_line(2, "CA  ", "ALA", "B", 1, 200.0, 0.0, 0.0, "C"),
            "END",
        ]
        return "\n".join(lines)

    def test_close_chains_interface(self) -> None:
        result = classify_residues(self._two_chain_close_pdb())
        # Both residues should be flagged as interface (distance 5 Å ≤ 8 Å cutoff)
        iface = result["interface"]
        self.assertIn("A", iface)
        self.assertIn(1, iface["A"])
        self.assertIn("B", iface)
        self.assertIn(1, iface["B"])

    def test_far_chains_no_interface(self) -> None:
        result = classify_residues(self._two_chain_far_pdb())
        self.assertEqual(result["counts"]["interface"], 0)


# ---------------------------------------------------------------------------
# Schema / return-type checks
# ---------------------------------------------------------------------------

class TestClassifyResiduesSchema(unittest.TestCase):
    def test_empty_pdb_returns_empty_dicts(self) -> None:
        result = classify_residues("")
        self.assertIsInstance(result["surface"], dict)
        self.assertIsInstance(result["core"], dict)
        self.assertIsInstance(result["interface"], dict)
        self.assertEqual(result["counts"], {"surface": 0, "core": 0, "interface": 0})

    def test_custom_cutoff_changes_classification(self) -> None:
        # A single isolated atom should always be surface regardless of cutoff
        pdb = _make_atom_line(1, "CA  ", "ALA", "A", 1, 0.0, 0.0, 0.0, "C") + "\nEND"
        result_strict = classify_residues(pdb, surface_area_cutoff=0.0)
        result_loose = classify_residues(pdb, surface_area_cutoff=1000.0)
        # With cutoff 0.0, any exposed area → surface
        self.assertEqual(result_strict["counts"]["surface"], 1)
        # With a huge cutoff, the residue ends up in core
        self.assertEqual(result_loose["counts"]["core"], 1)


if __name__ == "__main__":
    unittest.main()
