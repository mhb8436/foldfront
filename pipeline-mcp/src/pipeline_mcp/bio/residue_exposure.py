"""Server-side residue surface/core/interface classification.

Faithfully ports the client-side Shrake-Rupley SASA algorithm from
frontend/lib/residue-picker.js so that counts match the web app's 3D
picker for the same PDB and cutoffs.
"""
from __future__ import annotations

import math
from typing import Any

# ---------------------------------------------------------------------------
# Constants — match JS exactly
# ---------------------------------------------------------------------------

DEFAULT_SURFACE_AREA_CUTOFF: float = 2.5
DEFAULT_SURFACE_PROBE_RADIUS: float = 1.4
DEFAULT_SURFACE_POINT_COUNT: int = 96
DEFAULT_INTERFACE_DISTANCE: float = 8.0
SURFACE_MAX_NEIGHBORS: int = 3
CORE_MIN_NEIGHBORS: int = 8

PROTEIN_LIKE_HETATM_RESN: frozenset[str] = frozenset({
    "ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY",
    "HIS", "ILE", "LEU", "LYS", "MET", "MSE", "PHE", "PRO",
    "SER", "THR", "TRP", "TYR", "VAL",
})

VDW_RADIUS_BY_ELEMENT: dict[str, float] = {
    "H": 1.2,
    "C": 1.7,
    "N": 1.55,
    "O": 1.52,
    "F": 1.47,
    "P": 1.8,
    "S": 1.8,
    "CL": 1.75,
    "BR": 1.85,
    "I": 1.98,
    "SE": 1.9,
}


# ---------------------------------------------------------------------------
# Unit sphere point generation — port of buildUnitSpherePoints() in JS
# ---------------------------------------------------------------------------

def _build_unit_sphere_points(count: int = DEFAULT_SURFACE_POINT_COUNT) -> list[tuple[float, float, float]]:
    """Golden-spiral uniform sphere sampling — exact port of JS buildUnitSpherePoints()."""
    samples = max(12, int(count))
    points: list[tuple[float, float, float]] = []
    offset = 2.0 / samples
    increment = math.pi * (3.0 - math.sqrt(5.0))
    for index in range(samples):
        y = index * offset - 1.0 + offset / 2.0
        radius = math.sqrt(max(0.0, 1.0 - y * y))
        phi = index * increment
        points.append((math.cos(phi) * radius, y, math.sin(phi) * radius))
    return points


UNIT_SPHERE_POINTS: list[tuple[float, float, float]] = _build_unit_sphere_points()


# ---------------------------------------------------------------------------
# Element / vdW helpers — port of inferAtomElement() / vdwRadiusForElement()
# ---------------------------------------------------------------------------

def _infer_atom_element(line: str, atom_name: str) -> str:
    """Port of JS inferAtomElement(line, atomName)."""
    # columns 76-78 (0-based 76:78)
    explicit = "".join(ch for ch in line[76:78] if ch.isalpha()).upper()
    if explicit:
        return explicit
    fallback = "".join(ch for ch in (atom_name or "") if ch.isalpha()).upper()
    if not fallback:
        return ""
    return fallback[0]


def _vdw_radius_for_element(element: str) -> float:
    """Port of JS vdwRadiusForElement()."""
    normalized = (element or "").strip().upper()
    return VDW_RADIUS_BY_ELEMENT.get(normalized, 1.7)


# ---------------------------------------------------------------------------
# PDB parser — port of parseProteinAtoms()
# ---------------------------------------------------------------------------

def _normalize_chain(chain: str) -> str:
    text = (chain or "").strip().upper()
    return text if text else "_"


