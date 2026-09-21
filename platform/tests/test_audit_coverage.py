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
    "paper_constraints": "Reads a PDF and asks the LLM for suggestions; stores nothing",
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


# ---------------------------------------------------------------- 읽기 경로

#  조회 전부를 남기지는 않는다. 실행 목록을 한 번 보는 것까지 적으면 기록이
#  화면 갱신으로 가득 차고, 정작 봐야 할 줄이 묻힌다. 남겨야 하는 것은
#  **설치 밖으로 무언가가 나간 경우**다 — 설계 서열과 예측 구조가 담긴 파일.
MUST_RECORD_READS: dict[str, str] = {
    "artifact_content": "산출물 파일을 내보낸다. 무엇이 설치 밖으로 나갔는지가 감사의 본령이다",
}

#  실행 단위 접근 범위를 검사해야 하는 조회. 남의 실행을 읽을 수 있으면
#  역할만 맞으면 누구나 남의 설계를 본다.
MUST_SCOPE_READS: tuple[str, ...] = (
    "get_run", "list_events", "list_artifacts", "artifact_content",
)


def _read_endpoints() -> dict[str, ast.AsyncFunctionDef]:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    found: dict[str, ast.AsyncFunctionDef] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for dec in node.decorator_list:
            call = dec if isinstance(dec, ast.Call) else None
            func = call.func if call else None
            if isinstance(func, ast.Attribute) and func.attr == "get":
                found[node.name] = node
    return found


def _guarded(fn: ast.AsyncFunctionDef) -> bool:
    """Whether the decorator states a role requirement at all."""
    for dec in fn.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        for kw in dec.keywords:
            if kw.arg == "dependencies":
                return True
    return False


def _checks_scope(fn: ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(n, ast.Name) and n.id == "may_see_run" for n in ast.walk(fn)
    )


def test_조회_경로를_찾아낸다():
    assert len(_read_endpoints()) >= 15


@pytest.mark.parametrize("name", sorted(_read_endpoints()))
def test_모든_조회_경로가_로그인을_요구한다(name: str):
    """인증 의존성이 하나도 없던 자리다.

    OIDC 를 켜도 이 경로들은 포트에 닿는 누구에게나 답했다. 역할은 가리지
    않더라도, 누군가이긴 해야 한다 — 거절이 존재하기 위해서, 그리고 감사에
    적을 이름이 있기 위해서.
    """
    assert _guarded(_read_endpoints()[name]), (
        f"{name} 은 아무 인증도 요구하지 않는다."
    )


@pytest.mark.parametrize("name", sorted(MUST_RECORD_READS))
def test_내보내는_조회는_기록을_남긴다(name: str):
    fn = _read_endpoints().get(name)
    assert fn is not None, f"{name} 경로가 사라졌다. 이름이 바뀌었으면 목록을 고친다."
    assert _records(fn), f"{name} 은 감사 기록을 남기지 않는다 — {MUST_RECORD_READS[name]}"


@pytest.mark.parametrize("name", MUST_SCOPE_READS)
def test_실행을_읽는_조회는_접근_범위를_본다(name: str):
    fn = _read_endpoints().get(name)
    assert fn is not None, f"{name} 경로가 사라졌다."
    assert _checks_scope(fn), (
        f"{name} 이 실행 접근 범위를 보지 않는다. 역할만 맞으면 남의 설계를 읽는다."
    )


def test_덮인_조회_경로의_수를_센다():
    """작업 지시서의 완료 판정 — 몇 개 경로가 덮이는지 센다.

    2026. 9. 21. 실측 — 조회 경로 전부가 로그인을 요구하고, 그 가운데
    실행을 읽는 4개가 접근 범위를, 파일을 내보내는 1개가 감사를 남긴다.
    """
    reads = _read_endpoints()
    guarded = [n for n, fn in reads.items() if _guarded(fn)]
    scoped = [n for n, fn in reads.items() if _checks_scope(fn)]
    recorded = [n for n, fn in reads.items() if _records(fn)]

    assert len(guarded) == len(reads), sorted(set(reads) - set(guarded))
    assert len(scoped) >= 4, sorted(scoped)
    assert len(recorded) >= 1, sorted(recorded)
