from __future__ import annotations

import time
from http.cookiejar import Cookie
from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase, TestCase

from providers.accounts import get_credentials
from providers.adapters.areen.adapter import AreenAdapter
from providers.adapters.areen.client import (
    DEVICE_UNKNOWN,
    LOGIN_PAGE,
    OTP_REJECTED,
    OTP_SENT,
    AreenOtpRequired,
    AreenPortal,
    load_device,
    load_pending,
    save_device,
)
from providers.contracts import AuthRequired, TransientError
from providers.crypto import encrypt
from providers.forms import ProviderAccountForm
from providers.models import ProviderConnection
from providers.redis_conn import get_redis
from providers.sessions import _cooldown_key, reset_session, run_authenticated

LOGIN_HTML = (
    'O0=new Ext.window.Window({id:"O0_id",title:"frmNewLogin"});'
    'O0.nm="O0";_cdo_("frmNewLogin")'
    '_cdo_("edtUserName",O28,null)'
    '_cdo_("edtPassword",O2C,null)'
    '_cdo_("btnLogin",O5C,null)'
)
VERIFY_HTML = (
    'OBB=new Ext.window.Window({id:"OBB_id",title:"frmVerifyCodeSMS"});'
    '_cdo_("edtCode",OD6,null)'
    '_cdo_("btnSave",OF2,null)'
)
UNKNOWN = "هذا الجهاز غير معرف , هل تريد تعريفه ؟"


def _response(text, *, session_id="", cookies=()):
    response = requests.Response()
    response.status_code = 200
    response.encoding = "utf-8"
    response._content = text.encode()
    response.url = LOGIN_PAGE
    if session_id:
        response.headers["session_id"] = session_id
    for name, value, expires in cookies:
        response.cookies.set_cookie(
            Cookie(
                0,
                name,
                value,
                None,
                False,
                "agents.areen.net",
                True,
                False,
                "/",
                True,
                False,
                expires,
                False,
                None,
                None,
                {},
            )
        )
    return response


class Scripted(requests.Session):
    def __init__(self, responses):
        super().__init__()
        self._responses = list(responses)
        self.sent = []

    def get(self, url, **kwargs):
        self.sent.append(("GET", url, None))
        return self._responses.pop(0)

    def post(self, url, data=None, **kwargs):
        body = data.decode() if isinstance(data, bytes) else data
        self.sent.append(("POST", url, body))
        response = self._responses.pop(0)
        for cookie in response.cookies:
            self.cookies.set_cookie(cookie)
        return response


def _page():
    return _response(LOGIN_HTML, session_id="SID")


