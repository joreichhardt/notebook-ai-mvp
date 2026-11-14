"""Local archive-only preparation; never reads/uploads the worktree or runs Docker.

python prepare.py FULL_SHA NEW_OUTPUT_DIRECTORY
Outputs source.tar, source-manifest.json and a separate standalone receive.py.
Review/retain ALL printed SHA256 anchors independently before transfer.
"""

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
from pathlib import Path

from protocol import RELATIVE, need
from receive import validated_members


def prepare(sha, target):
    need(re.fullmatch("[0-9a-f]{40}", sha) is not None and not sha.startswith("ac817f8"))
    revision = (
        subprocess.run(
            ["git", "rev-parse", "--verify", sha + "^{commit}"], capture_output=True, check=True
        )
        .stdout.decode()
        .strip()
    )
    need(revision == sha and not sha.startswith("ac817f8"))
    data = subprocess.run(
        ["git", "-c", "tar.umask=0022", "archive", "--format=tar", sha],
        capture_output=True,
        check=True,
    ).stdout
    files = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive.getmembers():
            path = Path(member.name)
            need(not path.is_absolute() and ".." not in path.parts)
            need(not any(p in (".pi", ".git") for p in path.parts))
            need(member.isdir() or member.isfile())
            if member.isfile():
                need(member.mode in (0o644, 0o755))
                stream = archive.extractfile(member)
                need(stream is not None)
                if stream is None:
                    raise RuntimeError()
                files[path.as_posix()] = {
                    "mode": member.mode,
                    "sha256": hashlib.sha256(stream.read()).hexdigest(),
                }
    for name in (
        str(RELATIVE / "operator_packet.py"),
        str(RELATIVE / "protocol.py"),
        str(RELATIVE / "receive.py"),
        str(RELATIVE / "cases.json"),
        "backend/migrations/versions/0007_text_dispatch.sql",
        "backend/app/workers/delegation.py",
        "backend/tests/documents/test_delegation_database.py",
        "backend/tests/auth/test_text_delegation_migration.py",
    ):
        need(name in files)
    manifest = json.dumps(
        {"sha": sha, "files": files}, sort_keys=True, separators=(",", ":")
    ).encode()
    members, _ = validated_members(data, json.loads(manifest))
    receiver = members[str(RELATIVE / "receive.py")]
    os.umask(0o077)
    target.mkdir(mode=0o700)
    for name, value in (
        ("source.tar", data),
        ("source-manifest.json", manifest),
        ("receive.py", receiver),
    ):
        with (target / name).open("xb") as out:
            out.write(value)
    print(
        json.dumps(
            {
                "archive_sha256": hashlib.sha256(data).hexdigest(),
                "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
                "receiver_sha256": hashlib.sha256(receiver).hexdigest(),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    try:
        need(len(sys.argv) == 3)
        prepare(sys.argv[1], Path(sys.argv[2]))
    except Exception:  # noqa: BLE001 — no git stderr/worktree content in output
        raise SystemExit(1) from None
