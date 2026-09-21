"""Which actions leave a trace.

An audit trail is only worth having if it is complete. The gap it is meant to
close is the quiet one: a write path added later that records nothing, which
nobody notices because the trail still looks full of other things.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from foldfront.api import routes

SOURCE = Path(inspect.getfile(routes))

#  Write paths that deliberately leave no record, with the reason. Anything
#  else must record, and a new one has to be argued for here.
EXEMPT: dict[str, str] = {
    "complete_node": "Called by a worker for every node; the run's own events already carry it",
    "lease_job": "Called continuously by every worker; recording each would drown the trail",
    "preflight": "Reads and validates, changes nothing",
    "start_run": "ExecutionService.start records run.create one layer down",
    "start_forked_run": "ExecutionService.resume records run.start one layer down",
    "pause_run": "ExecutionService.pause records run.pause one layer down",
    "resume_run": "ExecutionService.unpause records run.resume one layer down",
    "review_checkpoint": "ExecutionService.decide_checkpoint records run.review one layer down",
    "rerun_stage": "ExecutionService.rerun_stage records run.rerun one layer down",
    "copilot_chat": "Reads and answers; changes nothing",
}


def _write_endpoints() -> dict[str, ast.AsyncFunctionDef]:
    """Functions decorated with @router.post, by name."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    found: dict[str, ast.AsyncFunctionDef] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for dec in node.decorator_list:
            call = dec if isinstance(dec, ast.Call) else None
            func = call.func if call else None
            if isinstance(func, ast.Attribute) and func.attr == "post":
                found[node.name] = node
    return found


def _records(fn: ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(n, ast.Attribute) and n.attr == "record" for n in ast.walk(fn)
    )


def test_쓰기_경로를_찾아낸다():
    """If this finds nothing the rest of the file is vacuously true."""
    assert len(_write_endpoints()) >= 10


@pytest.mark.parametrize("name", sorted(_write_endpoints()))
def test_모든_쓰기_경로가_기록을_남긴다(name: str):
    if name in EXEMPT:
        pytest.skip(EXEMPT[name])

    assert _records(_write_endpoints()[name]), (
        f"{name} 은 감사 기록을 남기지 않는다. "
        "남기거나, 남기지 않는 이유를 EXEMPT 에 적는다."
    )


@pytest.mark.parametrize("name", sorted(set(_write_endpoints()) - set(EXEMPT)))
def test_행위자를_검증된_신원에서_가져온다(name: str):
    """A caller that names itself in the body is not evidence of anything."""
    fn = _write_endpoints()[name]
    args = [a.arg for a in fn.args.args] + [a.arg for a in fn.args.kwonlyargs]

    assert "actor_id" not in args, f"{name} 은 행위자를 요청에서 받는다"
    assert "identity" in args or any(
        isinstance(n, ast.Attribute) and n.attr == "owner_id" for n in ast.walk(fn)
    ), f"{name} 은 검증된 신원을 쓰지 않는다"


def test_면제_목록이_실재하는_경로만_담는다():
    """A stale exemption would silently excuse a path that no longer exists."""
    assert set(EXEMPT) <= set(_write_endpoints())
