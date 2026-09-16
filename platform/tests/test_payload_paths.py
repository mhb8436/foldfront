"""Where a request string becomes a file read.

StageInput.text is the one place that happens, so it is the one place a
request could name a file it should not. These pin the rule: content is
content, a path is read only from inside the storage root, and the console's
rule for telling the two apart is the server's too.
"""

from __future__ import annotations

import pytest

from foldfront.core.config import get_settings
from foldfront.engine.payloads import PayloadError, StageInput


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_OUTPUT_ROOT", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def inp(**request):
    return StageInput(request=request, upstream={})


def test_저장_위치_안의_파일은_읽는다(root):
    (root / "inputs").mkdir()
    (root / "inputs" / "lys.fasta").write_text(">lys\nMK\n")

    assert inp(target_fasta=str(root / "inputs" / "lys.fasta")).text("target_fasta") == ">lys\nMK\n"


def test_저장_위치_밖의_파일은_읽지_않는다(root, tmp_path_factory):
    """Found in review: any path the worker could open was read, and the FASTA
    headers of that file went into the run's metrics for anyone to see."""
    outside = tmp_path_factory.mktemp("elsewhere") / "secret.fasta"
    outside.write_text(">SECRET_TOKEN\nMK\n")

    with pytest.raises(PayloadError, match="저장 위치 밖"):
        inp(target_fasta=str(outside)).text("target_fasta")


def test_상대_경로로도_밖으로_나가지_못한다(root):
    with pytest.raises(PayloadError, match="저장 위치 밖"):
        inp(target_fasta="../../etc/passwd").text("target_fasta")


def test_없는_파일은_저장_위치_기준_경로로만_말한다(root):
    """The message must not become a filesystem oracle for the rest of the host."""
    with pytest.raises(PayloadError) as exc:
        inp(target_fasta=str(root / "gone.fasta")).text("target_fasta")

    assert "gone.fasta" in str(exc.value)
    assert str(root) not in str(exc.value)


def test_경로_뒤의_줄바꿈은_경로로_읽는다(root):
    """A textarea hands a path over with a newline after it. Untrimmed, that
    newline made it content and the run failed with a FASTA-format error
    that pointed away from the cause."""
    (root / "lys.fasta").write_text(">lys\nMK\n")

    assert inp(target_fasta=str(root / "lys.fasta") + "\n").text("target_fasta") == ">lys\nMK\n"


def test_공백이_든_한_줄은_내용이다(root):
    """A one-line ATOM record, trimmed, has no newline. It is still content -
    a path never contains a space."""
    assert inp(target_pdb="ATOM      1  N\n").text("target_pdb") == "ATOM      1  N"


def test_공백_없는_한_줄은_경로다(root):
    (root / "a.fasta").write_text(">a\nMK\n")
    assert inp(target_fasta="a.fasta").text("target_fasta") == ">a\nMK\n"


def test_내용은_내용대로_받는다(root):
    assert inp(target_fasta=">a\nMK\n").text("target_fasta") == ">a\nMK"
    assert inp(target_fasta="  >a\nMK\n ").text("target_fasta") == ">a\nMK"


def test_공백뿐이면_없는_것이다(root):
    assert inp(target_fasta="   \n").text("target_fasta") is None
