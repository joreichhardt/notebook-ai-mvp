"""Real temporary-file tests only: no DB, network, containers or credentials."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.documents.local_storage import LocalStorage, LocalStorageConfig
from app.documents.storage import ObjectScope, StorageError
from app.ingestion.text import MAX_UPLOAD_BYTES


class Denied(RuntimeError):
    pass


class TestLocalStorage(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.actor = uuid4()
        self.scope = ObjectScope(uuid4(), uuid4(), uuid4(), uuid4())
        self.calls = []
        self.config = LocalStorageConfig(self.root)
        self.store = LocalStorage(self.config, authorize=self.authorize)

    async def authorize(self, actor, scope, action):
        if actor != self.actor or scope != self.scope:
            raise Denied()
        self.calls.append(action)

    @property
    def original(self):
        return self.root / self.scope.key

    async def test_persists_across_instances_with_private_permissions(self):
        await self.store.put(self.actor, self.scope, b"private source", "text/plain")
        restarted = LocalStorage(self.config, authorize=self.authorize)
        self.assertEqual(await restarted.read(self.actor, self.scope), b"private source")
        self.assertEqual(self.original.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.original.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.calls, ["write", "write", "write", "read", "read"])
        self.assertEqual(list(self.original.parent.iterdir()), [self.original])

    async def test_markdown_and_pdf_are_stored_as_opaque_originals(self):
        # Persistence only: this deliberately does not claim PDF parsing.
        for media in ("text/markdown", "application/pdf"):
            self.scope = ObjectScope(uuid4(), uuid4(), uuid4(), uuid4())
            await self.store.put(self.actor, self.scope, b"opaque original", media)
            self.assertEqual(await self.store.read(self.actor, self.scope), b"opaque original")

    async def test_foreign_actor_or_scope_never_reaches_filesystem(self):
        with patch("app.documents.local_storage.os.open") as opened:
            with self.assertRaises(Denied):
                await self.store.read(uuid4(), self.scope)
            with self.assertRaises(Denied):
                await self.store.put(
                    self.actor, ObjectScope(uuid4(), uuid4(), uuid4(), uuid4()), b"x", "text/plain"
                )
            opened.assert_not_called()

    async def test_existing_original_is_never_overwritten(self):
        await self.store.put(self.actor, self.scope, b"first", "text/plain")
        with self.assertRaises(StorageError):
            await self.store.put(self.actor, self.scope, b"second", "text/plain")
        self.assertEqual(await self.store.read(self.actor, self.scope), b"first")
        self.assertEqual(list(self.original.parent.iterdir()), [self.original])

    async def test_workspace_symlink_cannot_redirect_writes(self):
        outside = self.root / "outside"
        outside.mkdir(mode=0o700)
        (self.root / str(self.scope.workspace_id)).symlink_to(outside, target_is_directory=True)
        with self.assertRaises(StorageError):
            await self.store.put(self.actor, self.scope, b"x", "text/plain")
        self.assertEqual(list(outside.iterdir()), [])

    async def test_original_symlink_is_not_followed(self):
        await self.store.put(self.actor, self.scope, b"source", "text/plain")
        target = self.root / "target"
        target.write_bytes(b"not an original")
        self.original.unlink()
        self.original.symlink_to(target)
        with self.assertRaises(StorageError):
            await self.store.read(self.actor, self.scope)
        self.assertEqual(target.read_bytes(), b"not an original")

    async def test_hardlinked_or_world_readable_original_is_rejected(self):
        await self.store.put(self.actor, self.scope, b"source", "text/plain")
        extra = self.root / "extra"
        os.link(self.original, extra)
        with self.assertRaises(StorageError):
            await self.store.read(self.actor, self.scope)
        extra.unlink()
        self.original.chmod(0o644)
        with self.assertRaises(StorageError):
            await self.store.read(self.actor, self.scope)

    async def test_fifo_is_rejected_without_blocking(self):
        await self.store.put(self.actor, self.scope, b"source", "text/plain")
        self.original.unlink()
        os.mkfifo(self.original, 0o600)
        with self.assertRaises(StorageError):
            await self.store.read(self.actor, self.scope)

    async def test_revocation_before_publication_removes_staging_file(self):
        async def revoke(actor, scope, action):
            await self.authorize(actor, scope, action)
            if len(self.calls) == 2:
                raise Denied()

        store = LocalStorage(self.config, authorize=revoke)
        with self.assertRaises(Denied):
            await store.put(self.actor, self.scope, b"source", "text/plain")
        self.assertFalse(self.original.exists())
        self.assertEqual(list(self.original.parent.iterdir()), [])

    async def test_revocation_after_read_discards_result(self):
        await self.store.put(self.actor, self.scope, b"source", "text/plain")
        self.calls.clear()

        async def revoke(actor, scope, action):
            await self.authorize(actor, scope, action)
            if len(self.calls) == 2:
                raise Denied()

        with self.assertRaises(Denied):
            await LocalStorage(self.config, authorize=revoke).read(self.actor, self.scope)

    async def test_insecure_or_missing_root_has_no_fallback(self):
        self.root.chmod(0o755)
        with self.assertRaises(StorageError):
            await self.store.put(self.actor, self.scope, b"x", "text/plain")
        missing = self.root / "missing"
        with self.assertRaises(StorageError):
            await LocalStorage(LocalStorageConfig(missing), authorize=self.authorize).read(
                self.actor, self.scope
            )
        self.assertFalse(missing.exists())

    async def test_invalid_size_and_media_do_not_touch_disk(self):
        with patch("app.documents.local_storage.os.open") as opened:
            for data, media in [(b"", "text/plain"), (b"x", "application/zip")]:
                with self.assertRaises(StorageError):
                    await self.store.put(self.actor, self.scope, data, media)
            with self.assertRaises(StorageError):
                await self.store.put(
                    self.actor, self.scope, b"x" * (MAX_UPLOAD_BYTES + 1), "text/plain"
                )
            opened.assert_not_called()

    def test_relative_or_traversing_config_is_rejected(self):
        for root in [Path("relative"), Path("/private/../outside")]:
            with self.assertRaises(ValueError):
                LocalStorageConfig(root)
