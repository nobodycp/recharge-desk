from __future__ import annotations

import os
from unittest import mock

import pyotp
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserProfile
from providers.accounts import Credentials, get_credentials, record_login
from providers.contracts import PermanentError
from providers.crypto import decrypt, encrypt
from providers.forms import ProviderAccountForm
from providers.models import ProviderConnection

User = get_user_model()

SKY_ENV_KEYS = (
    "SKY_SALES_USER",
    "SKY_SALES_PASSWORD",
    "SKY_SALES_TOTP_SECRET",
    "SKY_SALES_OTP_AUTH_URI",
    "SKY_SALES_PROXY",
)

TOTP_SECRET = "JBSWY3DPEHPK3PXP"


def _clean_env(**overrides: str):
    """Patch os.environ so no SKY_SALES_* leaks in, then apply overrides."""
    env = {k: v for k, v in os.environ.items() if k not in SKY_ENV_KEYS}
    env.update(overrides)
    return mock.patch.dict(os.environ, env, clear=True)


class CryptoTests(TestCase):
    def test_encrypt_roundtrip_and_ciphertext_differs(self):
        token = encrypt("s3cret-pass")
        self.assertNotEqual(token, "s3cret-pass")
        self.assertNotIn("s3cret-pass", token)
        self.assertEqual(decrypt(token), "s3cret-pass")
        # Fernet is non-deterministic.
        self.assertNotEqual(token, encrypt("s3cret-pass"))

    def test_dev_fallback_key_derived_from_secret_key(self):
        with self.settings(PROVIDER_SECRETS_KEY=""):
            token = encrypt("abc")
            self.assertEqual(decrypt(token), "abc")


OTHER_KEY = "Zm9yLXRlc3RzLW9ubHktb3RoZXIta2V5LTMyYnl0ZXM="


