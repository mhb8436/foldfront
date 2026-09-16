"""What is removed, and when.

Uploaded inputs are the only thing the platform writes on someone's behalf
without a run to own them. A run keeps its own inputs - they are how it was
reproduced - so retention is only ever about files nothing read.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from foldfront.core.config import get_settings
from foldfront.db.models import utcnow
from foldfront.db.repositories import Repos


async def prune_inputs(repos: Repos, *, days: int | None = None, limit: int = 1000) -> dict[str, Any]:
    """Remove uploaded files older than `days` that no run has read.

    The record goes when the file goes, and a file already gone is simply
    forgotten. Nothing here touches a file a run named.
    """
    days = get_settings().input_retention_days if days is None else days
    cutoff = utcnow() - timedelta(days=days)
    removed: list[dict[str, Any]] = []
    freed = 0
    for item in await repos.inputs.prunable(older_than=cutoff, limit=limit):
        path = Path(item.path)
        if path.is_file():
            path.unlink()
            freed += item.size_bytes
        await repos.inputs.forget(item.input_id)
        removed.append({"input_id": item.input_id, "name": item.name, "owner_id": item.owner_id})
    return {"days": days, "removed": len(removed), "freed_bytes": freed, "items": removed}
