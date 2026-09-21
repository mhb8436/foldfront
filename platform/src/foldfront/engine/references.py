"""Reference search.

Looks up external evidence for a design decision - literature to start, with
structure and annotation sources slotting into the same interface. A hit is a
small, uniform record (id, source, title, url, extra) whichever source it came
from, so the console and the evidence store never learn a source's own shape.

This is net-new: the original has no reference search. It is deliberately
read-only and side-effect free - a search attaches nothing on its own; the
caller decides what to pin to a run (engine has no say, the API route does).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

#  Europe PMC. A plain GET, JSON out, no key. Cheap and public, which is why it
#  is the first source in.
_EUROPE_PMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

SOURCES = ("literature",)

TIMEOUT_S = 12.0
MAX_LIMIT = 25


class ReferenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Hit:
    id: str
    source: str          # the source key: "literature" ...
    title: str
    url: str
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clamp(limit: int) -> int:
    return max(1, min(MAX_LIMIT, int(limit)))


def _europe_pmc(payload: dict[str, Any]) -> list[Hit]:
    hits: list[Hit] = []
    for r in payload.get("resultList", {}).get("result", []):
        #  Only a real PubMed id builds a MED link; the generic record `id` is
        #  not a pmid and must not be turned into a wrong article URL.
        pmid = r.get("pmid")
        pmcid = r.get("pmcid")
        #  Prefer a resolvable link: PMC full text, then PubMed, then the DOI.
        if pmcid:
            url = f"https://europepmc.org/article/PMC/{pmcid}"
        elif pmid:
            url = f"https://europepmc.org/article/MED/{pmid}"
        elif r.get("doi"):
            url = f"https://doi.org/{r['doi']}"
        else:
            url = ""
        hits.append(
            Hit(
                id=str(r.get("id") or pmid or pmcid or ""),
                source="literature",
                title=str(r.get("title") or "(제목 없음)"),
                url=url,
                extra={
                    "authors": r.get("authorString"),
                    "journal": r.get("journalTitle"),
                    "year": r.get("pubYear"),
                    "cited_by": r.get("citedByCount"),
                },
            )
        )
    return hits


async def search(source: str, query: str, *, limit: int = 10) -> list[Hit]:
    """Search one source. Read-only; raises ReferenceError on a bad source, an
    empty query, or an upstream failure - the route turns that into a 4xx/5xx."""
    query = (query or "").strip()
    if not query:
        raise ReferenceError("검색어가 비어 있습니다")
    if source not in SOURCES:
        raise ReferenceError(
            f"모르는 참조 소스입니다: {source} (가능: {', '.join(SOURCES)})"
        )
    n = _clamp(limit)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            resp = await client.get(
                _EUROPE_PMC,
                params={"query": query, "format": "json", "pageSize": n, "resultType": "lite"},
                headers={"User-Agent": "foldfront/0.1 (reference search)"},
            )
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPError as exc:
        raise ReferenceError(f"참조 소스 호출 실패: {exc}") from exc
    return _europe_pmc(payload)[:n]
