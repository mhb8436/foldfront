"""Reference search.

Looks up external evidence for a design decision across a few public sources,
each returning the same small, uniform hit (id, source, title, url, extra) so
the console and the evidence store never learn a source's own shape.

Sources:
    literature  Europe PMC   - papers
    structure   RCSB PDB     - solved structures
    protein     UniProt      - reviewed protein entries
    family      InterPro     - domains and families
    cluster     UniRef       - sequence clusters

This is net-new: the original has no reference search. It is read-only and
side-effect free - a search attaches nothing on its own; the API route decides
what to pin to a run.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable

import httpx

_EUROPE_PMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
_UNIPROT = "https://rest.uniprot.org/uniprotkb/search"
_UNIREF = "https://rest.uniprot.org/uniref/search"
_INTERPRO = "https://www.ebi.ac.uk/interpro/api/entry/interpro/"
_RCSB_SEARCH = "https://search.rcsb.org/rcsbsearch/v2/query"
_RCSB_GRAPHQL = "https://data.rcsb.org/graphql"

SOURCES = ("literature", "structure", "protein", "family", "cluster")

TIMEOUT_S = 12.0
MAX_LIMIT = 25
_UA = {"User-Agent": "foldfront/0.1 (reference search)"}


class ReferenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Hit:
    id: str
    source: str
    title: str
    url: str
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clamp(limit: int) -> int:
    return max(1, min(MAX_LIMIT, int(limit)))


# ---------------------------------------------------------------- parsers (pure)


def _europe_pmc(payload: dict[str, Any]) -> list[Hit]:
    hits: list[Hit] = []
    for r in payload.get("resultList", {}).get("result", []):
        #  Only a real PubMed id builds a MED link; the generic record `id` is
        #  not a pmid and must not be turned into a wrong article URL.
        pmid = r.get("pmid")
        pmcid = r.get("pmcid")
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


def _uniprot(payload: dict[str, Any]) -> list[Hit]:
    hits: list[Hit] = []
    for r in payload.get("results", []):
        acc = str(r.get("primaryAccession") or "")
        name = (
            r.get("proteinDescription", {})
            .get("recommendedName", {})
            .get("fullName", {})
            .get("value")
        )
        hits.append(
            Hit(
                id=acc,
                source="protein",
                title=str(name or acc or "(이름 없음)"),
                url=f"https://www.uniprot.org/uniprotkb/{acc}" if acc else "",
                extra={
                    "organism": r.get("organism", {}).get("scientificName"),
                    "reviewed": "reviewed" in str(r.get("entryType", "")).lower(),
                },
            )
        )
    return hits


def _uniref(payload: dict[str, Any]) -> list[Hit]:
    hits: list[Hit] = []
    for r in payload.get("results", []):
        cid = str(r.get("id") or "")
        rep = r.get("representativeMember", {}) or {}
        hits.append(
            Hit(
                id=cid,
                source="cluster",
                title=str(r.get("name") or cid or "(이름 없음)"),
                url=f"https://www.uniprot.org/uniref/{cid}" if cid else "",
                extra={
                    "members": r.get("memberCount"),
                    "organism": rep.get("organismName"),
                },
            )
        )
    return hits


def _interpro(payload: dict[str, Any]) -> list[Hit]:
    hits: list[Hit] = []
    for r in payload.get("results", []):
        m = r.get("metadata", {}) or {}
        acc = str(m.get("accession") or "")
        hits.append(
            Hit(
                id=acc,
                source="family",
                title=str(m.get("name") or acc or "(이름 없음)"),
                url=f"https://www.ebi.ac.uk/interpro/entry/interpro/{acc}/" if acc else "",
                extra={"type": m.get("type"), "source_db": m.get("source_database")},
            )
        )
    return hits


def _rcsb(ids: list[str], titles: dict[str, str]) -> list[Hit]:
    return [
        Hit(
            id=pdb_id,
            source="structure",
            title=titles.get(pdb_id) or pdb_id,
            url=f"https://www.rcsb.org/structure/{pdb_id}",
            extra={"pdb_id": pdb_id},
        )
        for pdb_id in ids
    ]


# ---------------------------------------------------------------- handlers (HTTP)


async def _get_json(client: httpx.AsyncClient, url: str, params: dict[str, Any]) -> dict[str, Any]:
    resp = await client.get(url, params=params)
    resp.raise_for_status()
    return resp.json()


async def _h_literature(client: httpx.AsyncClient, query: str, n: int) -> list[Hit]:
    payload = await _get_json(
        client, _EUROPE_PMC,
        {"query": query, "format": "json", "pageSize": n, "resultType": "lite"},
    )
    return _europe_pmc(payload)[:n]


async def _h_protein(client: httpx.AsyncClient, query: str, n: int) -> list[Hit]:
    payload = await _get_json(
        client, _UNIPROT,
        {"query": query, "format": "json", "size": n,
         "fields": "accession,protein_name,organism_name,reviewed"},
    )
    return _uniprot(payload)[:n]


async def _h_cluster(client: httpx.AsyncClient, query: str, n: int) -> list[Hit]:
    payload = await _get_json(client, _UNIREF, {"query": query, "format": "json", "size": n})
    return _uniref(payload)[:n]


async def _h_family(client: httpx.AsyncClient, query: str, n: int) -> list[Hit]:
    payload = await _get_json(client, _INTERPRO, {"search": query, "page_size": n})
    return _interpro(payload)[:n]


async def _h_structure(client: httpx.AsyncClient, query: str, n: int) -> list[Hit]:
    #  RCSB search returns identifiers only; titles come from one GraphQL call.
    search = await client.post(
        _RCSB_SEARCH,
        json={
            "query": {"type": "terminal", "service": "full_text", "parameters": {"value": query}},
            "return_type": "entry",
            "request_options": {"paginate": {"start": 0, "rows": n}},
        },
    )
    #  No match answers 204 with no body rather than an empty result set.
    if search.status_code == 204:
        return []
    search.raise_for_status()
    ids = [str(r["identifier"]) for r in search.json().get("result_set", [])][:n]
    if not ids:
        return []
    titles: dict[str, str] = {}
    gql = await client.post(
        _RCSB_GRAPHQL,
        json={"query": "{entries(entry_ids:%s){rcsb_id struct{title}}}"
              % ("[" + ",".join(f'"{i}"' for i in ids) + "]")},
    )
    if gql.status_code == 200:
        for e in gql.json().get("data", {}).get("entries", []) or []:
            titles[str(e.get("rcsb_id"))] = (e.get("struct") or {}).get("title") or ""
    return _rcsb(ids, titles)


_HANDLERS: dict[str, Callable[[httpx.AsyncClient, str, int], Awaitable[list[Hit]]]] = {
    "literature": _h_literature,
    "structure": _h_structure,
    "protein": _h_protein,
    "family": _h_family,
    "cluster": _h_cluster,
}


async def search(source: str, query: str, *, limit: int = 10) -> list[Hit]:
    """Search one source. Read-only; raises ReferenceError on a bad source, an
    empty query, or an upstream failure - the route turns that into a 4xx/5xx."""
    query = (query or "").strip()
    if not query:
        raise ReferenceError("검색어가 비어 있습니다")
    handler = _HANDLERS.get(source)
    if handler is None:
        raise ReferenceError(
            f"모르는 참조 소스입니다: {source} (가능: {', '.join(SOURCES)})"
        )
    n = _clamp(limit)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, headers=_UA) as client:
            return await handler(client, query, n)
    except httpx.HTTPError as exc:
        raise ReferenceError(f"참조 소스 호출 실패: {exc}") from exc
