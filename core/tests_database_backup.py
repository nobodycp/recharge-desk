"""Database backup tab: export download and guarded import."""
from __future__ import annotations

import gzip
import tempfile
from io import BytesIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import UserProfile
from core import database_backup
from core.forms_database import IMPORT_CONFIRM_WORD

User = get_user_model()


class DatabaseBackupViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("mgr_db", password="x")
        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user)

    def test_database_tab_renders(self):
        url = reverse("core:system_settings")
        response = self.client.get(url, {"tab": "database"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Database backup")
        self.assertContains(response, "Download database")
        self.assertContains(response, "Import database")

    def test_export_sqlite_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.sqlite3"
            db_path.write_bytes(b"SQLite-format-3\x00")
            with override_settings(
                DATABASES={
                    "default": {
                        "ENGINE": "django.db.backends.sqlite3",
                        "NAME": db_path,
                    }
                }
            ):
                response = self.client.get(reverse("core:database_export"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/x-sqlite3", response["Content-Type"])
        self.assertIn(b"SQLite-format-3", response.content)

    def test_export_fixture_gz_for_postgres_without_pg_tools(self):
        with override_settings(
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.postgresql",
                    "NAME": "recharge",
                    "USER": "u",
                    "PASSWORD": "p",
                    "HOST": "127.0.0.1",
                    "PORT": "5432",
                }
            }
        ):
            payload, filename, content_type = database_backup.export_bytes()
        self.assertTrue(filename.endswith(".json.gz"))
        self.assertEqual(content_type, "application/gzip")
        data = gzip.decompress(payload)
        self.assertTrue(data.startswith(b"["))

    def test_import_requires_confirm_word(self):
        url = reverse("core:system_settings")
        payload = b"[]"
        response = self.client.post(
            url + "?tab=database",
            {
                "form": "import",
                "tab": "database",
                "confirm": "WRONG",
                "acknowledge": "on",
                "backup_file": BytesIO(payload),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, IMPORT_CONFIRM_WORD)

    def test_import_fixture_roundtrip(self):
        """Restore via loaddata after flush keeps exported users."""
        from django.core.management import call_command

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tmp:
            call_command(
                "dumpdata",
                "auth.user",
                natural_foreign=True,
                natural_primary=True,
                indent=2,
                stdout=tmp,
            )
            path = tmp.name

        try:
            with open(path, "rb") as fh:
                raw = gzip.compress(fh.read())
        finally:
            Path(path).unlink(missing_ok=True)

        database_backup.import_upload(
            SimpleUploadedFile("backup.json.gz", raw, content_type="application/gzip"),
            filename="backup.json.gz",
        )
        self.assertTrue(User.objects.filter(username="mgr_db").exists())
