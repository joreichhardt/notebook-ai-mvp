"""Execute the Dockerfile's stdlib stamp recipe, not a Docker/image build."""

import shlex
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("value", ["", "a" * 40, "a" * 39, "a" * 41, "A" * 40, "a" * 40 + "\n"])
def test_image_stamp_recipe_validates_before_writing(tmp_path, value):
    dockerfile = Path(__file__).resolve().parents[2] / "docker" / "api.Dockerfile"
    text = dockerfile.read_text()
    commands = [line[4:] for line in text.splitlines() if line.startswith("RUN python -c ")]
    assert len(commands) == 1
    argv = shlex.split(commands[0])
    assert argv[:2] == ["python", "-c"]
    assert argv[3:] == ["$NOTEBOOK_RELEASE_SHA"]
    # Execute identical validation; redirect only the fixed image destination to tmp_path.
    assert argv[2].count('"/app/app/_release_sha"') == 1
    stamp = tmp_path / "_release_sha"
    script = argv[2].replace('"/app/app/_release_sha"', repr(str(stamp)))
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, value],
        cwd=tmp_path,
        capture_output=True,
        timeout=5,
        check=False,
    )
    valid = value in ("", "a" * 40)
    assert (result.returncode == 0) is valid
    if valid:
        assert stamp.read_text() == value
    else:
        assert not stamp.exists()
        assert b"invalid release SHA" in result.stderr
    assert text.index("RUN python -c ") < text.index("USER 10001:10001")
    assert "LABEL org.opencontainers.image.revision=$NOTEBOOK_RELEASE_SHA" in text
