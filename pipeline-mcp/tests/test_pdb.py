import unittest

from pipeline_mcp.bio.pdb import ca_rmsd
from pipeline_mcp.bio.pdb import dssp_non_loop_positions_by_chain
from pipeline_mcp.bio.pdb import ligand_atoms_present
from pipeline_mcp.bio.pdb import ligand_proximity_mask
from pipeline_mcp.bio.pdb import mmcif_to_pdb
from pipeline_mcp.bio.pdb import preprocess_pdb
from pipeline_mcp.bio.pdb import sequence_by_chain
from pipeline_mcp.bio.sdf import append_ligand_pdb
from pipeline_mcp.bio.sdf import sdf_to_pdb


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


def _shift_residue_backbone(pdb_text: str, offsets: dict[int, tuple[float, float, float]]) -> str:
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


class TestPdbLigandMask(unittest.TestCase):
    def test_ligand_mask_distance(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2      20.000   0.000   0.000  1.00 20.00           C\n"
            "HETATM    3  C1  LIG A 100       0.000   0.000   5.000  1.00 20.00           C\n"
            "END\n"
        )
        mask = ligand_proximity_mask(pdb, chains=["A"], distance_angstrom=6.0)
        self.assertEqual(mask, {"A": [1]})

    def test_ligand_resname_filter(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "HETATM    3  C1  LIG A 100       0.000   0.000   5.000  1.00 20.00           C\n"
            "END\n"
        )
        mask = ligand_proximity_mask(pdb, chains=["A"], distance_angstrom=6.0, ligand_resnames=["XXX"])
        self.assertEqual(mask, {"A": []})

    def test_atom_chain_as_ligand(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2      20.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  GLY B   1       0.000   0.000   5.000  1.00 20.00           C\n"
            "END\n"
        )
        mask = ligand_proximity_mask(pdb, chains=["A"], distance_angstrom=6.0, ligand_atom_chains=["B"])
        self.assertEqual(mask, {"A": [1]})

    def test_atom_chain_overlap_with_masked_chain_is_ignored(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2      20.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        mask = ligand_proximity_mask(pdb, chains=["A"], distance_angstrom=6.0, ligand_atom_chains=["A"])
        self.assertEqual(mask, {"A": []})

    def test_ligand_atoms_present(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "HETATM    2  C1  LIG A 100       0.000   0.000   5.000  1.00 20.00           C\n"
            "END\n"
        )
        self.assertTrue(ligand_atoms_present(pdb, chains=["A"]))

    def test_ligand_atoms_present_from_atom_chain(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY B   1       0.000   0.000   5.000  1.00 20.00           C\n"
            "END\n"
        )
        self.assertTrue(ligand_atoms_present(pdb, chains=["A"], ligand_atom_chains=["B"]))


class TestSdfHelpers(unittest.TestCase):
    def test_sdf_to_pdb(self) -> None:
        sdf = (
            "test\n"
            "  test\n"
            "\n"
            "  1  0  0  0  0  0  0  0  0  0  0  0 V2000\n"
            "    1.0000    2.0000    3.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
            "M  END\n"
            "$$$$\n"
        )
        pdb = sdf_to_pdb(sdf)
        self.assertIn("HETATM", pdb)
        self.assertIn("LIG", pdb)

    def test_append_ligand_pdb(self) -> None:
        protein = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        ligand = (
            "HETATM    2  C1  LIG Z   1       1.000   2.000   3.000  1.00 20.00           C\n"
            "END\n"
        )
        combined = append_ligand_pdb(protein, ligand)
        self.assertIn("ATOM      1", combined)
        self.assertIn("HETATM    2", combined)
        self.assertTrue(combined.strip().endswith("END"))


class TestPdbSequenceExtraction(unittest.TestCase):
    def test_sequence_by_chain_from_atom_records(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "HETATM    3  C1  LIG A 100       0.000   0.000   5.000  1.00 20.00           C\n"
            "END\n"
        )
        seqs = sequence_by_chain(pdb)
        self.assertEqual(seqs, {"A": "AG"})

    def test_mmcif_to_pdb_supports_target_sequence_extraction(self) -> None:
        cif = """data_demo
#
loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_alt_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_entity_id
_atom_site.label_seq_id
_atom_site.pdbx_PDB_ins_code
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.occupancy
_atom_site.B_iso_or_equiv
_atom_site.pdbx_formal_charge
_atom_site.auth_seq_id
_atom_site.auth_comp_id
_atom_site.auth_asym_id
_atom_site.auth_atom_id
_atom_site.pdbx_PDB_model_num
ATOM 1 C CA . ALA A 1 1 ? 0.000 0.000 0.000 1.00 20.00 ? 1 ALA A CA 1
ATOM 2 C CA . CYS A 1 2 ? 1.000 0.000 0.000 1.00 20.00 ? 2 CYS A CA 1
#
"""
        pdb = mmcif_to_pdb(cif)
        self.assertIn("ATOM", pdb)
        self.assertEqual(sequence_by_chain(pdb), {"A": "AC"})


class TestPdbPreprocess(unittest.TestCase):
    def test_strips_nonpositive_resseq(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A  -1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   1       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  SER A   2       2.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        out, mapping = preprocess_pdb(pdb, chains=["A"], strip_nonpositive_resseq=True, renumber_resseq_from_1=False)
        self.assertNotIn("  -1", out)
        self.assertIn("   1", out)
        self.assertIn("   2", out)
        self.assertEqual(
            mapping,
            {
                "A": [
                    {"index": 1, "original_resseq": 1, "original_icode": "", "processed_resseq": 1, "processed_icode": ""},
                    {"index": 2, "original_resseq": 2, "original_icode": "", "processed_resseq": 2, "processed_icode": ""},
                ]
            },
        )

    def test_renumbers_selected_chains_only(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A  10       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A  20       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  ALA B  10       0.000   1.000   0.000  1.00 20.00           C\n"
            "ATOM      4  CA  GLY B  20       1.000   1.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        out, mapping = preprocess_pdb(pdb, chains=["A"], strip_nonpositive_resseq=False, renumber_resseq_from_1=True)
        self.assertIn("A   1", out)
        self.assertIn("A   2", out)
        self.assertIn("B  10", out)
        self.assertIn("B  20", out)
        self.assertEqual(
            mapping.get("A"),
            [
                {"index": 1, "original_resseq": 10, "original_icode": "", "processed_resseq": 1, "processed_icode": ""},
                {"index": 2, "original_resseq": 20, "original_icode": "", "processed_resseq": 2, "processed_icode": ""},
            ],
        )


class TestCaRmsd(unittest.TestCase):
    def test_ca_rmsd_two_residue_identical_backbones(self) -> None:
        pdb_ref = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  ALA A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        pdb_mob = (
            "ATOM      1  CA  ALA A   1      10.000   5.000  -3.000  1.00 20.00           C\n"
            "ATOM      2  CA  ALA A   2      11.000   5.000  -3.000  1.00 20.00           C\n"
            "END\n"
        )
        rmsd = ca_rmsd(pdb_ref, pdb_mob)
        self.assertAlmostEqual(rmsd, 0.0, places=5)

    def test_ca_rmsd_subset_matching(self) -> None:
        # Reference: Residues 10-12
        pdb_ref = (
            "ATOM      1  CA  ALA A  10       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  ALA A  11       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  ALA A  12       2.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        # Mobile: Residues 1-12. Residues 10-12 are identical to ref.
        pdb_mob = (
            "ATOM      1  CA  GLY A   1      10.000  10.000  10.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2      11.000  10.000  10.000  1.00 20.00           C\n"
            "ATOM      3  CA  GLY A   3      12.000  10.000  10.000  1.00 20.00           C\n"
            "ATOM     10  CA  ALA A  10       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM     11  CA  ALA A  11       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM     12  CA  ALA A  12       2.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        rmsd = ca_rmsd(pdb_ref, pdb_mob)
        self.assertAlmostEqual(rmsd, 0.0, places=5)

    def test_ca_rmsd_uses_mean_over_matched_atoms(self) -> None:
        pdb_ref = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       2.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  SER A   3       0.000   2.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        pdb_mob = (
            "ATOM      1  CA  ALA A   1       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       3.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  SER A   3       1.000   3.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        pdb_ref_duplicated = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       2.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  SER A   3       0.000   2.000   0.000  1.00 20.00           C\n"
            "ATOM      4  CA  ALA A   4       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      5  CA  GLY A   5       2.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      6  CA  SER A   6       0.000   2.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        pdb_mob_duplicated = (
            "ATOM      1  CA  ALA A   1       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       3.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      3  CA  SER A   3       1.000   3.000   0.000  1.00 20.00           C\n"
            "ATOM      4  CA  ALA A   4       1.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      5  CA  GLY A   5       3.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      6  CA  SER A   6       1.000   3.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        rmsd = ca_rmsd(pdb_ref, pdb_mob)
        duplicated_rmsd = ca_rmsd(pdb_ref_duplicated, pdb_mob_duplicated)
        self.assertGreater(rmsd, 0.0)
        self.assertAlmostEqual(duplicated_rmsd, rmsd, places=6)

    def test_ca_rmsd_mismatching_resseq(self) -> None:
        # Different shapes but same indices if matched by position
        pdb_ref = (
            "ATOM      1  CA  ALA A  10       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  ALA A  11       1.000   1.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        pdb_mob = (
            "ATOM      1  CA  GLY A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "ATOM      2  CA  GLY A   2       1.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        # Should return None because no resseq match
        rmsd = ca_rmsd(pdb_ref, pdb_mob)
        self.assertIsNone(rmsd)

    def test_ca_rmsd_single_matched_residue_returns_none(self) -> None:
        pdb_ref = (
            "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n"
            "END\n"
        )
        pdb_mob = (
            "ATOM      1  CA  ALA A   1       5.000   5.000   5.000  1.00 20.00           C\n"
            "END\n"
        )
        rmsd = ca_rmsd(pdb_ref, pdb_mob)
        self.assertIsNone(rmsd)

    def test_dssp_non_loop_positions_identify_reference_helix_core(self) -> None:
        positions = dssp_non_loop_positions_by_chain(_DSSP_REFERENCE_FRAGMENT, chains=["A"])
        chain_positions = positions.get("A") or set()
        self.assertTrue({(12, ""), (13, ""), (14, ""), (15, "")}.issubset(chain_positions))
        self.assertNotIn((8, ""), chain_positions)
        self.assertNotIn((9, ""), chain_positions)
        self.assertNotIn((10, ""), chain_positions)
        self.assertNotIn((17, ""), chain_positions)

    def test_ca_rmsd_can_exclude_loop_positions_using_dssp_mask(self) -> None:
        pdb_mob = _shift_residue_backbone(
            _DSSP_REFERENCE_FRAGMENT,
            {
                8: (11.0, 0.0, 0.0),
                9: (0.0, 11.0, 0.0),
                10: (0.0, 0.0, 11.0),
                17: (-11.0, 7.0, 0.0),
            },
        )
        unmasked = ca_rmsd(_DSSP_REFERENCE_FRAGMENT, pdb_mob, chains=["A"])
        mask = dssp_non_loop_positions_by_chain(_DSSP_REFERENCE_FRAGMENT, chains=["A"])
        masked = ca_rmsd(
            _DSSP_REFERENCE_FRAGMENT,
            pdb_mob,
            chains=["A"],
            include_positions=mask,
        )
        self.assertIsNotNone(unmasked)
        self.assertIsNotNone(masked)
        self.assertGreater(float(unmasked or 0.0), 2.0)
        self.assertLess(float(masked), 0.2)


if __name__ == "__main__":
    unittest.main()
