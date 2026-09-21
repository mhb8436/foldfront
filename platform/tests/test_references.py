"""Reference search: the Europe PMC transform and input validation.

No network here - the transform runs on a sample payload, and the validation
cases raise before any HTTP call is made.
"""

import pytest

from foldfront.engine import references
from foldfront.engine.references import ReferenceError, _europe_pmc


SAMPLE = {
    "hitCount": 2,
    "resultList": {
        "result": [
            {
                "id": "42578672", "pmid": "42578672", "pmcid": "PMC13532329",
                "title": "Solubility of engineered lysozyme variants",
                "authorString": "Kim J, Park Y.", "journalTitle": "J Mol Biol",
                "pubYear": "2026", "citedByCount": 3,
            },
            {
                "id": "MED-99", "pmid": "99",
                "title": "Backbone design", "doi": "10.1/xyz", "pubYear": "2025",
            },
        ]
    },
}


def test_transform_prefers_pmc_then_pubmed_then_doi():
    hits = _europe_pmc(SAMPLE)
    assert len(hits) == 2
    a, b = hits
    assert a.source == "literature"
    assert a.title.startswith("Solubility")
    #  A PMC id resolves to the PMC article page.
    assert a.url == "https://europepmc.org/article/PMC/PMC13532329"
    assert a.extra["year"] == "2026" and a.extra["cited_by"] == 3
    #  No PMC id: fall back to the PubMed id.
    assert b.url == "https://europepmc.org/article/MED/99"


def test_transform_survives_missing_fields():
    hits = _europe_pmc({"resultList": {"result": [{"id": "x"}]}})
    assert len(hits) == 1
    assert hits[0].title == "(제목 없음)"
    assert hits[0].url == ""


def test_hit_as_dict_is_flat_and_serialisable():
    hit = _europe_pmc(SAMPLE)[0]
    d = hit.as_dict()
    assert set(d) == {"id", "source", "title", "url", "extra"}


async def test_search_rejects_an_empty_query():
    with pytest.raises(ReferenceError):
        await references.search("literature", "   ", limit=5)


async def test_search_rejects_an_unknown_source():
    with pytest.raises(ReferenceError):
        await references.search("wikipedia", "lysozyme", limit=5)
