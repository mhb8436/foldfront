"""Paper constraint extraction: parsing the model's reply and folding it to
fixed positions. The LLM is injected, so no model is called here."""

import pytest

from foldfront.engine import paper
from foldfront.engine.paper import (
    PaperError,
    extract_constraints,
    extract_text,
    fixed_positions_from_masks,
    masks_from_reply,
)

_REPLY = """```json
{"suggested_masks": [
  {"chain": "A", "residue_index": 64, "residue_name": "HIS", "label": "Catalytic", "evidence": "His64 is catalytic", "confidence": "high"},
  {"chain": "A", "residue_index": 35, "residue_name": "GLU", "confidence": "low_sequence_mismatch"},
  {"chain": "B", "residue_index": "not-a-number"}
]}
```"""


def test_masks_from_reply_parses_through_fences_and_filters_bad_rows():
    masks = masks_from_reply(_REPLY)
    #  The row with a non-integer residue index is dropped.
    assert len(masks) == 2
    assert masks[0]["chain"] == "A" and masks[0]["residue_index"] == 64
    assert masks[0]["confidence"] == "high"
    #  "low_sequence_mismatch" normalises to "low".
    assert masks[1]["confidence"] == "low"


def test_masks_from_reply_is_empty_on_non_json():
    assert masks_from_reply("I could not find any residues.") == []
    assert masks_from_reply('{"something_else": 1}') == []


def test_fixed_positions_folds_and_sorts():
    masks = [
        {"chain": "A", "residue_index": 64},
        {"chain": "A", "residue_index": 35},
        {"chain": "A", "residue_index": 64},
        {"chain": "B", "residue_index": 2},
    ]
    assert fixed_positions_from_masks(masks) == {"A": [35, 64], "B": [2]}


async def test_extract_constraints_uses_the_injected_model():
    async def fake_complete(messages):
        #  The system prompt asks for the mask schema; the user message carries
        #  the paper text.
        assert any("suggested_masks" in m["content"] for m in messages)
        return _REPLY

    result = await extract_constraints("His64 is the catalytic residue.", complete=fake_complete)
    assert result["fixed_positions"] == {"A": [35, 64]}
    assert len(result["masks"]) == 2


async def test_extract_constraints_rejects_empty_text():
    with pytest.raises(PaperError):
        await extract_constraints("   ", complete=None)


def test_extract_text_rejects_non_pdf_bytes():
    with pytest.raises(PaperError):
        extract_text(b"this is not a pdf")