class PortalSequenceTests(TestCase):
    def setUp(self):
        get_redis().flushall()

    def test_known_device_stops_at_the_password_click(self):
        expires = int(time.time()) + 3600
        http = Scripted(
            [
                _page(),
                _response("xEncode"),
                _response("show"),
                _response(
                    "MainForm",
                    cookies=(
                        ("ARN_Session", "session-a", expires),
                        ("SIDX_CXR", "session-b", expires),
                    ),
                ),
            ]
        )
        portal = AreenPortal(http)
        body = portal.sign_in(
            portal.open_login(), "user", "pw", prompt_device=True
        )
        self.assertIn("MainForm", body)
        posts = [item[2] for item in http.sent if item[0] == "POST"]
        self.assertEqual(len(posts), 3)
        self.assertIn("_seq_=0", posts[0])
        self.assertIn("Evt=cinfo", posts[0])
        self.assertIn("ci=", posts[0])
        self.assertIn("_seq_=1", posts[1])
        self.assertIn("Evt=show", posts[1])
        self.assertIn("_seq_=2", posts[2])
        self.assertIn("Obj=O5C", posts[2])
        self.assertIn("Evt=click", posts[2])
        self.assertNotIn("password=", posts[2])
        self.assertNotIn("ButtonID=yes", posts[2])
        device = portal.device_cookies()
        self.assertEqual(device["cookies"]["ARN_Session"], "session-a")
        self.assertEqual(device["cookies"]["SIDX_CXR"], "session-b")

    def test_script_cookies_count_as_the_device(self):
        expires_in = 10 * 24 * 3600
        body = (
            "MainForm;"
            'document.cookie="ARN_Session=session-a; Path=/; Max-Age=%s";'
            'document.cookie="SIDX_CXR=session-b; Path=/; Max-Age=%s";'
        ) % (expires_in, expires_in)
        http = Scripted(
            [_page(), _response("x"), _response("show"), _response(body)]
        )
        portal = AreenPortal(http)
        portal.sign_in(portal.open_login(), "user", "pw", prompt_device=False)
        device = portal.device_cookies()
        self.assertEqual(device["cookies"]["ARN_Session"], "session-a")
        self.assertGreater(device["expires_at"], time.time() + expires_in - 5)

    def test_unknown_device_without_prompt_does_not_send_sms(self):
        http = Scripted([_page(), _response("x"), _response("show"), _response(UNKNOWN)])
        portal = AreenPortal(http)
        with self.assertRaises(Exception) as caught:
            portal.sign_in(portal.open_login(), "user", "pw", prompt_device=False)
        self.assertEqual(str(caught.exception), DEVICE_UNKNOWN)
        self.assertEqual(len([item for item in http.sent if item[0] == "POST"]), 3)
        self.assertNotIn("ButtonID=yes", http.sent[-1][2])

    def test_prompt_sends_sms_and_keeps_the_code_step(self):
        http = Scripted(
            [
                _page(),
                _response("x"),
                _response("show"),
                _response(UNKNOWN),
                _response(VERIFY_HTML),
            ]
        )
        portal = AreenPortal(http)
        with self.assertRaises(AreenOtpRequired) as caught:
            portal.sign_in(portal.open_login(), "user", "pw", prompt_device=True)
        self.assertEqual(str(caught.exception), OTP_SENT)
        self.assertIn("ButtonID=yes", http.sent[-1][2])
        self.assertIn("_a_=1", http.sent[-1][2])
        self.assertIn("_seq_=3", http.sent[-1][2])
        pending = load_pending()
        self.assertEqual(pending["code_field"], "OD6")
        self.assertEqual(pending["save_button"], "OF2")
        self.assertEqual(pending["window"], "OBB")
        self.assertEqual(pending["seq"], 4)

    def test_verify_window_is_the_sms_dialog_not_the_login_window(self):
        earlier = (
            'O0=new Ext.window.Window({id:"O0_id",title:"frmNewLogin"});'
            + VERIFY_HTML
        )
        http = Scripted(
            [_page(), _response("x"), _response("show"), _response(UNKNOWN), _response(earlier)]
        )
        portal = AreenPortal(http)
        with self.assertRaises(AreenOtpRequired):
            portal.sign_in(portal.open_login(), "user", "pw", prompt_device=True)
        self.assertEqual(load_pending()["window"], "OBB")

    def test_wrong_code_keeps_the_pending_step(self):
        http = Scripted([_response("changed"), _response("bad code")])
        portal = AreenPortal(http)
        from providers.adapters.areen.client import save_pending

        save_pending(
            {
                "session_id": "SID",
                "seq": 4,
                "window": "OBB",
                "code_field": "OD6",
                "save_button": "OF2",
                "cookies": {},
            }
        )
        with self.assertRaises(Exception) as caught:
            portal.submit_code("111111")
        self.assertEqual(str(caught.exception), OTP_REJECTED)
        pending = load_pending()
        self.assertIsNotNone(pending)
        self.assertEqual(pending["seq"], 6)
        self.assertIn("Evt=change", http.sent[0][2])
        self.assertIn("Obj=OD6", http.sent[0][2])
        self.assertIn("_seq_=4", http.sent[0][2])
        self.assertIn("Evt=click", http.sent[1][2])
        self.assertIn("Obj=OF2", http.sent[1][2])
        self.assertIn("_seq_=5", http.sent[1][2])
        self.assertNotIn("111111=", http.sent[0][2])
        self.assertNotIn("111111=", http.sent[1][2])


