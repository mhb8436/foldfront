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


# ---------------------------------------------------------------- 다른 소스 파서


def test_uniprot_parser():
    from foldfront.engine.references import _uniprot

    payload = {
        "results": [
            {
                "primaryAccession": "P00698",
                "entryType": "UniProtKB reviewed (Swiss-Prot)",
                "organism": {"scientificName": "Gallus gallus"},
                "proteinDescription": {"recommendedName": {"fullName": {"value": "Lysozyme C"}}},
            }
        ]
    }
    hits = _uniprot(payload)
    assert len(hits) == 1
    h = hits[0]
    assert h.source == "protein" and h.id == "P00698" and h.title == "Lysozyme C"
    assert h.url == "https://www.uniprot.org/uniprotkb/P00698"
    assert h.extra["organism"] == "Gallus gallus" and h.extra["reviewed"] is True


def test_uniref_parser():
    from foldfront.engine.references import _uniref

    payload = {
        "results": [
            {
                "id": "UniRef100_A0A011NRN3",
                "name": "Cluster: Lysozyme",
                "memberCount": 3,
                "representativeMember": {"organismName": "Candidatus Accumulibacter"},
            }
        ]
    }
    hits = _uniref(payload)
    assert hits[0].source == "cluster" and hits[0].id == "UniRef100_A0A011NRN3"
    assert hits[0].url == "https://www.uniprot.org/uniref/UniRef100_A0A011NRN3"
    assert hits[0].extra["members"] == 3


def test_interpro_parser():
    from foldfront.engine.references import _interpro

    payload = {
        "results": [
            {"metadata": {"accession": "IPR000974", "name": "Glycoside hydrolase",
                          "type": "family", "source_database": "interpro"}}
        ]
    }
    hits = _interpro(payload)
    assert hits[0].source == "family" and hits[0].id == "IPR000974"
    assert hits[0].url == "https://www.ebi.ac.uk/interpro/entry/interpro/IPR000974/"
    assert hits[0].extra["type"] == "family"


def test_rcsb_parser_uses_titles_when_present():
    from foldfront.engine.references import _rcsb

    hits = _rcsb(["168L", "169L"], {"168L": "T4 lysozyme"})
    assert hits[0].source == "structure" and hits[0].title == "T4 lysozyme"
    assert hits[0].url == "https://www.rcsb.org/structure/168L"
    #  A missing title falls back to the id, never blank.
    assert hits[1].title == "169L"


def test_every_source_has_a_handler():
    from foldfront.engine import references

    assert set(references.SOURCES) == set(references._HANDLERS)
