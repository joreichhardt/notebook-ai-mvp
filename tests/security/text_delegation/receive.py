"""Standalone stdlib receiver: authenticate THIS file independently before execution.

Never run the copy inside an unauthenticated archive. No received code is imported.
Arguments: SHA ARCHIVE_SHA256 MANIFEST_SHA256 ARCHIVE MANIFEST NEW_SNAPSHOT
"""

import hashlib
import io
import json
import os
import re
import stat
import sys
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any

MAX_ARCHIVE = 256 * 1024**2
MAX_MANIFEST = 1024**2


def require(condition):
    if not condition:
        raise ValueError("receiver refused")


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def authenticated_bytes(path, digest, limit):
    require(re.fullmatch("[0-9a-f]{64}", digest) is not None)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        meta = os.fstat(stream.fileno())
        require(stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1 and meta.st_size <= limit)
        raw = stream.read(limit + 1)
    require(len(raw) <= limit and hashlib.sha256(raw).hexdigest() == digest)
    return raw


def member_path(name):
    path = PurePosixPath(name)
    require(name and path.parts and not path.is_absolute() and path.as_posix() == name)
    require(not any(p in ("..", ".pi", ".git", ".source-manifest.json") for p in path.parts))
    return path


def validated_members(raw, manifest):
    """Validate the complete authenticated archive before creating any snapshot files."""
    files: dict[str, dict[str, Any]] = manifest["files"]
    require(type(files) is dict and 0 < len(files) < 10000)
    for name, expected in files.items():
        member_path(name)
        if not isinstance(expected, dict):
            raise TypeError("receiver refused")
        require(set(expected) == {"mode", "sha256"})
        require(expected["mode"] in (0o644, 0o755))
        require(re.fullmatch("[0-9a-f]{64}", expected["sha256"]) is not None)
    members, seen, directories = {}, set(), set()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        entries = archive.getmembers()
        require(0 < len(entries) < 20000)
        for entry in entries:
            path = member_path(entry.name)
            name = path.as_posix()
            require(name not in seen)
            seen.add(name)
            require(entry.type in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE))
            require(entry.sparse is None)
            if entry.isdir():
                require(entry.mode == 0o755)
                directories.add(name)
                continue
            expected = files.get(name)
            if not isinstance(expected, dict):
                raise TypeError("receiver refused")
            require(entry.mode == expected["mode"])
            require(0 <= entry.size <= MAX_ARCHIVE)
            stream = archive.extractfile(entry)
            if stream is None:
                raise ValueError("receiver refused")
            content = stream.read()
            require(hashlib.sha256(content).hexdigest() == expected["sha256"])
            members[name] = content
    require(set(members) == set(files))
    for name in seen:
        require(not any(p.as_posix() in members for p in PurePosixPath(name).parents))
    return members, directories


def receive(sha, archive_digest, manifest_digest, archive_path, manifest_path, root):
    require(re.fullmatch("[0-9a-f]{40}", sha) is not None and not sha.startswith("ac817f8"))
    # Hash BOTH received artifacts against independently retained anchors before tar parsing.
    raw = authenticated_bytes(archive_path, archive_digest, MAX_ARCHIVE)
    manifest_raw = authenticated_bytes(manifest_path, manifest_digest, MAX_MANIFEST)
    manifest = json.loads(manifest_raw, object_pairs_hook=unique)
    require(set(manifest) == {"sha", "files"} and manifest["sha"] == sha)
    members, directories = validated_members(raw, manifest)
    parent = root.parent
    meta = parent.lstat()
    require(parent.resolve() == parent and stat.S_ISDIR(meta.st_mode))
    require(meta.st_uid == os.geteuid() and stat.S_IMODE(meta.st_mode) == 0o700)
    require(root.name == sha)
    root.mkdir(mode=0o700)  # Exclusive: never adopt/reuse a failed or existing snapshot.
    for name in sorted(directories, key=lambda n: (len(PurePosixPath(n).parts), n)):
        (root / name).mkdir(mode=0o755, parents=True, exist_ok=True)
    for name, content in members.items():
        target = root / name
        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(content)
        target.chmod(manifest["files"][name]["mode"])
    with (root / ".source-manifest.json").open("xb") as stream:
        stream.write(manifest_raw)
    (root / ".source-manifest.json").chmod(0o600)
    # Independent on-disk verification, still without importing any received module.
    actual = set()
    for target in root.rglob("*"):
        meta = target.lstat()
        require(meta.st_uid == os.geteuid())
        require(stat.S_ISDIR(meta.st_mode) or stat.S_ISREG(meta.st_mode))
        if stat.S_ISREG(meta.st_mode):
            require(meta.st_nlink == 1)
            actual.add(target.relative_to(root).as_posix())
    require(actual == set(members) | {".source-manifest.json"})
    for name, expected in manifest["files"].items():
        target = root / name
        require(stat.S_IMODE(target.lstat().st_mode) == expected["mode"])
        require(hashlib.sha256(target.read_bytes()).hexdigest() == expected["sha256"])
    require((root / ".source-manifest.json").read_bytes() == manifest_raw)
    return len(members)


if __name__ == "__main__":
    try:
        require(len(sys.argv) == 7)
        require(os.getuid() == os.geteuid() == 1001 and os.getgid() == os.getegid() == 1001)
        os.umask(0o077)
        receive(*sys.argv[1:4], *(Path(p) for p in sys.argv[4:7]))
    except Exception:  # noqa: BLE001 — no archive paths/content or raw exception output
        raise SystemExit(1) from None
    print("TEXT_DELEGATION_RECEIVER_VERIFIED")
