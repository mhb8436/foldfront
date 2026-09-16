from __future__ import annotations


_MISSING_PDB_OUTPUTS_TOKEN = "no pdb outputs were found"

# Server-side failure patterns that should be treated as transient and skipped
# per-sequence rather than killing the entire AF2 stage. A worker that returns
# any of these is healthy at the LB level but failed on one specific job; the
# pipeline can continue with the remaining candidates and re-attempt the
# failed one later if needed.
_SERVER_ERROR_TOKENS = (
    "500 server error",
    "internal server error",
    "502 bad gateway",
    "503 service unavailable",
    "504 gateway timeout",
)


def af2_error_is_missing_pdb_outputs(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return _MISSING_PDB_OUTPUTS_TOKEN in value.strip().lower()


def af2_payload_has_missing_pdb_failure(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    return af2_error_is_missing_pdb_outputs(payload.get("error"))


def af2_error_is_server_failure(value: object) -> bool:
    """True if the error message looks like a transient worker-side 5xx.

    The AF2/ColabFold load balancer can stay healthy while a single backend
    worker returns 500 on one specific job (sequence-specific crash, OOM,
    per-job timeout). Such errors should not stop the entire stage; the
    pipeline records them as partial failures and continues.
    """
    if not isinstance(value, str):
        return False
    lowered = value.strip().lower()
    return any(token in lowered for token in _SERVER_ERROR_TOKENS)
