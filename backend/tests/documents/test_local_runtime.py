"""Local configuration tests; engine construction mocked, no database access."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.documents import runtime
from app.documents.local_storage import LocalStorage
from app.documents.service import DocumentError
from app.documents.storage import StorageError
from sqlalchemy.engine import URL


class TestLocalRuntime(unittest.TestCase):
    def setUp(self):
        runtime.local_upload_runtime.cache_clear()
        self.addCleanup(runtime.local_upload_runtime.cache_clear)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.root = base / "originals"
        self.root.mkdir(mode=0o700)
        self.secret = base / "io.secret"
        self.secret.touch(mode=0o600)
        # No credentials: engine construction is mocked throughout this unit suite.
        self.url = URL.create("postgresql", username="upload_io", host="db", database="app")
        self.secret.write_text(self.url.render_as_string(hide_password=False))
        self.settings = runtime.UploadSettings(
            local_uploads_enabled=True,
            local_storage_root=str(self.root),
            storage_io_database_url_file=str(self.secret),
        )
        self.settings_patch = patch.object(runtime, "upload_settings", return_value=self.settings)
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)
        self.engine = MagicMock()
        self.engine_patch = patch.object(runtime, "create_engine", return_value=self.engine)
        self.make_engine = self.engine_patch.start()
        self.addCleanup(self.engine_patch.stop)

    def test_disabled_does_not_open_storage_or_secret(self):
        self.settings.local_uploads_enabled = False
        with patch.object(runtime.os, "open") as opened:
            self.assertIsNone(runtime.local_upload_runtime())
            opened.assert_not_called()
        self.make_engine.assert_not_called()

    def test_explicit_configuration_builds_cached_bounded_engine(self):
        configured = runtime.local_upload_runtime()
        self.assertIsNotNone(configured)
        assert configured is not None
        self.assertIs(configured, runtime.local_upload_runtime())
        self.assertIs(configured.engine, self.engine)
        self.assertNotIn("upload_io", repr(configured))
        kwargs = self.make_engine.call_args.kwargs
        self.assertTrue(kwargs["hide_parameters"])
        self.assertEqual((kwargs["pool_size"], kwargs["max_overflow"]), (2, 0))
        self.assertEqual(kwargs["connect_args"]["connect_timeout"], 3)

        async def authorize(actor, scope, action):
            pass

        self.assertIsInstance(configured.store_factory(authorize), LocalStorage)
        self.engine.connect.assert_not_called()

    def test_partial_configuration_fails_without_fallback(self):
        self.settings.storage_io_database_url_file = ""
        with self.assertRaises(DocumentError):
            runtime.local_upload_runtime()
        self.make_engine.assert_not_called()

    def test_invalid_settings_are_opaque(self):
        with (
            patch.object(
                runtime, "upload_settings", side_effect=ValueError("configuration detail")
            ),
            self.assertRaises(DocumentError) as error,
        ):
            runtime.local_upload_runtime()
        self.assertEqual(str(error.exception), "storage_unconfigured")
        self.make_engine.assert_not_called()

    def test_bad_configuration_does_not_advertise_documents(self):
        from app.capabilities import capabilities

        self.secret.chmod(0o644)
        public = capabilities()
        self.assertIs(public["documents"], False)
        self.assertNotIn(str(self.root), str(public))
        self.assertNotIn("upload_io", str(public))
        self.make_engine.assert_not_called()

    def test_nonprivate_root_is_rejected(self):
        self.root.chmod(0o755)
        with self.assertRaises(StorageError):
            runtime.local_upload_runtime()
        self.make_engine.assert_not_called()

    def test_nonprivate_secret_is_rejected(self):
        self.secret.chmod(0o644)
        with self.assertRaises(DocumentError):
            runtime.local_upload_runtime()
        self.make_engine.assert_not_called()

    def test_symlink_secret_is_rejected(self):
        link = self.secret.parent / "link"
        link.symlink_to(self.secret)
        self.settings.storage_io_database_url_file = str(link)
        with self.assertRaises(DocumentError):
            runtime.local_upload_runtime()
        self.make_engine.assert_not_called()

    def test_fifo_secret_is_rejected_without_blocking(self):
        self.secret.unlink()
        os.mkfifo(self.secret, 0o600)
        with self.assertRaises(DocumentError):
            runtime.local_upload_runtime()
        self.make_engine.assert_not_called()

    def test_app_or_privileged_login_is_not_an_io_login(self):
        for user in ("notebook_app", "postgres", "supabase_admin", "supabase_auth_admin"):
            self.secret.write_text(
                self.url.set(username=user).render_as_string(hide_password=False)
            )
            with self.assertRaises(DocumentError):
                runtime.local_upload_runtime()
        self.make_engine.assert_not_called()
