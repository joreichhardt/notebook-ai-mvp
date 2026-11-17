"""Private, immutable originals on a provisioned local volume.

Same scoped authorization contract as SupabaseStorage. No public directory,
client filenames, implicit fallback, overwrites or parser credentials. The caller
must supply fresh application authorization; filesystem permissions do not
replace notebook membership checks.
"""

import os
import stat
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from app.documents.storage import Authorize, ObjectScope, StorageError
from app.ingestion.text import MAX_UPLOAD_BYTES


@dataclass(frozen=True)
class LocalStorageConfig:
    root: Path

    def __post_init__(self):
        if (
            not isinstance(self.root, Path)
            or not self.root.is_absolute()
            or ".." in self.root.parts
        ):
            raise ValueError("absolute administrator storage root required")


class LocalStorage:
    def __init__(self, config: LocalStorageConfig, *, authorize: Authorize):
        self._config = config
        self._authorize = authorize

    @staticmethod
    def _scope(actor: UUID, scope: ObjectScope):
        if type(actor) is not UUID or type(scope) is not ObjectScope:
            raise StorageError()
        if any(
            type(value) is not UUID
            for value in (
                scope.workspace_id,
                scope.notebook_id,
                scope.document_id,
                scope.version_id,
            )
        ):
            raise StorageError()

    @staticmethod
    def _private(fd: int, *, directory: bool):
        info = os.fstat(fd)
        expected = stat.S_ISDIR if directory else stat.S_ISREG
        if (
            not expected(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or (not directory and info.st_nlink != 1)
        ):
            raise StorageError()

    @contextmanager
    def _directory(self, scope: ObjectScope, *, create: bool):
        # Hold directory descriptors: replacing a path with a symlink cannot
        # redirect a later open/write. Root and every UUID directory are private.
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        with ExitStack() as stack:
            fd = os.open(self._config.root, flags)
            stack.callback(os.close, fd)
            self._private(fd, directory=True)
            for part in scope.key.split("/")[:-1]:
                if create:
                    try:
                        os.mkdir(part, 0o700, dir_fd=fd)
                        os.fsync(fd)
                    except FileExistsError:
                        pass
                fd = os.open(part, flags, dir_fd=fd)
                stack.callback(os.close, fd)
                self._private(fd, directory=True)
            yield fd

    async def put(self, actor_id: UUID, scope: ObjectScope, data: bytes, media_type: str) -> None:
        self._scope(actor_id, scope)
        if type(data) is not bytes or not data or len(data) > MAX_UPLOAD_BYTES:
            raise StorageError("upload_too_large")
        if media_type not in ("text/plain", "text/markdown", "application/pdf"):
            raise StorageError("unsupported_media")
        await self._authorize(actor_id, scope, "write")
        try:
            with self._directory(scope, create=True) as directory:
                temporary = f".{uuid4()}.upload"
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
                fd = os.open(temporary, flags, 0o600, dir_fd=directory)
                try:
                    with os.fdopen(fd, "wb") as output:
                        output.write(data)
                        output.flush()
                        os.fsync(output.fileno())
                    await self._authorize(actor_id, scope, "write")
                    # Atomic no-replace publication. Concurrent readers cannot
                    # observe a partially written original; retries never upsert.
                    os.link(
                        temporary,
                        "original",
                        src_dir_fd=directory,
                        dst_dir_fd=directory,
                        follow_symlinks=False,
                    )
                finally:
                    os.unlink(temporary, dir_fd=directory)
                    os.fsync(directory)
            await self._authorize(actor_id, scope, "write")
        except OSError:
            raise StorageError() from None

    async def read(self, actor_id: UUID, scope: ObjectScope) -> bytes:
        self._scope(actor_id, scope)
        await self._authorize(actor_id, scope, "read")
        try:
            with self._directory(scope, create=False) as directory:
                # NONBLOCK prevents a hostile FIFO from blocking before fstat.
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
                fd = os.open("original", flags, dir_fd=directory)
                with os.fdopen(fd, "rb") as original:
                    self._private(original.fileno(), directory=False)
                    if not 0 < os.fstat(original.fileno()).st_size <= MAX_UPLOAD_BYTES:
                        raise StorageError("storage_response_too_large")
                    data = original.read(MAX_UPLOAD_BYTES + 1)
                    if not data or len(data) > MAX_UPLOAD_BYTES:
                        raise StorageError("storage_response_too_large")
            await self._authorize(actor_id, scope, "read")
            return data
        except OSError:
            raise StorageError() from None