class UndecryptableSecretsTests(TestCase):
    """Secrets saved with one key must not break reads under another key."""

    @classmethod
    def setUpTestData(cls):
        cls.manager = User.objects.create_user("mgr2", password="x")
        UserProfile.objects.update_or_create(
            user=cls.manager,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )

    def setUp(self):
        ProviderConnection.objects.all().delete()
        form = ProviderAccountForm(
            {
                "username": "u",
                "password": "old-pass",
                "totp_secret": TOTP_SECRET,
                "proxy_url": "http://old:1",
                "is_enabled": "on",
            },
            provider_key="sky",
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.client = Client()
        self.client.force_login(self.manager)

    def test_admin_tab_renders_and_credentials_fail_closed_then_recover(self):
        with override_settings(PROVIDER_SECRETS_KEY=OTHER_KEY):
            page = self.client.get(reverse("phone_refresh:providers_index") + "?tab=sky")
            self.assertEqual(page.status_code, 200)

            # No silent fallback to env when the DB row is unreadable.
            with _clean_env(SKY_SALES_USER="envu", SKY_SALES_PASSWORD="envp"):
                with self.assertLogs("providers.accounts", level="WARNING") as logs:
                    with self.assertRaises(PermanentError) as ctx:
                        get_credentials("sky")
            self.assertEqual(str(ctx.exception), "config_error")
            self.assertNotIn("old-pass", "".join(logs.output))

            # record_login must not raise (and must not mask the real error).
            record_login("sky", False, "login failed")
            self.assertEqual(
                ProviderConnection.objects.get(provider_key="sky").last_error, "login failed"
            )

            # Admin can overwrite the password; unreadable TOTP/proxy are dropped.
            form = ProviderAccountForm(
                {"username": "u", "password": "new-pass", "is_enabled": "on"},
                provider_key="sky",
            )
            self.assertTrue(form.is_valid(), form.errors)
            form.save()
            creds = get_credentials("sky")
            self.assertEqual(creds.password, "new-pass")
            self.assertEqual(creds.totp_secret, "")
            self.assertEqual(creds.extra, {})

    def test_password_required_when_stored_one_is_unreadable(self):
        with override_settings(PROVIDER_SECRETS_KEY=OTHER_KEY):
            form = ProviderAccountForm({"username": "u", "is_enabled": "on"}, provider_key="sky")
            self.assertFalse(form.is_valid())
            self.assertIn("password", form.errors)


class CredentialsReprTests(TestCase):
    def test_repr_hides_secrets(self):
        creds = Credentials("alice", "pw-secret-1", "TOTPSECRET234567", {"proxy_url": "http://px:9"})
        text = repr(creds)
        for secret in ("pw-secret-1", "TOTPSECRET234567", "px:9"):
            self.assertNotIn(secret, text)
        self.assertIn("alice", text)


class GetCredentialsTests(TestCase):
    def setUp(self):
        # A migration may have imported env values into the test DB.
        ProviderConnection.objects.all().delete()

    def _create_account(self, **kw):
        form = ProviderAccountForm(
            {
                "username": kw.get("username", "dbuser"),
                "password": kw.get("password", "dbpass"),
                "totp_secret": kw.get("totp_secret", TOTP_SECRET),
                "proxy_url": kw.get("proxy_url", "http://proxy.example:8080"),
                "is_enabled": "on",
            },
            provider_key="sky",
        )
        self.assertTrue(form.is_valid(), form.errors)
        return form.save()

    def test_get_credentials_prefers_db_account_over_env(self):
        self._create_account()
        with _clean_env(SKY_SALES_USER="envuser", SKY_SALES_PASSWORD="envpass"):
            creds = get_credentials("sky")
        self.assertEqual(
            creds,
            Credentials(
                username="dbuser",
                password="dbpass",
                totp_secret=TOTP_SECRET,
                extra={"proxy_url": "http://proxy.example:8080"},
            ),
        )

    def test_get_credentials_env_fallback_for_sky(self):
        with _clean_env(
            SKY_SALES_USER="envuser",
            SKY_SALES_PASSWORD="envpass",
            SKY_SALES_TOTP_SECRET="jbsw y3dp ehpk 3pxp",
            SKY_SALES_PROXY="http://envproxy:1",
        ):
            creds = get_credentials("sky")
        self.assertEqual(creds.username, "envuser")
        self.assertEqual(creds.password, "envpass")
        self.assertEqual(creds.totp_secret, TOTP_SECRET)
        self.assertEqual(creds.extra, {"proxy_url": "http://envproxy:1"})

    def test_env_fallback_reads_totp_from_otpauth_uri(self):
        uri = pyotp.TOTP(TOTP_SECRET).provisioning_uri(name="u", issuer_name="Sky")
        with _clean_env(
            SKY_SALES_USER="u", SKY_SALES_PASSWORD="p", SKY_SALES_OTP_AUTH_URI=uri
        ):
            creds = get_credentials("sky")
        self.assertEqual(creds.totp_secret, TOTP_SECRET)

    def test_get_credentials_missing_raises_permanent_config_error(self):
        with _clean_env():
            with self.assertRaises(PermanentError) as ctx:
                get_credentials("sky")
        self.assertEqual(str(ctx.exception), "config_error")

    def test_get_credentials_disabled_account_raises_without_env_fallback(self):
        from providers.models import ProviderConnection

        self._create_account()
        ProviderConnection.objects.filter(provider_key="sky").update(is_enabled=False)
        with _clean_env(SKY_SALES_USER="envuser", SKY_SALES_PASSWORD="envpass"):
            with self.assertRaises(PermanentError) as ctx:
                get_credentials("sky")
        self.assertEqual(str(ctx.exception), "provider_disabled")

    def test_get_credentials_other_provider_has_no_env_fallback(self):
        with _clean_env(SKY_SALES_USER="u", SKY_SALES_PASSWORD="p"):
            with self.assertRaises(PermanentError):
                get_credentials("layan")


class ImportSkyEnvMigrationTests(TestCase):
    def setUp(self):
        ProviderConnection.objects.all().delete()
        from importlib import import_module

        from django.apps import apps

        class _RenamedModelApps:
            """Migration 0002 asks for ``ProviderAccount``; the model is now
            ``ProviderConnection`` (same table)."""

            @staticmethod
            def get_model(app_label, model_name):
                if (app_label, model_name) == ("providers", "ProviderAccount"):
                    model_name = "ProviderConnection"
                return apps.get_model(app_label, model_name)

        self.run = lambda: import_module(
            "providers.migrations.0002_import_sky_env"
        ).import_sky_env(_RenamedModelApps, None)

    def test_imports_env_encrypted(self):
        with _clean_env(
            SKY_SALES_USER="envu",
            SKY_SALES_PASSWORD="envp-pass",
            SKY_SALES_TOTP_SECRET=TOTP_SECRET,
            SKY_SALES_PROXY="http://px:1",
        ):
            self.run()
        account = ProviderConnection.objects.get(provider_key="sky")
        self.assertEqual(account.username, "envu")
        self.assertNotIn("envp-pass", account.secret_encrypted)
        with _clean_env():
            creds = get_credentials("sky")
        self.assertEqual(creds.password, "envp-pass")
        self.assertEqual(creds.totp_secret, TOTP_SECRET)
        self.assertEqual(creds.extra, {"proxy_url": "http://px:1"})

    def test_does_not_overwrite_existing_or_import_incomplete_env(self):
        with _clean_env(SKY_SALES_USER="only-user"):
            self.run()
        self.assertFalse(ProviderConnection.objects.exists())
        ProviderConnection.objects.create(provider_key="sky", username="keep")
        with _clean_env(SKY_SALES_USER="envu", SKY_SALES_PASSWORD="envp"):
            self.run()
        self.assertEqual(ProviderConnection.objects.get(provider_key="sky").username, "keep")


class RecordLoginTests(TestCase):
    def setUp(self):
        ProviderConnection.objects.all().delete()
        form = ProviderAccountForm(
            {
                "username": "u",
                "password": "hunter2-pass",
                "totp_secret": TOTP_SECRET,
                "is_enabled": "on",
            },
            provider_key="sky",
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.account = form.save()

    def test_record_login_ok_clears_error(self):
        record_login("sky", False, "boom")
        record_login("sky", True)
        self.account.refresh_from_db()
        self.assertTrue(self.account.last_login_ok)
        self.assertEqual(self.account.last_error, "")
        self.assertIsNotNone(self.account.last_login_at)

    def test_record_login_failure_scrubs_secrets_and_truncates(self):
        record_login(
            "sky",
            False,
            "Wrong password hunter2-pass / totp %s %s" % (TOTP_SECRET, "x" * 800),
        )
        self.account.refresh_from_db()
        self.assertFalse(self.account.last_login_ok)
        self.assertNotIn("hunter2-pass", self.account.last_error)
        self.assertNotIn(TOTP_SECRET, self.account.last_error)
        self.assertIn("***", self.account.last_error)
        self.assertLessEqual(len(self.account.last_error), 500)

    def test_record_login_without_account_is_noop(self):
        ProviderConnection.objects.all().delete()
        record_login("sky", True)  # must not raise
        self.assertFalse(ProviderConnection.objects.exists())


class ProviderAccountFormTests(TestCase):
    def setUp(self):
        ProviderConnection.objects.all().delete()

    def _data(self, **kw):
        data = {"username": "u", "password": "first-pass", "is_enabled": "on"}
        data.update(kw)
        return data

    def test_blank_password_keeps_existing_secret(self):
        form = ProviderAccountForm(self._data(), provider_key="sky")
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        before = decrypt(ProviderConnection.objects.get(provider_key="sky").secret_encrypted)

        form2 = ProviderAccountForm(
            self._data(username="renamed", password=""), provider_key="sky"
        )
        self.assertTrue(form2.is_valid(), form2.errors)
        form2.save()
        account = ProviderConnection.objects.get(provider_key="sky")
        self.assertEqual(decrypt(account.secret_encrypted), before)
        self.assertEqual(before, "first-pass")
        self.assertEqual(account.username, "renamed")

    def test_blank_totp_and_proxy_keep_existing(self):
        form = ProviderAccountForm(
            self._data(totp_secret=TOTP_SECRET, proxy_url="http://p:1"),
            provider_key="sky",
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        form2 = ProviderAccountForm(self._data(password=""), provider_key="sky")
        self.assertTrue(form2.is_valid(), form2.errors)
        form2.save()
        creds = get_credentials("sky")
        self.assertEqual(creds.totp_secret, TOTP_SECRET)
        self.assertEqual(creds.extra["proxy_url"], "http://p:1")

    def test_clear_proxy_removes_proxy(self):
        form = ProviderAccountForm(self._data(proxy_url="http://p:1"), provider_key="sky")
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        form2 = ProviderAccountForm(
            self._data(password="", clear_proxy="on"), provider_key="sky"
        )
        self.assertTrue(form2.is_valid(), form2.errors)
        form2.save()
        self.assertEqual(get_credentials("sky").extra.get("proxy_url", ""), "")

    def test_password_required_for_new_account(self):
        form = ProviderAccountForm(self._data(password=""), provider_key="sky")
        self.assertFalse(form.is_valid())
        self.assertIn("password", form.errors)

    def test_otpauth_uri_is_reduced_to_secret(self):
        uri = pyotp.TOTP(TOTP_SECRET).provisioning_uri(name="u", issuer_name="Sky")
        form = ProviderAccountForm(self._data(totp_secret=uri), provider_key="sky")
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        account = ProviderConnection.objects.get(provider_key="sky")
        self.assertEqual(decrypt(account.totp_secret_encrypted), TOTP_SECRET)

    def test_invalid_totp_secret_rejected(self):
        form = ProviderAccountForm(self._data(totp_secret="not base32!!"), provider_key="sky")
        self.assertFalse(form.is_valid())
        self.assertIn("totp_secret", form.errors)

    def test_secrets_stored_encrypted(self):
        form = ProviderAccountForm(
            self._data(totp_secret=TOTP_SECRET, proxy_url="http://u:pw@p:1"),
            provider_key="sky",
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        account = ProviderConnection.objects.get(provider_key="sky")
        for blob in (
            account.secret_encrypted,
            account.totp_secret_encrypted,
            account.extra_encrypted,
        ):
            self.assertNotIn("first-pass", blob)
            self.assertNotIn(TOTP_SECRET, blob)
            self.assertNotIn("pw@p", blob)

    def test_form_never_renders_stored_secret_values(self):
        form = ProviderAccountForm(
            self._data(totp_secret=TOTP_SECRET, proxy_url="http://u:pw@p:1"),
            provider_key="sky",
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        html = str(ProviderAccountForm(provider_key="sky"))
        self.assertNotIn("first-pass", html)
        self.assertNotIn(TOTP_SECRET, html)
        self.assertNotIn("pw@p", html)


class AccountCardViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = User.objects.create_user("mgr", password="x")
        UserProfile.objects.update_or_create(
            user=cls.manager,
            defaults={"role": UserProfile.Role.MANAGEMENT, "is_active_profile": True},
        )
        cls.employee = User.objects.create_user("emp", password="x")
        UserProfile.objects.update_or_create(
            user=cls.employee,
            defaults={"role": UserProfile.Role.EMPLOYEE, "is_active_profile": True},
        )

    def setUp(self):
        ProviderConnection.objects.all().delete()
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.manager)
        self.index_url = reverse("phone_refresh:providers_index") + "?tab=sky"
        self.save_url = reverse("phone_refresh:provider_account_save", args=["sky"])

    def _post(self, data, client=None):
        client = client or self.client
        token = str(client.get(self.index_url).context["csrf_token"])
        return client.post(self.save_url, {**data, "csrfmiddlewaretoken": token})

    def test_account_card_never_renders_secret(self):
        response = self._post(
            {
                "username": "sky_user",
                "password": "PlainPassw0rd!",
                "totp_secret": TOTP_SECRET,
                "proxy_url": "http://px:secretproxypw@host:1",
                "is_enabled": "on",
            }
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.index_url)

        page = self.client.get(self.index_url)
        self.assertEqual(page.status_code, 200)
        body = page.content.decode()
        self.assertIn("sky_user", body)
        self.assertNotIn("PlainPassw0rd!", body)
        self.assertNotIn(TOTP_SECRET, body)
        self.assertNotIn("secretproxypw", body)
        self.assertIn('autocomplete="new-password"', body)

    def test_card_shows_last_login_state_and_escapes_error(self):
        self._post({"username": "u", "password": "p", "is_enabled": "on"})
        account = ProviderConnection.objects.get(provider_key="sky")
        account.last_login_ok = False
        account.last_login_at = timezone.now()
        account.last_error = "<script>alert(1)</script>"
        account.save()
        body = self.client.get(self.index_url).content.decode()
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;", body)

    def test_card_only_on_sky_tab(self):
        other = self.client.get(reverse("phone_refresh:providers_index") + "?tab=layan")
        self.assertEqual(other.status_code, 200)
        self.assertNotIn(self.save_url, other.content.decode())
        self.assertIn(self.save_url, self.client.get(self.index_url).content.decode())

    def test_invalid_post_does_not_create_account(self):
        response = self._post({"username": "u", "password": ""})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ProviderConnection.objects.exists())

    def test_save_rejects_unknown_provider(self):
        token = str(self.client.get(self.index_url).context["csrf_token"])
        url = reverse("phone_refresh:provider_account_save", args=["nope"])
        response = self.client.post(url, {"username": "u", "password": "p", "csrfmiddlewaretoken": token})
        self.assertEqual(response.status_code, 404)

    def test_save_requires_management(self):
        emp = Client()  # CSRF not enforced: isolate the permission check
        emp.force_login(self.employee)
        response = emp.post(self.save_url, {"username": "u", "password": "p"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ProviderConnection.objects.exists())

    def test_save_requires_csrf(self):
        response = self.client.post(self.save_url, {"username": "u", "password": "p"})
        self.assertEqual(response.status_code, 403)

    def test_save_is_post_only(self):
        self.assertEqual(self.client.get(self.save_url).status_code, 405)
