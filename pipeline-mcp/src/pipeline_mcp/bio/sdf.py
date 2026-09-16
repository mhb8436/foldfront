from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SdfAtom:
    x: float
    y: float
    z: float
    element: str


def _parse_int(value: str, default: int = 0) -> int:
    try:
        return int(value.strip())
    except Exception:
        return default


def _parse_float(value: str, default: float = 0.0) -> float:
    try:
        return float(value.strip())
    except Exception:
        return default


def _parse_counts_line(line: str) -> int:
    parts = line.split()
    if len(parts) >= 1 and parts[0].isdigit():
        return _parse_int(parts[0], 0)
    if len(line) >= 3:
        return _parse_int(line[:3], 0)
    return 0


def parse_sdf_atoms(sdf_text: str) -> list[SdfAtom]:
    block = str(sdf_text or "").split("$$$$", 1)[0]
    lines = [line.rstrip("\n") for line in block.splitlines()]
    if len(lines) < 4:
        raise ValueError("SDF parse failed: header too short")
    n_atoms = _parse_counts_line(lines[3])
    if n_atoms <= 0:
        raise ValueError("SDF parse failed: atom count missing")

    atoms: list[SdfAtom] = []
    start = 4
    for raw in lines[start : start + n_atoms]:
        parts = raw.split()
        if len(parts) < 4:
            continue
        x = _parse_float(parts[0])
        y = _parse_float(parts[1])
        z = _parse_float(parts[2])
        element = str(parts[3]).strip().upper()
        if not element:
            continue
        atoms.append(SdfAtom(x=x, y=y, z=z, element=element))

    if not atoms:
        raise ValueError("SDF parse failed: no atoms found")
    return atoms


def sdf_to_pdb(
    sdf_text: str,
    *,
    resname: str = "LIG",
    chain_id: str = "Z",
    resseq: int = 1,
) -> str:
    atoms = parse_sdf_atoms(sdf_text)
    lines: list[str] = []
    serial = 1
    for atom in atoms:
        element = (atom.element or "C").upper()
        atom_name = element
        lines.append(
            f"HETATM{serial:5d} {atom_name:>4s} {resname:>3s} {chain_id}{int(resseq):4d}    "
            f"{atom.x:8.3f}{atom.y:8.3f}{atom.z:8.3f}  1.00 20.00           {element:>2s}"
        )
        serial += 1
    lines.append("END")
    return "\n".join(lines) + "\n"


def append_ligand_pdb(protein_pdb: str, ligand_pdb: str) -> str:
    protein_lines = [
        line
        for line in str(protein_pdb or "").splitlines()
        if line.strip().upper() not in {"END", "ENDMDL"}
    ]
    ligand_lines = [
        line
        for line in str(ligand_pdb or "").splitlines()
        if line.strip().upper() not in {"END", "ENDMDL"}
    ]
    out = protein_lines + ligand_lines + ["END"]
    return "\n".join(out) + "\n"