def _parse_protein_atoms(pdb_text: str) -> list[dict[str, Any]]:
    """Port of JS parseProteinAtoms().

    Returns list of dicts with keys:
        chain, resi, resn, atomName, element, radius, x, y, z, residueKey
    """
    atoms: list[dict[str, Any]] = []
    for raw_line in pdb_text.splitlines():
        line = raw_line  # keep original for column indexing

        # pad to at least 80 chars so slices never raise
        line_padded = line.ljust(80)

        record = line_padded[0:6].strip().upper()
        if record == "ATOM":
            pass  # include
        elif record == "HETATM":
            resn_check = line_padded[17:20].strip().upper()
            if resn_check not in PROTEIN_LIKE_HETATM_RESN:
                continue
        else:
            continue

        atom_name = line_padded[12:16].strip()
        alt_loc = line_padded[16:17].strip()
        if alt_loc and alt_loc not in ("A", "1"):
            continue

        chain = _normalize_chain(line_padded[21:22])

        try:
            resi = int(line_padded[22:26].strip())
        except (ValueError, IndexError):
            continue

        resn = line_padded[17:20].strip()

        try:
            x = float(line_padded[30:38].strip())
            y = float(line_padded[38:46].strip())
            z = float(line_padded[46:54].strip())
        except (ValueError, IndexError):
            continue

        element = _infer_atom_element(line_padded, atom_name)

        if (not math.isfinite(x) or not math.isfinite(y) or not math.isfinite(z)
                or not element or element == "H"):
            continue

        atoms.append({
            "chain": chain,
            "resi": resi,
            "resn": resn,
            "atomName": atom_name,
            "element": element,
            "radius": _vdw_radius_for_element(element),
            "x": x,
            "y": y,
            "z": z,
            "residueKey": f"{chain}:{resi}",
        })
    return atoms


# ---------------------------------------------------------------------------
# Atom grid — port of buildAtomGrid()
# ---------------------------------------------------------------------------

def _build_atom_grid(atoms: list[dict[str, Any]], cell_size: float) -> dict[str, list[int]]:
    """Port of JS buildAtomGrid()."""
    grid: dict[str, list[int]] = {}
    for index, atom in enumerate(atoms):
        cell_x = int(math.floor(atom["x"] / cell_size))
        cell_y = int(math.floor(atom["y"] / cell_size))
        cell_z = int(math.floor(atom["z"] / cell_size))
        key = f"{cell_x}:{cell_y}:{cell_z}"
        if key not in grid:
            grid[key] = []
        grid[key].append(index)
    return grid


# ---------------------------------------------------------------------------
# SASA calculation — port of estimateAtomExposedAreas()
# ---------------------------------------------------------------------------

def _estimate_atom_exposed_areas(
    atoms: list[dict[str, Any]],
    *,
    probe_radius: float = DEFAULT_SURFACE_PROBE_RADIUS,
    sample_points: list[tuple[float, float, float]] | None = None,
    cell_size: float | None = None,
) -> list[dict[str, Any]]:
    """Port of JS estimateAtomExposedAreas().

    Returns a copy of atoms with an added ``exposedArea`` key.
    """
    if not atoms:
        return []

    if sample_points is None:
        sample_points = UNIT_SPHERE_POINTS

    probe_radius = max(0.0, probe_radius)

    # Add surfaceRadius to each atom
    prepared = [{**a, "surfaceRadius": a["radius"] + probe_radius} for a in atoms]

    max_surface_radius = max(a["surfaceRadius"] for a in prepared)

    if cell_size is None:
        cell_size = max(4.0, max_surface_radius * 2.0 + probe_radius)

    grid = _build_atom_grid(prepared, cell_size)

    result: list[dict[str, Any]] = []
    n_points = len(sample_points)

    for atom_index, atom in enumerate(prepared):
        ax, ay, az = atom["x"], atom["y"], atom["z"]
        asr = atom["surfaceRadius"]

        atom_cell_x = int(math.floor(ax / cell_size))
        atom_cell_y = int(math.floor(ay / cell_size))
        atom_cell_z = int(math.floor(az / cell_size))

        search_radius = max(1, math.ceil((asr + max_surface_radius) / cell_size))

        neighbor_candidates: set[int] = set()
        for dx in range(-search_radius, search_radius + 1):
            for dy in range(-search_radius, search_radius + 1):
                for dz in range(-search_radius, search_radius + 1):
                    key = f"{atom_cell_x + dx}:{atom_cell_y + dy}:{atom_cell_z + dz}"
                    for ci in grid.get(key, []):
                        if ci != atom_index:
                            neighbor_candidates.add(ci)

        # Filter to truly overlapping atoms
        neighbors: list[dict[str, Any]] = []
        for ci in neighbor_candidates:
            nb = prepared[ci]
            ddx = ax - nb["x"]
            ddy = ay - nb["y"]
            ddz = az - nb["z"]
            dist_sq = ddx * ddx + ddy * ddy + ddz * ddz
            interaction_r = asr + nb["surfaceRadius"]
            if dist_sq <= interaction_r * interaction_r:
                neighbors.append(nb)

        # Count accessible sample points
        accessible = 0
        for (ux, uy, uz) in sample_points:
            px = ax + ux * asr
            py = ay + uy * asr
            pz = az + uz * asr
            blocked = False
            for nb in neighbors:
                nbx, nby, nbz = nb["x"], nb["y"], nb["z"]
                nbr = nb["surfaceRadius"]
                pd2 = (px - nbx) ** 2 + (py - nby) ** 2 + (pz - nbz) ** 2
                if pd2 < nbr * nbr - 1e-6:
                    blocked = True
                    break
            if not blocked:
                accessible += 1

        exposed_area = (accessible / n_points) * 4.0 * math.pi * asr * asr
        result.append({**atom, "exposedArea": exposed_area})

    return result