class AdapterLoginTests(TestCase):
    def setUp(self):
        get_redis().flushall()
        ProviderConnection.objects.filter(provider_key="areen").delete()
        ProviderConnection.objects.create(
            provider_key="areen", username="user", secret_encrypted=encrypt("pw")
        )

    def test_login_saves_device_cookies_outside_the_short_session(self):
        expires = time.time() + 10 * 24 * 3600
        device = {
            "cookies": {"ARN_Session": "session-a", "SIDX_CXR": "session-b"},
            "expires_at": expires,
        }
        portal = MagicMock()
        portal.device_cookies.return_value = device
        with patch("providers.adapters.areen.adapter.AreenPortal", return_value=portal):
            adapter = AreenAdapter()
            adapter.force_login = True
            adapter.prompt_device = True
            adapter.login()
        self.assertFalse(adapter.force_login)
        portal.sign_in.assert_called_once()
        self.assertTrue(portal.sign_in.call_args.kwargs["prompt_device"])
        reset_session("areen")
        saved = load_device(get_credentials("areen"))
        self.assertEqual(saved["cookies"]["SIDX_CXR"], "session-b")
        adapter.ping()

    def test_ping_with_a_saved_device_does_not_ask_for_login(self):
        save_device(
            {
                "cookies": {"ARN_Session": "session-a", "SIDX_CXR": "session-b"},
                "expires_at": time.time() + 10 * 24 * 3600,
            }
        )
        AreenAdapter().ping()

    def test_old_cookies_stay_saved_and_do_not_ask_for_a_new_code(self):
        save_device(
            {
                "cookies": {"ARN_Session": "session-a", "SIDX_CXR": "session-b"},
                "expires_at": time.time() - 10,
            }
        )
        self.assertEqual(
            load_device(get_credentials("areen"))["cookies"]["ARN_Session"], "session-a"
        )
        adapter = AreenAdapter()
        with self.assertRaises(AuthRequired):
            adapter.ping()
        portal = MagicMock()
        portal.http = requests.Session()
        portal.device_cookies.return_value = {
            "cookies": {"ARN_Session": "session-a", "SIDX_CXR": "session-b"},
            "expires_at": time.time() + 10 * 24 * 3600,
        }
        with patch("providers.adapters.areen.adapter.AreenPortal", return_value=portal):
            adapter.prompt_device = True
            adapter.login()
        self.assertFalse(portal.sign_in.call_args.kwargs["prompt_device"])
        self.assertEqual(portal.http.cookies.get("ARN_Session"), "session-a")
        portal.submit_code.assert_not_called()

    def test_code_is_submitted_on_the_pending_session(self):
        portal = MagicMock()
        portal.device_cookies.return_value = {
            "cookies": {"ARN_Session": "session-a", "SIDX_CXR": "session-b"},
            "expires_at": time.time() + 3600,
        }
        with patch("providers.adapters.areen.adapter.AreenPortal", return_value=portal):
            adapter = AreenAdapter()
            adapter.otp_code = "111111"
            adapter.login()
        portal.submit_code.assert_called_once_with("111111")
        portal.sign_in.assert_not_called()

    def test_sms_request_does_not_start_a_cooldown(self):
        portal = MagicMock()
        portal.sign_in.side_effect = AreenOtpRequired(OTP_SENT)
        with patch("providers.adapters.areen.adapter.AreenPortal", return_value=portal):
            adapter = AreenAdapter()
            adapter.force_login = True
            adapter.prompt_device = True
            with self.assertRaises(TransientError):
                run_authenticated(adapter, adapter.ping)
        self.assertFalse(get_redis().exists(_cooldown_key("areen")))
        account = ProviderConnection.objects.get(provider_key="areen")
        self.assertIn("رمز التحقق", account.last_error)
        self.assertNotIn("pw", account.last_error)
        self.assertFalse(account.last_login_ok)

    def test_refresh_stays_on_the_public_endpoint(self):
        with patch("providers.adapters.areen.adapter.requests.post") as post:
            post.return_value = MagicMock(text="{}", json=lambda: {}, status_code=200)
            AreenAdapter().refresh("0590000000")
        self.assertEqual(
            post.call_args.args[0], "https://api.areen.net/api/common/RefreshMobileNumber"
        )
        self.assertEqual(post.call_args.kwargs["data"], {"MobNumber": "0590000000"})


class AccountFormTests(TestCase):
    def setUp(self):
        ProviderConnection.objects.filter(provider_key="areen").delete()

    def test_areen_form_has_no_totp_and_drops_the_device_when_the_password_changes(self):
        form = ProviderAccountForm(
            {"username": "user", "password": "pw"}, provider_key="areen"
        )
        self.assertNotIn("totp_secret", form.fields)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        save_device(
            {
                "cookies": {"ARN_Session": "session-a", "SIDX_CXR": "session-b"},
                "expires_at": time.time() + 3600,
            }
        )
        again = ProviderAccountForm(
            {"username": "user", "password": ""}, provider_key="areen"
        )
        self.assertTrue(again.is_valid(), again.errors)
        again.save()
        self.assertIsNotNone(load_device(get_credentials("areen")))
        changed = ProviderAccountForm(
            {"username": "user", "password": "next"}, provider_key="areen"
        )
        self.assertTrue(changed.is_valid(), changed.errors)
        changed.save()
        self.assertIsNone(load_device(get_credentials("areen")))


class EnqueueTests(TestCase):
    def setUp(self):
        get_redis().flushall()
        ProviderConnection.objects.filter(provider_key="areen").delete()
        ProviderConnection.objects.create(
            provider_key="areen", username="user", secret_encrypted=encrypt("pw")
        )

    def test_start_passes_the_code_to_the_worker(self):
        from providers import login_test, tasks

        with patch.object(tasks.test_connection, "delay") as delay:
            self.assertEqual(login_test.start("areen", "111111"), "started")
        delay.assert_called_once_with("areen", "111111")


class ParseTests(SimpleTestCase):
    def test_login_page_without_a_session_is_rejected(self):
        from providers.adapters.areen.client import parse_login_page

        with self.assertRaises(Exception):
            parse_login_page(LOGIN_HTML, "")
