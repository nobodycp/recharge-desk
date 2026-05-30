import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from phone_refresh.providers.sky_sales_client import (
    SkySalesClient,
    SkySalesSession,
    client_from_env,
)


class SkySalesSessionFileTests(SimpleTestCase):
    def test_load_session_file_ignores_legacy_pending_otp(self):
        path = Path("/tmp/test_sky_pending_session.json")
        path.write_text(
            json.dumps(
                {
                    "jwt_token": "jwt",
                    "session_id": "sid",
                    "pending_otp": True,
                    "user_name": "u",
                    "cookies": {},
                }
            ),
            encoding="utf-8",
        )
        self.addCleanup(lambda: path.unlink(missing_ok=True))

        with patch(
            "phone_refresh.providers.sky_sales_client.session_file_path",
            return_value=path,
        ):
            self.assertIsNone(SkySalesClient.load_session_file())
            self.assertFalse(path.exists())

    @patch("phone_refresh.providers.sky_sales_client.SkySalesClient.ensure_authenticated")
    @patch("phone_refresh.providers.sky_sales_client.load_dotenv_sky")
    def test_client_from_env_skips_pending_otp_resume(
        self, _load_env, ensure_authenticated
    ):
        with patch.dict(
            "os.environ",
            {
                "SKY_SALES_USER": "user",
                "SKY_SALES_PASSWORD": "pass",
                "SKY_SALES_SESSION_FILE": "/tmp/nonexistent_sky_session.json",
            },
            clear=False,
        ):
            with patch(
                "phone_refresh.providers.sky_sales_client.SkySalesClient.load_session_file",
                return_value=None,
            ):
                client_from_env()
        ensure_authenticated.assert_called_once()

    def test_begin_login_does_not_persist_pending_otp(self):
        client = SkySalesClient()
        with patch.object(client, "login", return_value={}), patch.object(
            client, "save_session_file"
        ) as save_mock:
            client.begin_login("user", "pass")
        self.assertTrue(client.session.pending_otp)
        save_mock.assert_not_called()
