from __future__ import annotations

from dataclasses import dataclass
import re


_HEADER_ID_RE = re.compile(r"^(\S+)")


@dataclass(frozen=True)
class FastaRecord:
    header: str
    sequence: str

    @property
    def id(self) -> str:
        h = self.header.strip()
        if not h:
            return "seq"

        # UniProt standard format: ">db|accession|name ..." where db ∈ {sp, tr}.
        # The first pipe-segment alone ("sp" or "tr") is not unique across entries,
        # so use db_accession to keep IDs distinct.
        pipe_parts = h.split("|")
        first_pipe = pipe_parts[0].strip().lower()
        if first_pipe in ("sp", "tr") and len(pipe_parts) >= 2:
            accession = pipe_parts[1].split()[0].strip()
            if accession:
                return f"{first_pipe}_{accession}"

        # Take everything before the first pipe
        base = h.split("|", 1)[0].strip()

        # Split into first word and rest
        parts = base.split(None, 1)
        first = parts[0]

        # If the first word contains '=' and the base contains 'sample=',
        # it's likely a ProteinMPNN-style parameter list header without a leading ID.
        # We keep the spaces in the base part to ensure uniqueness.
        if "=" in first and "sample=" in base:
            return base

        # Otherwise, follow standard: stop at first space
        return first


def parse_fasta(text: str) -> list[FastaRecord]:
    records: list[FastaRecord] = []
    header: str | None = None
    seq_parts: list[str] = []

    def flush() -> None:
        nonlocal header, seq_parts
        if header is None:
            return
        seq = "".join(seq_parts).replace(" ", "").replace("\t", "").strip()
        if not seq:
            raise ValueError(f"Empty FASTA sequence for header: {header!r}")
        records.append(FastaRecord(header=header, sequence=seq))
        header = None
        seq_parts = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(">"):
            flush()
            header = line[1:].strip()
            continue
        if header is None:
            raise ValueError("Invalid FASTA: sequence line before header")
        seq_parts.append(line)
    flush()

    if not records:
        raise ValueError("No FASTA records found")
    return records


def to_fasta(records: list[FastaRecord]) -> str:
    lines: list[str] = []
    for rec in records:
        header = rec.header.replace("\n", " ").strip()
        seq = rec.sequence.replace("\n", "").strip()
        lines.append(f">{header}")
        lines.append(seq)
    return "\n".join(lines) + "\n"

