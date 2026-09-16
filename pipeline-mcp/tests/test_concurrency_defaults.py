from __future__ import annotations

import ast
from pathlib import Path

from pipeline_mcp import pipeline


REPO_ROOT = Path(__file__).resolve().parents[2]


def _read_int_constant(path: Path, name: str) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            continue
        value = ast.literal_eval(node.value)
        if not isinstance(value, int):
            raise AssertionError(f"{name} in {path} is not an int")
        return value
    raise AssertionError(f"{name} not found in {path}")


def test_pipeline_gpu_http_worker_defaults_are_conservative(monkeypatch):
    monkeypatch.delenv("PIPELINE_AF2_MAX_WORKERS", raising=False)
    monkeypatch.delenv("PIPELINE_RELAX_MAX_WORKERS", raising=False)

    assert getattr(pipeline, "DEFAULT_AF2_MAX_WORKERS", None) == 1
    assert getattr(pipeline, "DEFAULT_RELAX_MAX_WORKERS", None) == 4
    assert (
        pipeline._parallel_worker_limit(
            10,
            env_name="PIPELINE_AF2_MAX_WORKERS",
            default=pipeline.DEFAULT_AF2_MAX_WORKERS,
            hard_cap=12,
        )
        == 1
    )
    assert (
        pipeline._parallel_worker_limit(
            10,
            env_name="PIPELINE_RELAX_MAX_WORKERS",
            default=pipeline.DEFAULT_RELAX_MAX_WORKERS,
            hard_cap=12,
        )
        == 4
    )


def test_cath_batch_defaults_start_with_two_target_workers():
    for relative in (
        "scripts/02_run_cath_batch.py",
    ):
        assert _read_int_constant(REPO_ROOT / relative, "MAX_CONCURRENT_PIPELINES") == 2


def test_cath_ui_default_matches_batch_default():
    html = (REPO_ROOT / "frontend/index.html").read_text(encoding="utf-8")

    assert 'id="cathMaxWorkers" type="number" min="1" step="1" value="2"' in html
