from __future__ import annotations

import json
import logging

from django import forms

from providers.accounts import load_extra, normalize_totp_secret
from providers.crypto import decrypt_or_empty, encrypt
from providers.models import ProviderConnection
from providers.sanitize import scrub
from providers.sessions import reset_session

log = logging.getLogger(__name__)

SAVED_PLACEHOLDER = "•••• (محفوظ)"


def _secret_widget(placeholder: str = "") -> forms.PasswordInput:
    return forms.PasswordInput(
        render_value=False,
        attrs={
            "class": "form-control",
            "dir": "ltr",
            "autocomplete": "new-password",
            "placeholder": placeholder,
        },
    )


class ProviderAccountForm(forms.Form):
    """Write-only account form: stored secrets are never rendered back."""

    username = forms.CharField(
        label="اسم المستخدم",
        max_length=150,
        widget=forms.TextInput(
            attrs={"class": "form-control", "dir": "ltr", "autocomplete": "off"}
        ),
    )
    password = forms.CharField(
        label="كلمة المرور",
        required=False,
        widget=_secret_widget(),
    )
    totp_secret = forms.CharField(
        label="مفتاح TOTP (أو رابط otpauth://)",
        required=False,
        widget=_secret_widget(),
    )
    proxy_url = forms.CharField(
        label="رابط البروكسي",
        required=False,
        widget=_secret_widget(),
    )
    clear_proxy = forms.BooleanField(
        label="إزالة البروكسي المحفوظ",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, provider_key: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.provider_key = provider_key
        if provider_key == "areen":
            self.fields.pop("totp_secret", None)
        self.account = ProviderConnection.objects.filter(provider_key=provider_key).first()
        if self.account is not None:
            self.fields["username"].initial = self.account.username
            has_proxy = bool(load_extra(self.account).get("proxy_url"))
            for name, saved in (
                ("password", bool(decrypt_or_empty(self.account.secret_encrypted))),
                ("totp_secret", bool(decrypt_or_empty(self.account.totp_secret_encrypted))),
                ("proxy_url", has_proxy),
            ):
                if saved and name in self.fields:
                    self.fields[name].widget.attrs["placeholder"] = SAVED_PLACEHOLDER

    def clean_totp_secret(self) -> str:
        raw = self.cleaned_data.get("totp_secret", "")
        try:
            return normalize_totp_secret(raw)
        except ValueError:
            raise forms.ValidationError("مفتاح TOTP غير صالح.")

    def clean_proxy_url(self) -> str:
        value = (self.cleaned_data.get("proxy_url") or "").strip()
        if value and "://" not in value:
            raise forms.ValidationError("رابط البروكسي غير صالح.")
        return value

    def clean(self):
        cleaned = super().clean()
        # An unreadable stored password counts as "not saved" so it can be overwritten.
        has_saved_password = bool(
            self.account and decrypt_or_empty(self.account.secret_encrypted)
        )
        if not cleaned.get("password") and not has_saved_password:
            self.add_error("password", "كلمة المرور مطلوبة.")
        return cleaned

    def save(self) -> ProviderConnection:
        data = self.cleaned_data
        account = self.account or ProviderConnection(provider_key=self.provider_key)
        previous_username = account.username
        account.username = data["username"].strip()
        if data["password"]:
            account.secret_encrypted = encrypt(data["password"])
        totp_secret = data.get("totp_secret") or ""
        if totp_secret:
            account.totp_secret_encrypted = encrypt(totp_secret)
        elif "totp_secret" in self.fields and not decrypt_or_empty(account.totp_secret_encrypted):
            account.totp_secret_encrypted = ""  # drop an unreadable blob

        extra = load_extra(account) if account.pk else {}
        if self.provider_key == "areen" and (
            data["password"] or previous_username != account.username
        ):
            extra.pop("areen_device", None)
        if data["clear_proxy"]:
            extra.pop("proxy_url", None)
        elif data["proxy_url"]:
            extra["proxy_url"] = data["proxy_url"]
        account.extra_encrypted = encrypt(json.dumps(extra)) if extra else ""

        account.save()
        # New credentials: drop the stored session and any login cool-down so
        # the next authenticated call logs in with them right away. The save
        # above already succeeded, so a Redis outage must not turn it into a 500.
        try:
            reset_session(self.provider_key)
        except Exception as exc:
            log.warning(
                "provider %s session reset failed after saving credentials: %s",
                self.provider_key,
                scrub(str(exc)) or exc.__class__.__name__,
            )
        self.account = account
        return account
