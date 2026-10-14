import time
from datetime import date
from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase

from providers.accounts import Credentials
from providers.adapters.sky.client import (
    SkySalesClient,
    SkySalesError,
    SkySalesSession,
    SkySalesSessionError,
    generate_totp_code,
    resolve_otp_code,
)
from providers.contracts import PermanentError
from providers.redis_conn import get_redis
from providers.sessions import SessionStore

GET_CREDS = "providers.adapters.sky.client.get_credentials"
TOTP_SECRET = "JBSWY3DPEHPK3PXP"


def _creds(proxy: str = "", totp: str = "") -> Credentials:
    extra = {"proxy_url": proxy} if proxy else {}
    return Credentials("user", "pass", totp, extra)


def _ready_client(**kwargs) -> SkySalesClient:
    return SkySalesClient(
        session=SkySalesSession(
            jwt_token="jwt", session_id="sid", eot_user_id=1550, user_name="u"
        ),
        **kwargs,
    )


class SkySalesClientTests(SimpleTestCase):
    def setUp(self):
        get_redis().flushall()
        # Hermetic default: no DB / env lookups; tests override as needed.
        patcher = patch(GET_CREDS, return_value=_creds())
        self.get_creds = patcher.start()
        self.addCleanup(patcher.stop)

    def test_credentials_looked_up_once_per_client(self):
        client = SkySalesClient()
        client.proxy_url
        client.current_egress_ip()
        client._totp_secret()
        self.assertEqual(self.get_creds.call_count, 1)

    def test_missing_credentials_mean_direct_mode(self):
        self.get_creds.side_effect = PermanentError("config_error")
        client = SkySalesClient()
        self.assertEqual(client.proxy_url, "")
        self.assertEqual(client._totp_secret(), "")

    def test_totp_code_comes_from_account_secret(self):
        self.assertEqual(len(generate_totp_code(TOTP_SECRET)), 6)
        with self.assertRaises(SkySalesError) as ctx:
            generate_totp_code("")
        self.assertEqual(str(ctx.exception), "OTP_REQUIRED")
        self.assertEqual(resolve_otp_code(" 123456 ", ""), "123456")

    def test_auto_login_uses_client_totp_secret(self):
        client = SkySalesClient(credentials=_creds(totp=TOTP_SECRET))
        with patch.object(client, "begin_login"), patch.object(
            client, "confirm_otp"
        ) as confirm:
            client.auto_login("user", "pass")
        code = confirm.call_args.args[0]
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())

    def test_auto_login_retries_once_after_otp_rejected(self):
        client = SkySalesClient(credentials=_creds(totp=TOTP_SECRET))
        with patch.object(client, "begin_login") as begin, patch.object(
            client,
            "confirm_otp",
            side_effect=[SkySalesError("OTP rejected: {}"), None],
        ) as confirm:
            client.auto_login("user", "pass")
        self.assertEqual(begin.call_count, 2)
        self.assertEqual(confirm.call_count, 2)

    def test_auto_login_retries_only_once(self):
        client = SkySalesClient(credentials=_creds(totp=TOTP_SECRET))
        with patch.object(client, "begin_login") as begin, patch.object(
            client, "confirm_otp", side_effect=SkySalesError("OTP rejected: {}")
        ):
            with self.assertRaises(SkySalesError):
                client.auto_login("user", "pass")
        self.assertEqual(begin.call_count, 2)

    def test_otp_retry_skipped_when_it_would_not_fit_the_deadline(self):
        client = SkySalesClient(credentials=_creds(totp=TOTP_SECRET))
        client._login_deadline = time.monotonic() + 30
        with patch.object(client, "begin_login") as begin, patch.object(
            client,
            "confirm_otp",
            side_effect=[SkySalesError("OTP rejected: {}"), None],
        ):
            with self.assertRaises(SkySalesError):
                client.auto_login("user", "pass")
        self.assertEqual(begin.call_count, 1)

        client._login_deadline = time.monotonic() + 61
        with patch.object(client, "begin_login") as begin, patch.object(
            client,
            "confirm_otp",
            side_effect=[SkySalesError("OTP rejected: {}"), None],
        ):
            client.auto_login("user", "pass")
        self.assertEqual(begin.call_count, 2)

    def test_login_requests_use_short_timeouts_and_normal_requests_do_not(self):
        client = SkySalesClient(credentials=_creds(totp=TOTP_SECRET))
        timeouts = []

        def fake_post(url, json, timeout):
            timeouts.append(timeout)
            resp = MagicMock()
            path = url.split(":8888", 1)[1]
            resp.json.return_value = {
                "/ipa/apis/json/general/login": {"result": "SUCCESS", "jwtToken": "j", "sessionId": "s"},
                "/ipa/apis/json/factor/init2fa": {"result": "SUCCESS"},
                "/ipa/apis/json/factor/confirm2fa": {"result": "SUCCESS", "confirmed": True},
                "/ipa/apis/json/general/getLoginInfo": {"result": "SUCCESS", "userId": 1},
                "/ipa/apis/json/general/genJwtToken": {"result": "SUCCESS", "jwtToken": "j2"},
            }[path]
            return resp

        with patch("requests.Session.post", side_effect=fake_post):
            client.force_new_session("user", "pass")
        self.assertEqual(len(timeouts), 5)
        for connect, read in timeouts:
            self.assertEqual(connect, 5)
            self.assertLessEqual(read, 15)

        with patch.object(client.http, "post", side_effect=fake_post):
            client._refresh_jwt()
        self.assertEqual(timeouts[-1], 90)

    def test_login_deadline_stops_further_requests(self):
        from providers.adapters.sky import client as client_module

        # Worst case = deadline + one request (connect + read) must fit the lock.
        from providers.sessions import LOGIN_LOCK_TIMEOUT_SECONDS

        self.assertLess(
            client_module.LOGIN_DEADLINE_SECONDS
            + client_module.LOGIN_CONNECT_TIMEOUT_SECONDS
            + client_module.LOGIN_READ_TIMEOUT_SECONDS,
            LOGIN_LOCK_TIMEOUT_SECONDS,
        )
        client = SkySalesClient(credentials=_creds(totp=TOTP_SECRET))
        with patch.object(client_module, "LOGIN_DEADLINE_SECONDS", -1), patch(
            "requests.Session.post"
        ) as post:
            with self.assertRaises(SkySalesError) as ctx:
                client.force_new_session("user", "pass")
        self.assertIn("deadline", str(ctx.exception))
        post.assert_not_called()
        self.assertIsNone(client._login_deadline)

    def test_begin_login_does_not_persist_pending_otp(self):
        client = SkySalesClient()
        with patch.object(client, "login", return_value={}):
            client.begin_login("user", "pass")
        self.assertTrue(client.session.pending_otp)
        self.assertIsNone(SessionStore("sky").load())

    def test_force_new_session_saves_only_complete_session(self):
        client = SkySalesClient(credentials=_creds(totp=TOTP_SECRET))
        saved_during_login = []

        def fake_auto_login(username, password, otp_code=""):
            client.session.jwt_token = "jwt"
            client.session.eot_user_id = 1
            client.save_session()
            saved_during_login.append(SessionStore("sky").load())

        with patch.object(client, "auto_login", side_effect=fake_auto_login):
            client.force_new_session("user", "pass")
        self.assertEqual(saved_during_login, [None])
        data = SessionStore("sky").load()
        self.assertEqual(data["jwt_token"], "jwt")
        self.assertGreater(data["logged_in_at"], 0)

    def test_stale_client_does_not_overwrite_newer_login(self):
        stale = _ready_client()
        stale.session.logged_in_at = time.time() - 600
        SessionStore("sky").save({"jwt_token": "fresh", "logged_in_at": time.time()})
        stale.save_session()
        self.assertEqual(SessionStore("sky").load()["jwt_token"], "fresh")

    def test_from_store_roundtrip(self):
        client = _ready_client()
        client.session.logged_in_at = time.time()
        client.http.cookies.set("JSESSIONID", "abc")
        client.save_session()
        loaded = SkySalesClient.from_store(_creds())
        self.assertEqual(loaded.session.jwt_token, "jwt")
        self.assertEqual(loaded.session.eot_user_id, 1550)
        self.assertEqual(loaded.http.cookies.get("JSESSIONID"), "abc")
        self.assertTrue(loaded.is_session_usable())

    def test_refresh_sim_session_error_propagates(self):
        client = _ready_client()
        with patch.object(
            client, "_refresh_sim_once", side_effect=SkySalesSessionError("REJECTED on /x")
        ):
            with self.assertRaises(SkySalesSessionError):
                client.refresh_sim("0599000000")

    def test_refresh_sim_maps_other_errors_to_reasons(self):
        client = _ready_client()
        cases = {
            "CreateNewTaskWithBalanceCheck failed: Task not created ERROR": "not_eligible",
            "GetSubscriberCosts failed: {}": "api_error",
        }
        for message, reason in cases.items():
            with patch.object(
                client, "_refresh_sim_once", side_effect=SkySalesError(message)
            ):
                self.assertEqual(client.refresh_sim("0599000000")["reason"], reason)

    def test_post_http_status_error_is_session_error_and_transport_error_propagates(self):
        client = _ready_client()
        bad = MagicMock()
        bad.raise_for_status.side_effect = requests.HTTPError("401")
        with patch.object(client.http, "post", return_value=bad):
            with self.assertRaises(SkySalesSessionError):
                client.generic_api("X", [])
        with patch.object(client.http, "post", side_effect=requests.ConnectionError("down")):
            with self.assertRaises(requests.ConnectionError):
                client.generic_api("X", [])

    def test_fetch_balance_report_calls_template_22(self):
        client = _ready_client()
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

    def test_http_ignores_env_proxy_unless_account_proxy(self):
        with patch.dict(
            "os.environ",
            {
                "HTTPS_PROXY": "http://127.0.0.1:9",
                "HTTP_PROXY": "http://127.0.0.1:9",
                "ALL_PROXY": "http://127.0.0.1:9",
            },
            clear=False,
        ):
            client = SkySalesClient()
        self.assertFalse(client.http.trust_env)
        self.assertEqual(client.http.proxies, {})

        client2 = SkySalesClient(credentials=_creds(proxy="http://127.0.0.1:8888"))
        self.assertEqual(
            client2.http.proxies.get("https"),
            "http://127.0.0.1:8888",
        )

    def test_direct_mode_skips_ipify(self):
        client = _ready_client(credentials=_creds())
        with patch.object(client, "detect_egress_ip") as detect:
            self.assertEqual(client.current_egress_ip(), "")
            self.assertTrue(client.is_session_usable())
            detect.assert_not_called()

        proxied = _ready_client(credentials=_creds(proxy="http://127.0.0.1:8888"))
        with patch.object(proxied, "detect_egress_ip", return_value="1.2.3.4") as detect:
            self.assertEqual(proxied.current_egress_ip(), "1.2.3.4")
            detect.assert_called_once()
