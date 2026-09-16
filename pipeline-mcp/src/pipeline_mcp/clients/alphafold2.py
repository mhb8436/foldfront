from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from ..models import SequenceRecord


@dataclass(frozen=True)
class AlphaFold2Client:
    url: str
    timeout_s: float = 60.0 * 30

    def predict(
        self,
        sequences: list[SequenceRecord],
        *,
        model_preset: str = "monomer",
        db_preset: str = "full_dbs",
        max_template_date: str = "2020-05-14",
        extra_flags: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            # Backward compatible keys (some services expect "preset")
            "preset": model_preset,
            "model_preset": model_preset,
            "db_preset": db_preset,
            "max_template_date": max_template_date,
            "alphafold_extra_flags": extra_flags,
            "sequences": [{"id": s.id, "sequence": s.sequence} for s in sequences],
        }
        r = requests.post(self.url, json=payload, timeout=self.timeout_s)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"AF2 response invalid: {data!r}")
        return data
