from datetime import date
from unittest.mock import MagicMock, patch

from django.test import TestCase

from providers.accounts import Credentials
from providers.adapters.layan.client import LayanClient, LayanSessionError
from providers.contracts import AuthRequired
from providers.sessions import SessionStore


def _creds() -> Credentials:
    return Credentials("hamza", "secret", "JBSWY3DPEHPK3PXP", {})


class LayanClientTests(TestCase):
    def setUp(self):
        from providers.redis_conn import get_redis

        get_redis().flushall()

    def test_login_with_totp_stores_a_usable_session(self):
        client = LayanClient(credentials=_creds())
        login = MagicMock()
        login.status_code = 200
        login.json.return_value = {
            "statusCode": 202,
            "data": {"requestId": "req-1", "verificationMethod": 2},
        }
        verify = MagicMock()
        verify.status_code = 200
        verify.json.return_value = {"jwt": _jwt(exp=2_000_000_000)}
        with patch.object(client, "_post", side_effect=[login, verify]) as post, patch(
            "providers.adapters.layan.client._totp_code", return_value="123456"
        ):
            client.force_new_session("hamza", "secret")
        self.assertEqual(post.call_args_list[0].args[0], "Auth/Login")
        self.assertEqual(post.call_args_list[1].args[0], "Auth/VerifyCode")
        self.assertEqual(post.call_args_list[1].args[1]["code"], "123456")
        stored = SessionStore("layan").load()
        self.assertEqual(stored["jwt"].count("."), 2)
        self.assertTrue(LayanClient.from_store(_creds()).is_session_usable())

    def test_saved_session_survives_when_memory_is_wiped(self):
        from providers.crypto import encrypt
        from providers.models import ProviderConnection
        from providers.redis_conn import get_redis
        from providers.sessions import reset_session

        connection = ProviderConnection.for_key("layan")
        connection.username = "hamza"
        connection.secret_encrypted = encrypt("secret")
        connection.totp_secret_encrypted = encrypt("JBSWY3DPEHPK3PXP")
        connection.save()
        client = LayanClient(credentials=_creds())
        login = MagicMock(status_code=200)
        login.json.return_value = {"statusCode": 200, "data": {"jwt": _jwt(exp=2_000_000_000)}}
        with patch.object(client, "_post", return_value=login):
            client.force_new_session("hamza", "secret")
        token = client.jwt
        get_redis().flushall()
        restored = LayanClient.from_store(_creds())
        self.assertIsNotNone(restored)
        self.assertEqual(restored.jwt, token)
        self.assertTrue(restored.is_session_usable())
        from providers.adapters.layan.adapter import LayanAdapter

        self.assertEqual(LayanAdapter()._stored_client().jwt, token)
        reset_session("layan")
        self.assertIsNone(LayanClient.from_store(_creds()))

    def test_expired_session_asks_for_login(self):
        from providers.adapters.layan.adapter import LayanAdapter
        from providers.crypto import encrypt
        from providers.models import ProviderConnection

        connection = ProviderConnection.for_key("layan")
        connection.username = "user"
        connection.secret_encrypted = encrypt("pass")
        connection.save()
        SessionStore("layan").save({"jwt": "a.b.c", "expires_at": 1, "logged_in_at": 1})
        with self.assertRaises(AuthRequired):
            LayanAdapter().fetch_accounting_rows(date(2026, 10, 1), date(2026, 10, 2))

    def test_accounting_page_uses_charged_rows(self):
        client = LayanClient(credentials=_creds(), jwt="token", expires_at=2_000_000_000)
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "recordsFiltered": 1,
            "data": [{"phoneNumber": "0511111111", "cost": "30.00"}],
        }
        with patch.object(client, "_post", return_value=response) as post:
            rows = client.fetch_accounting(date(2026, 10, 1), date(2026, 10, 18))
        body = post.call_args.args[1]
        self.assertTrue(body["customSearch"]["Charged"])
        self.assertEqual(body["customSearch"]["From"], "2026-10-01T00:00:00.000Z")
        self.assertEqual(rows[0]["phoneNumber"], "0511111111")

    def test_unauthorized_report_is_a_session_error(self):
        client = LayanClient(credentials=_creds(), jwt="token", expires_at=2_000_000_000)
        response = MagicMock()
        response.status_code = 401
        with patch.object(client, "_post", return_value=response):
            with self.assertRaises(LayanSessionError):
                client.fetch_accounting(date(2026, 10, 1), date(2026, 10, 2))


def _jwt(exp: int) -> str:
    import base64
    import json

    def part(obj):
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return ".".join((part({"alg": "none"}), part({"exp": exp}), "sig"))
