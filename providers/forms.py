from __future__ import annotations

import json

from django import forms

from providers.accounts import load_extra, normalize_totp_secret
from providers.crypto import encrypt
from providers.models import ProviderAccount

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
    is_enabled = forms.BooleanField(
        label="مفعّل",
        required=False,
        widget=forms.CheckboxInput(
            attrs={"class": "form-check-input", "role": "switch"}
        ),
    )

    def __init__(self, *args, provider_key: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.provider_key = provider_key
        self.account = ProviderAccount.objects.filter(provider_key=provider_key).first()
        if self.account is not None:
            self.fields["username"].initial = self.account.username
            self.fields["is_enabled"].initial = self.account.is_enabled
            has_proxy = bool(load_extra(self.account).get("proxy_url"))
            for name, saved in (
                ("password", bool(self.account.secret_encrypted)),
                ("totp_secret", bool(self.account.totp_secret_encrypted)),
                ("proxy_url", has_proxy),
            ):
                if saved:
                    self.fields[name].widget.attrs["placeholder"] = SAVED_PLACEHOLDER
        else:
            self.fields["is_enabled"].initial = True

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
        has_saved_password = bool(self.account and self.account.secret_encrypted)
        if not cleaned.get("password") and not has_saved_password:
            self.add_error("password", "كلمة المرور مطلوبة.")
        return cleaned

    def save(self) -> ProviderAccount:
        data = self.cleaned_data
        account = self.account or ProviderAccount(provider_key=self.provider_key)
        account.username = data["username"].strip()
        account.is_enabled = data["is_enabled"]
        if data["password"]:
            account.secret_encrypted = encrypt(data["password"])
        if data["totp_secret"]:
            account.totp_secret_encrypted = encrypt(data["totp_secret"])

        extra = load_extra(account) if account.pk else {}
        if data["clear_proxy"]:
            extra.pop("proxy_url", None)
        elif data["proxy_url"]:
            extra["proxy_url"] = data["proxy_url"]
        account.extra_encrypted = encrypt(json.dumps(extra)) if extra else ""

        account.save()
        self.account = account
        return account