# ---------------------------------------------------------------------------
# Residue aggregation — port of buildResidueExposureEntries()
# ---------------------------------------------------------------------------

def _residue_centroid(
    coords: list[tuple[float, float, float]],
    ca: tuple[float, float, float] | None,
) -> tuple[float, float, float]:
    if ca is not None:
        return ca
    if not coords:
        return (0.0, 0.0, 0.0)
    n = len(coords)
    sx = sum(c[0] for c in coords) / n
    sy = sum(c[1] for c in coords) / n
    sz = sum(c[2] for c in coords) / n
    return (sx, sy, sz)


def _build_residue_exposure_entries(
    atoms: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Port of JS buildResidueExposureEntries()."""
    residue_map: dict[str, dict[str, Any]] = {}

    for atom in atoms:
        key = atom.get("residueKey") or f"{_normalize_chain(atom.get('chain', ''))}"
        if key not in residue_map:
            residue_map[key] = {
                "chain": _normalize_chain(atom.get("chain", "")),
                "resi": atom.get("resi"),
                "resn": (atom.get("resn") or "").strip(),
                "coords": [],
                "ca": None,
                "exposedAreaMax": 0.0,
                "exposedAreaSum": 0.0,
            }
        entry = residue_map[key]
        coord = (atom.get("x", 0.0), atom.get("y", 0.0), atom.get("z", 0.0))
        entry["coords"].append(coord)
        if (atom.get("atomName") or "").strip().upper() == "CA":
            entry["ca"] = coord

        exposed_area = atom.get("exposedArea")
        if exposed_area is not None and math.isfinite(exposed_area):
            entry["exposedAreaMax"] = max(entry["exposedAreaMax"], exposed_area)
            entry["exposedAreaSum"] += exposed_area

    result: list[dict[str, Any]] = []
    for entry in residue_map.values():
        result.append({
            "chain": entry["chain"],
            "resi": entry["resi"],
            "resn": entry["resn"],
            "centroid": _residue_centroid(entry["coords"], entry["ca"]),
            "exposedAreaMax": entry["exposedAreaMax"],
            "exposedAreaSum": entry["exposedAreaSum"],
        })
    return result


# ---------------------------------------------------------------------------
# Interface detection + final classification
# ---------------------------------------------------------------------------

def _distance3(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.sqrt(
        (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2
    )


def _push_residue(
    mapping: dict[str, list[int]], chain: str, resi: int
) -> None:
    """Add resi to mapping[chain] in sorted order (no duplicates)."""
    chain_id = _normalize_chain(chain)
    if chain_id not in mapping:
        mapping[chain_id] = []
    if resi not in mapping[chain_id]:
        mapping[chain_id].append(resi)
        mapping[chain_id].sort()


def _classify_residue_exposure(
    entries: list[dict[str, Any]],
    *,
    surface_area_cutoff: float = DEFAULT_SURFACE_AREA_CUTOFF,
    surface_max_neighbors: int = SURFACE_MAX_NEIGHBORS,
    core_min_neighbors: int = CORE_MIN_NEIGHBORS,
) -> dict[str, dict[str, list[int]]]:
    """Port of JS classifyResidueExposure().

    Returns {"surface": {...}, "core": {...}, "interface": {...}}.
    """
    surface: dict[str, list[int]] = {}
    core: dict[str, list[int]] = {}
    interface: dict[str, list[int]] = {}

    for entry in entries:
        chain = entry["chain"]
        resi = entry["resi"]

        if entry.get("interface"):
            _push_residue(interface, chain, resi)

        exposed_area_max = entry.get("exposedAreaMax")
        exposed_area_sum = entry.get("exposedAreaSum")

        has_area_signal = (
            (exposed_area_max is not None and math.isfinite(exposed_area_max)) or
            (exposed_area_sum is not None and math.isfinite(exposed_area_sum))
        )

        if has_area_signal:
            if (exposed_area_max is not None and math.isfinite(exposed_area_max)):
                area_signal = exposed_area_max
            else:
                area_signal = exposed_area_sum
            if area_signal > surface_area_cutoff:
                _push_residue(surface, chain, resi)
            else:
                _push_residue(core, chain, resi)
            continue

        neighbor_count = entry.get("neighborCount")
        if neighbor_count is not None and math.isfinite(neighbor_count):
            if neighbor_count <= surface_max_neighbors:
                _push_residue(surface, chain, resi)
            elif neighbor_count >= core_min_neighbors:
                _push_residue(core, chain, resi)

    return {"surface": surface, "core": core, "interface": interface}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_residues(
    pdb_text: str,
    *,
    surface_area_cutoff: float = DEFAULT_SURFACE_AREA_CUTOFF,
    probe_radius: float = DEFAULT_SURFACE_PROBE_RADIUS,
    surface_max_neighbors: int = SURFACE_MAX_NEIGHBORS,
    core_min_neighbors: int = CORE_MIN_NEIGHBORS,
    interface_distance: float = DEFAULT_INTERFACE_DISTANCE,
) -> dict[str, Any]:
    """Classify residues as surface / core / interface.

    Matches the web app's 3D residue picker exactly (same algorithm, same
    constants, same unit-sphere sampling).

    Parameters
    ----------
    pdb_text:
        Raw PDB file content (ATOM / HETATM records).
    surface_area_cutoff:
        Exposed-area threshold (Å²) above which a residue is surface.
    probe_radius:
        Solvent probe radius (Å) for SASA.
    surface_max_neighbors:
        Fallback: residues with ≤ this many spatial neighbours → surface.
    core_min_neighbors:
        Fallback: residues with ≥ this many spatial neighbours → core.
    interface_distance:
        Cross-chain centroid distance (Å) at or below which a residue is
        flagged as interface.

    Returns
    -------
    dict with keys:
        ``surface``   – {chain: [resi, …]} sorted ascending
        ``core``      – {chain: [resi, …]}
        ``interface`` – {chain: [resi, …]}
        ``counts``    – {"surface": N, "core": N, "interface": N}
    """
    # Parse atoms
    atoms = _parse_protein_atoms(pdb_text)

    # SASA
    atoms_with_sasa = _estimate_atom_exposed_areas(
        atoms,
        probe_radius=probe_radius,
    )

    # Aggregate to per-residue
    residues = _build_residue_exposure_entries(atoms_with_sasa)

    # Interface detection — port of JS deriveResidueSpatialPresets() loop
    interface_dist = max(3.0, float(interface_distance))
    exposure: list[dict[str, Any]] = []
    for idx, entry in enumerate(residues):
        interface_hit = False
        for other_idx, other in enumerate(residues):
            if idx == other_idx:
                continue
            if entry["chain"] != other["chain"]:
                dist = _distance3(entry["centroid"], other["centroid"])
                if dist <= interface_dist:
                    interface_hit = True
                    break
        exposure.append({
            **entry,
            "interface": interface_hit,
        })

    # Classify
    classification = _classify_residue_exposure(
        exposure,
        surface_area_cutoff=surface_area_cutoff,
        surface_max_neighbors=surface_max_neighbors,
        core_min_neighbors=core_min_neighbors,
    )

    surface = classification["surface"]
    core = classification["core"]
    iface = classification["interface"]

    counts = {
        "surface": sum(len(v) for v in surface.values()),
        "core": sum(len(v) for v in core.values()),
        "interface": sum(len(v) for v in iface.values()),
    }

    return {
        "surface": surface,
        "core": core,
        "interface": iface,
        "counts": counts,
    }
