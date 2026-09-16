"""Indexes against the schemas they serve.

Index declarations live in db/client.py and the documents they index live in
db/models.py. Nothing links the two, so renaming a field leaves an index
pointing at a name that no longer exists - and Mongo will happily build it,
because an index on a missing field is legal. Queries then fall back to a
collection scan and the only symptom is that things get slow.

These tests are that link.
"""

from __future__ import annotations

import pytest

from foldfront.db.client import C, INDEXES
from foldfront.db.models import (
    Artifact,
    AuditLog,
    Doc,
    Experiment,
    Feedback,
    Job,
    ModelVersion,
    Project,
    Report,
    Round,
    Run,
    RunEvent,
    Task,
    User,
    Workflow,
)

#  Which document each collection holds.
SCHEMA: dict[str, type[Doc]] = {
    C.RUNS: Run,
    C.RUN_EVENTS: RunEvent,
    C.ARTIFACTS: Artifact,
    C.MODELS: ModelVersion,
    C.WORKFLOWS: Workflow,
    C.JOBS: Job,
    C.PROJECTS: Project,
    C.ROUNDS: Round,
    C.TASKS: Task,
    C.FEEDBACK: Feedback,
    C.EXPERIMENTS: Experiment,
    C.REPORTS: Report,
    C.AUDIT: AuditLog,
    C.USERS: User,
}


def collections() -> list[str]:
    return [v for k, v in vars(C).items() if not k.startswith("__") and isinstance(v, str)]


def indexed_fields(collection: str) -> set[str]:
    """Field paths an index is built on, with any dotted suffix dropped."""
    fields: set[str] = set()
    for model in INDEXES.get(collection, []):
        for key, _direction in model.document["key"].items():
            fields.add(str(key).split(".")[0])
    return fields


def test_every_collection_has_a_schema():
    """A collection nothing maps to is one nothing here can check."""
    assert set(collections()) == set(SCHEMA)


@pytest.mark.parametrize("collection", sorted(SCHEMA))
def test_every_indexed_field_exists_on_the_document(collection: str):
    """The failure this catches is silent: Mongo indexes a field that is not
    there, and the query it was meant to serve scans the collection instead."""
    unknown = indexed_fields(collection) - set(SCHEMA[collection].model_fields)

    assert unknown == set(), f"{collection} indexes fields the document does not have: {unknown}"


@pytest.mark.parametrize(
    "collection",
    [C.RUNS, C.RUN_EVENTS, C.ARTIFACTS, C.MODELS, C.WORKFLOWS, C.JOBS, C.AUDIT],
)
def test_the_collections_the_console_lists_are_indexed(collection: str):
    """These are polled continuously. A scan on any of them is what takes the
    cluster down under load."""
    assert INDEXES.get(collection), f"{collection} has no index"


def test_index_names_are_unique_within_a_collection():
    """Two indexes sharing a name means Mongo keeps only one of them."""
    for collection, models in INDEXES.items():
        names = [m.document["name"] for m in models]
        assert len(names) == len(set(names)), f"{collection} declares a name twice"


def test_identifiers_are_unique_where_the_code_assumes_so():
    """Upserts key on these. Without a unique index a race inserts a duplicate
    and every later lookup silently picks one of them."""
    expected = {
        C.RUNS: "run_id",
        C.PROJECTS: "project_id",
        C.USERS: "user_id",
    }
    for collection, field in expected.items():
        unique = [
            m
            for m in INDEXES[collection]
            if m.document.get("unique") and field in m.document["key"]
        ]
        assert unique, f"{collection}.{field} is not covered by a unique index"
