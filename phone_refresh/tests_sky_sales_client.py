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

    def test_fetch_balance_report_calls_template_22(self):
        from datetime import date

        from phone_refresh.providers.sky_sales_client import SkySalesError

        client = SkySalesClient(
            session=SkySalesSession(jwt_token="jwt", session_id="sid", eot_user_id=1550)
        )
        rows = [{"ext_return_value_6": "0528819116"}]
        with patch.object(
            client,
            "generic_api",
            return_value={"result": "SUCCESS", "data": rows},
        ) as api:
            out = client.fetch_balance_report(date(2026, 8, 1), date(2026, 8, 31))
        self.assertEqual(out, rows)
        api.assert_called_once_with(
            "GetEot4RReportData",
            [22, 1550, "01/08/2026", "31/08/2026"],
        )

        client.session.eot_user_id = None
        with self.assertRaises(SkySalesError):
            client.fetch_balance_report(date(2026, 8, 1), date(2026, 8, 31))

    def test_http_ignores_env_proxy_unless_sky_sales_proxy(self):
        with patch.dict(
            "os.environ",
            {
                "HTTPS_PROXY": "http://127.0.0.1:9",
                "HTTP_PROXY": "http://127.0.0.1:9",
                "ALL_PROXY": "http://127.0.0.1:9",
                "SKY_SALES_PROXY": "",
            },
            clear=False,
        ):
            client = SkySalesClient()
        self.assertFalse(client.http.trust_env)
        self.assertEqual(client.http.proxies, {})

        with patch.dict(
            "os.environ",
            {"SKY_SALES_PROXY": "http://127.0.0.1:8888"},
            clear=False,
        ):
            client2 = SkySalesClient()
        self.assertEqual(
            client2.http.proxies.get("https"),
            "http://127.0.0.1:8888",
        )

    def test_direct_mode_skips_ipify(self):
        client = SkySalesClient(
            session=SkySalesSession(
                jwt_token="jwt",
                session_id="sid",
                eot_user_id=1,
                user_name="u",
            )
        )
        with patch.dict("os.environ", {"SKY_SALES_PROXY": ""}, clear=False):
            with patch.object(client, "detect_egress_ip") as detect:
                self.assertEqual(client.current_egress_ip(), "")
                detect.assert_not_called()

            with patch.object(client, "detect_egress_ip", return_value="1.2.3.4") as detect:
                with patch.dict(
                    "os.environ",
                    {"SKY_SALES_PROXY": "http://127.0.0.1:8888"},
                    clear=False,
                ):
                    self.assertEqual(client.current_egress_ip(), "1.2.3.4")
                detect.assert_called_once()

        with patch.object(client, "current_egress_ip", return_value=""):
            with patch.object(client, "_probe_session") as probe:
                with patch.object(client, "save_session_file"):
                    client.ensure_authenticated("u", "p")
            probe.assert_called_once()
            # Must not fall through to force_new_session / ipify.
            self.assertEqual(client.session.jwt_token, "jwt")
