"""API error codes and their messages.

An error travels as a **code**, not as a sentence. The code is what a caller
branches on and what stays stable across releases; the sentence is chosen from
the catalogue below according to the request's Accept-Language.

    raise ApiError(E.WORKFLOW_NOT_FOUND, workflow_id="wf-binding")

    HTTP 404
    {"error": {"code": "workflow.not_found",
               "message": "워크플로를 찾지 못했습니다: wf-binding",
               "params": {"workflow_id": "wf-binding"}}}

Three things follow from keeping them apart. A console can react to a code
without matching on text. Adding a language is one entry per message rather
than a change at every raise site. And a message can be reworded without
breaking anyone who depends on the code.

Messages are written for the person reading the screen, so they are polite
Korean; comments and codes are for whoever reads the source, so they are
English. The two audiences are different.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class E(StrEnum):
    """Error codes. The value is part of the API surface - do not rename."""

    # -------------------------------------------------- authentication
    AUTH_NOT_CONFIGURED = "auth.not_configured"
    AUTH_TOKEN_REQUIRED = "auth.token_required"
    AUTH_TOKEN_INVALID = "auth.token_invalid"
    AUTH_FORBIDDEN = "auth.forbidden"

    # -------------------------------------------------- run
    RUN_NOT_FOUND = "run.not_found"
    RUN_NODE_FAILED = "run.node_failed"

    # -------------------------------------------------- workflow
    WORKFLOW_NOT_FOUND = "workflow.not_found"
    WORKFLOW_GRAPH_INVALID = "workflow.graph_invalid"

    # -------------------------------------------------- artifact
    ARTIFACT_NOT_REGISTERED = "artifact.not_registered"
    ARTIFACT_OUTSIDE_ROOT = "artifact.outside_root"
    ARTIFACT_FILE_MISSING = "artifact.file_missing"

    # -------------------------------------------------- model registry
    INPUT_TYPE_REJECTED = "input.type_rejected"
    INPUT_TOO_LARGE = "input.too_large"
    INPUT_EMPTY = "input.empty"
    INPUT_MISSING = "input.missing"
    INPUT_LENGTH_REQUIRED = "input.length_required"
    INPUT_INLINE_TOO_LARGE = "input.inline_too_large"
    INPUT_OUTSIDE_ROOT = "input.outside_root"
    INPUT_QUOTA_EXCEEDED = "input.quota_exceeded"
    ROUND_NOT_IN_PROJECT = "round.not_in_project"
    PROJECT_NOT_FOUND = "project.not_found"
    USER_NOT_FOUND = "user.not_found"
    USER_LAST_ADMIN = "user.last_admin"
    USER_SELF = "user.self"
    USER_EMPTY_PATCH = "user.empty_patch"
    AUTH_IDENTITY_MISMATCH = "auth.identity_mismatch"
    FORK_NOT_READY = "run.fork_not_ready"
    COPILOT_EMPTY = "copilot.empty"
    COPILOT_UNAVAILABLE = "copilot.unavailable"
    MODEL_NOT_FOUND = "model.not_found"
    MODEL_UNRESOLVABLE = "model.unresolvable"


#  HTTP status per code. Kept beside the codes so a new code cannot be added
#  without deciding what it answers.
STATUS: dict[E, int] = {
    E.AUTH_NOT_CONFIGURED: 503,
    E.AUTH_TOKEN_REQUIRED: 401,
    E.AUTH_TOKEN_INVALID: 401,
    E.AUTH_FORBIDDEN: 403,
    E.RUN_NOT_FOUND: 404,
    E.RUN_NODE_FAILED: 400,
    E.WORKFLOW_NOT_FOUND: 404,
    E.WORKFLOW_GRAPH_INVALID: 400,
    E.ARTIFACT_NOT_REGISTERED: 404,
    E.ARTIFACT_OUTSIDE_ROOT: 400,
    E.ARTIFACT_FILE_MISSING: 404,
    E.INPUT_TYPE_REJECTED: 415,
    E.INPUT_TOO_LARGE: 413,
    E.INPUT_EMPTY: 400,
    E.INPUT_MISSING: 400,
    E.INPUT_LENGTH_REQUIRED: 411,
    E.INPUT_INLINE_TOO_LARGE: 413,
    E.INPUT_OUTSIDE_ROOT: 400,
    E.INPUT_QUOTA_EXCEEDED: 413,
    E.ROUND_NOT_IN_PROJECT: 400,
    E.PROJECT_NOT_FOUND: 404,
    E.USER_NOT_FOUND: 404,
    E.USER_LAST_ADMIN: 409,
    E.USER_SELF: 400,
    E.USER_EMPTY_PATCH: 400,
    E.AUTH_IDENTITY_MISMATCH: 403,
    E.FORK_NOT_READY: 400,
    E.COPILOT_EMPTY: 400,
    E.COPILOT_UNAVAILABLE: 503,
    E.MODEL_NOT_FOUND: 404,
    E.MODEL_UNRESOLVABLE: 404,
}

DEFAULT_LANGUAGE = "ko"

#  Placeholders are named so a translation may reorder them.
MESSAGES: dict[str, dict[E, str]] = {
    "ko": {
        E.AUTH_NOT_CONFIGURED: "인증 공급자가 설정되어 있지 않습니다.",
        E.AUTH_TOKEN_REQUIRED: "로그인이 필요합니다.",
        E.AUTH_TOKEN_INVALID: "로그인 정보를 확인하지 못했습니다. 다시 로그인하십시오.",
        E.AUTH_FORBIDDEN: "이 작업을 수행할 권한이 없습니다.",
        E.RUN_NOT_FOUND: "실행을 찾지 못했습니다: {run_id}",
        E.RUN_NODE_FAILED: "노드를 처리하지 못했습니다: {reason}",
        E.WORKFLOW_NOT_FOUND: "워크플로를 찾지 못했습니다: {workflow_id}",
        E.WORKFLOW_GRAPH_INVALID: "그래프에 결함이 있습니다: {reason}",
        E.ARTIFACT_NOT_REGISTERED: "등록되지 않은 산출물입니다.",
        E.ARTIFACT_OUTSIDE_ROOT: "저장 위치를 벗어나는 경로입니다.",
        E.ARTIFACT_FILE_MISSING: "산출물 파일이 저장소에 없습니다.",
        E.INPUT_TYPE_REJECTED: "받지 않는 파일 형식입니다: {suffix}. 서열(FASTA)이나 구조(PDB·mmCIF) 파일을 올리십시오.",
        E.INPUT_TOO_LARGE: "파일이 너무 큽니다. {limit_mb}MB 이하만 올릴 수 있습니다.",
        E.INPUT_EMPTY: "빈 파일입니다.",
        E.INPUT_MISSING: "올릴 파일이 없습니다. file 항목으로 보내십시오.",
        E.INPUT_LENGTH_REQUIRED: "파일 크기를 먼저 알려야 합니다. Content-Length 없는 전송은 받지 않습니다.",
        E.INPUT_INLINE_TOO_LARGE: "붙여넣은 내용이 너무 큽니다({field}). {limit_mb}MB 를 넘으면 파일로 올리십시오.",
        E.INPUT_OUTSIDE_ROOT: "저장 위치 밖의 파일은 읽지 않습니다.",
        E.INPUT_QUOTA_EXCEEDED: "올린 파일이 한도를 넘습니다. {used_mb}MB 보관 중, 한도 {quota_mb}MB. 어느 실행도 읽지 않은 파일은 {days}일 뒤 지워지고, 실행이 읽은 파일은 그 실행과 함께 남습니다.",
        E.ROUND_NOT_IN_PROJECT: "회차 {round_id} 는 프로젝트 {project_id} 의 것이 아닙니다.",
        E.PROJECT_NOT_FOUND: "프로젝트를 찾지 못했습니다: {project_id}",
        E.USER_NOT_FOUND: "이용자를 찾지 못했습니다: {user_id}",
        E.USER_LAST_ADMIN: "{user_id} 는 마지막 운영자입니다. 다른 운영자를 먼저 두십시오.",
        E.USER_SELF: "자기 계정의 운영 권한을 빼거나 끌 수 없습니다. 다른 운영자가 해야 합니다.",
        E.USER_EMPTY_PATCH: "바꿀 내용이 없습니다.",
        E.AUTH_IDENTITY_MISMATCH: "이름 {user_id} 는 다른 계정이 쓰고 있습니다. 운영자에게 알리십시오.",
        E.FORK_NOT_READY: "이 지점에서 갈라질 수 없습니다: {reason}",
        E.COPILOT_EMPTY: "물어볼 내용이 없습니다.",
        E.COPILOT_UNAVAILABLE: "설계 Copilot 의 모델에 닿지 못했습니다: {reason}",
        E.MODEL_NOT_FOUND: "모델을 찾지 못했습니다: {model_id}",
        E.MODEL_UNRESOLVABLE: "모델의 실행 위치를 해석하지 못했습니다: {reason}",
    },
    "en": {
        E.AUTH_NOT_CONFIGURED: "No authentication provider is configured.",
        E.AUTH_TOKEN_REQUIRED: "Sign-in is required.",
        E.AUTH_TOKEN_INVALID: "Your sign-in could not be verified. Please sign in again.",
        E.AUTH_FORBIDDEN: "You do not have permission to do this.",
        E.RUN_NOT_FOUND: "No such run: {run_id}",
        E.RUN_NODE_FAILED: "The node could not be processed: {reason}",
        E.WORKFLOW_NOT_FOUND: "No such workflow: {workflow_id}",
        E.WORKFLOW_GRAPH_INVALID: "The graph is not valid: {reason}",
        E.ARTIFACT_NOT_REGISTERED: "That artifact is not registered.",
        E.ARTIFACT_OUTSIDE_ROOT: "That path lies outside the storage root.",
        E.ARTIFACT_FILE_MISSING: "The artifact file is not in storage.",
        E.INPUT_TYPE_REJECTED: "That file type is not accepted: {suffix}. Upload a sequence (FASTA) or a structure (PDB, mmCIF).",
        E.INPUT_TOO_LARGE: "That file is too large. The limit is {limit_mb}MB.",
        E.INPUT_EMPTY: "That file is empty.",
        E.INPUT_MISSING: "No file to upload. Send it as the 'file' field.",
        E.INPUT_LENGTH_REQUIRED: "The upload must declare its size. Requests without Content-Length are refused.",
        E.INPUT_INLINE_TOO_LARGE: "Pasted content is too large ({field}). Above {limit_mb}MB, upload it as a file.",
        E.INPUT_OUTSIDE_ROOT: "Files outside the storage root are not read.",
        E.INPUT_QUOTA_EXCEEDED: "Your uploads exceed the limit: {used_mb}MB stored of {quota_mb}MB. Files no run has read are removed after {days} days; files a run read stay with that run.",
        E.ROUND_NOT_IN_PROJECT: "Round {round_id} does not belong to project {project_id}.",
        E.PROJECT_NOT_FOUND: "No such project: {project_id}",
        E.USER_NOT_FOUND: "No such user: {user_id}",
        E.USER_LAST_ADMIN: "{user_id} is the last operator. Appoint another first.",
        E.USER_SELF: "You cannot remove your own operator role or switch yourself off. Another operator must.",
        E.USER_EMPTY_PATCH: "Nothing to change.",
        E.AUTH_IDENTITY_MISMATCH: "The name {user_id} belongs to a different account. Tell an operator.",
        E.FORK_NOT_READY: "Cannot fork at that point: {reason}",
        E.COPILOT_EMPTY: "Nothing to ask.",
        E.COPILOT_UNAVAILABLE: "The copilot model could not be reached: {reason}",
        E.MODEL_NOT_FOUND: "No such model: {model_id}",
        E.MODEL_UNRESOLVABLE: "The model could not be resolved to an endpoint: {reason}",
    },
}


class ApiError(Exception):
    """An error the caller is meant to see. Carries a code, not a sentence."""

    def __init__(self, code: E, *, status: int | None = None, **params: Any) -> None:
        self.code = code
        self.status = status if status is not None else STATUS.get(code, 400)
        self.params = params
        super().__init__(f"{code}: {params}" if params else str(code))

    def body(self, language: str = DEFAULT_LANGUAGE) -> dict[str, Any]:
        return {
            "error": {
                "code": str(self.code),
                "message": render(self.code, language, **self.params),
                "params": self.params,
            }
        }


def negotiate(accept_language: str | None) -> str:
    """Pick a catalogue from an Accept-Language header.

    Quality values are ignored; the first understood tag wins, which is enough
    while there are two languages. An unknown tag falls back rather than fails.
    """
    if not accept_language:
        return DEFAULT_LANGUAGE
    for part in accept_language.split(","):
        tag = part.split(";")[0].strip().lower()
        if not tag:
            continue
        if tag in MESSAGES:
            return tag
        base = tag.split("-")[0]
        if base in MESSAGES:
            return base
    return DEFAULT_LANGUAGE


def render(code: E, language: str = DEFAULT_LANGUAGE, **params: Any) -> str:
    """Look the message up and fill in its placeholders.

    A missing placeholder must not turn an error into a different error, so the
    unformatted message is returned instead of raising.
    """
    catalogue = MESSAGES.get(language) or MESSAGES[DEFAULT_LANGUAGE]
    template = catalogue.get(code) or MESSAGES[DEFAULT_LANGUAGE].get(code) or str(code)
    try:
        return template.format(**params)
    except (KeyError, IndexError):
        return template


def missing_translations() -> dict[str, list[str]]:
    """Codes a catalogue does not cover. A test asserts this is empty."""
    return {
        lang: sorted(str(c) for c in E if c not in catalogue)
        for lang, catalogue in MESSAGES.items()
        if any(c not in catalogue for c in E)
    }
