import re
import hashlib

from django import forms
from django.core.exceptions import ValidationError

from phone_refresh.models import (
    ApiSettings,
    ApiToken,
    CustomerMessage,
    PhoneProvider,
    ProviderConfig,
    ProviderResponseRule,
    RefreshStatus,
    SiteSettings,
    SystemSettings,
)


from phone_refresh.validation import PHONE_HTML_PATTERN, PHONE_RE, PHONE_VALIDATION_ERROR_AR
STATUS_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
# Host label per RFC 1035 (a-z, 0-9, hyphens, dots between labels). We
# allow dots so a full subdomain like ``rn.prosim.ps`` validates as one
# value; we forbid scheme, path, port and userinfo separators.
SUBDOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")


class ProviderResponseRuleForm(forms.ModelForm):
    class Meta:
        model = ProviderResponseRule
        fields = [
            "match_type",
            "pattern",
            "expected_value",
            "target_status",
            "order",
            "is_active",
            "note",
        ]
        widgets = {
            "match_type": forms.Select(attrs={"class": "form-select"}),
            "pattern": forms.TextInput(attrs={"class": "form-control", "dir": "auto"}),
            "expected_value": forms.TextInput(attrs={"class": "form-control", "dir": "auto"}),
            "target_status": forms.Select(attrs={"class": "form-select"}),
            "order": forms.NumberInput(attrs={"class": "form-control", "min": "0"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "note": forms.TextInput(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # ModelChoiceField gets its queryset from the FK automatically,
        # but we want a stable Arabic-label order with no "----" blank
        # entry (one of the statuses is always required).
        self.fields["target_status"].queryset = RefreshStatus.objects.all()
        self.fields["target_status"].empty_label = None
        self.fields["target_status"].label_from_instance = lambda obj: obj.label


class CustomerMessageForm(forms.ModelForm):
    """ModelForm for one ``CustomerMessage`` row.

    On create the ``status`` choices are restricted to the
    ``RefreshStatus`` rows that don't yet have a message; on edit the
    field is rendered disabled so admins can't move a row to a
    different status (the FK is one-to-one, so reassigning it would
    require deleting the existing row first anyway).
    """

    class Meta:
        model = CustomerMessage
        fields = ["status", "title", "body"]
        widgets = {
            "status": forms.Select(attrs={"class": "form-select"}),
            "title": forms.TextInput(attrs={"class": "form-control", "dir": "auto"}),
            "body": forms.Textarea(attrs={"class": "form-control", "rows": 4, "dir": "auto"}),
        }

    def __init__(self, *args, available_statuses=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["status"].empty_label = None
        self.fields["status"].label_from_instance = lambda obj: obj.label

        is_edit = self.instance is not None and self.instance.pk is not None
        if is_edit:
            self.fields["status"].disabled = True
            self.fields["status"].queryset = RefreshStatus.objects.filter(
                pk=self.instance.status_id
            )
        else:
            if available_statuses is None:
                used_ids = CustomerMessage.objects.values_list("status_id", flat=True)
                available_statuses = RefreshStatus.objects.exclude(pk__in=used_ids)
            self.fields["status"].queryset = available_statuses


class RefreshStatusForm(forms.ModelForm):
    """Create / edit a ``RefreshStatus`` row from the messages tab.

    System statuses (``is_system=True``) freeze the ``code`` and
    ``is_system`` columns: only the Arabic ``label`` and ``sort_order``
    are editable so the rest of the codebase keeps resolving the
    canonical codes.
    """

    class Meta:
        model = RefreshStatus
        fields = ["code", "label", "sort_order"]
        widgets = {
            "code": forms.TextInput(
                attrs={"class": "form-control", "dir": "ltr", "autocomplete": "off"},
            ),
            "label": forms.TextInput(
                attrs={"class": "form-control", "dir": "auto", "autocomplete": "off"},
            ),
            "sort_order": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "10"}
            ),
        }
        help_texts = {
            "code": "English slug (lowercase letters, digits, dashes and underscores). "
            "Returned verbatim in the public API.",
            "label": "الاسم العربي المعروض في الواجهة.",
            "sort_order": "الترتيب في القوائم — الأرقام الأصغر تظهر أولاً.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # ``code`` is immutable on system statuses; allow it only when
        # creating a new row or editing a user-created row.
        if self.instance and self.instance.pk and self.instance.is_system:
            self.fields["code"].disabled = True
            self.fields["code"].help_text = (
                "حالة نظام — لا يمكن تغيير الرمز لأنّ الكود يعتمد عليه."
            )

    def clean_code(self) -> str:
        value = (self.cleaned_data.get("code") or "").strip().lower()
        if self.instance and self.instance.pk and self.instance.is_system:
            # Field is disabled so cleaned_data is the original instance
            # value already; just return it to keep the row intact.
            return self.instance.code
        if not value:
            raise ValidationError("الرمز مطلوب.")
        if not STATUS_CODE_RE.match(value):
            raise ValidationError(
                "الرمز يجب أن يكون بأحرف لاتينية صغيرة وأرقام، ويمكن أن يحتوي '-' أو '_'."
            )
        qs = RefreshStatus.objects.filter(code=value)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError("هذا الرمز مستخدم مسبقاً.")
        return value


class SystemSettingsForm(forms.ModelForm):
    """Form for the General-tab toggles + cooldown + default provider."""

    class Meta:
        model = SystemSettings
        fields = [
            "service_enabled",
            "db_precheck_enabled",
            "show_last_refresh",
            "cooldown_seconds",
            "default_provider",
        ]
        widgets = {
            "service_enabled": forms.CheckboxInput(
                attrs={"class": "form-check-input", "role": "switch"}
            ),
            "db_precheck_enabled": forms.CheckboxInput(
                attrs={"class": "form-check-input", "role": "switch"}
            ),
            "show_last_refresh": forms.CheckboxInput(
                attrs={"class": "form-check-input", "role": "switch"}
            ),
            "cooldown_seconds": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "60"}
            ),
            "default_provider": forms.Select(attrs={"class": "form-select"}),
        }


class SiteSettingsForm(forms.ModelForm):
    """Form for the إدارة الموقع tab: subdomain + redirect + social links."""

    class Meta:
        model = SiteSettings
        fields = [
            "public_subdomain",
            "redirect_main_to_subdomain",
            "whatsapp_url",
            "facebook_url",
        ]
        widgets = {
            "public_subdomain": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "dir": "ltr",
                    "autocomplete": "off",
                    "placeholder": "rn.prosim.ps",
                }
            ),
            "redirect_main_to_subdomain": forms.CheckboxInput(
                attrs={"class": "form-check-input", "role": "switch"}
            ),
            "whatsapp_url": forms.URLInput(
                attrs={
                    "class": "form-control",
                    "dir": "ltr",
                    "autocomplete": "off",
                    "placeholder": "https://wa.me/...",
                }
            ),
            "facebook_url": forms.URLInput(
                attrs={
                    "class": "form-control",
                    "dir": "ltr",
                    "autocomplete": "off",
                    "placeholder": "https://facebook.com/...",
                }
            ),
        }

    def clean_public_subdomain(self) -> str:
        raw = (self.cleaned_data.get("public_subdomain") or "").strip().lower()
        if not raw:
            return ""
        # Forgive a pasted full URL: strip scheme + trailing slashes.
        for scheme in ("https://", "http://", "//"):
            if raw.startswith(scheme):
                raw = raw[len(scheme):]
                break
        raw = raw.strip("/")
        if "/" in raw:
            raise ValidationError("لا تضع مساراً — فقط اسم النطاق (مثال: rn.prosim.ps).")
        if ":" in raw:
            raise ValidationError("لا تضع منفذاً — فقط اسم النطاق بدون رقم البورت.")
        if "@" in raw or " " in raw:
            raise ValidationError("اسم النطاق غير صالح.")
        if not SUBDOMAIN_RE.match(raw):
            raise ValidationError("اسم النطاق غير صالح. استخدم أحرفاً لاتينية وأرقاماً ونقاطاً فقط.")
        return raw

    @staticmethod
    def _clean_social_url(raw: str, label: str) -> str:
        value = (raw or "").strip()
        if not value:
            return ""
        lower = value.lower()
        if not (lower.startswith("https://") or lower.startswith("http://")):
            raise ValidationError(
                f"رابط {label} يجب أن يبدأ بـ https:// أو http://."
            )
        return value

    def clean_whatsapp_url(self) -> str:
        return self._clean_social_url(self.cleaned_data.get("whatsapp_url"), "WhatsApp")

    def clean_facebook_url(self) -> str:
        return self._clean_social_url(self.cleaned_data.get("facebook_url"), "Facebook")


class PublicPageTokenAssignForm(forms.Form):
    """Assign an existing active ``ApiToken`` to the public refresh page."""

    public_page_token = forms.ModelChoiceField(
        label="التوكن",
        queryset=ApiToken.objects.none(),
        required=True,
        empty_label="— اختر توكناً —",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    public_page_token_raw = forms.CharField(
        label="القيمة الخام للتوكن",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "dir": "ltr",
                "autocomplete": "off",
                "placeholder": "الصق القيمة التي ظهرت لحظة إنشاء التوكن",
            }
        ),
        help_text=(
            "مطلوبة عند تعيين توكن جديد. إذا كان التوكن مُعيَّناً مسبقاً "
            "وتحتفظ بالقيمة في الإعدادات، يمكنك ترك الحقل فارغاً."
        ),
    )

    def __init__(self, *args, site_settings=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.site_settings = site_settings
        self.fields["public_page_token"].queryset = ApiToken.objects.filter(
            revoked_at__isnull=True,
        ).order_by("-created_at")

    def clean(self):
        cleaned = super().clean()
        token = cleaned.get("public_page_token")
        raw = (cleaned.get("public_page_token_raw") or "").strip()
        if token is None:
            return cleaned

        if not raw:
            if (
                self.site_settings
                and self.site_settings.public_page_token_id == token.pk
                and self.site_settings.public_page_token_raw
            ):
                cleaned["public_page_token_raw"] = self.site_settings.public_page_token_raw
                return cleaned
            raise ValidationError("يجب إدخال القيمة الخام للتوكن عند التعيين.")

        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        if token.token_hash != digest:
            raise ValidationError("القيمة الخام لا تطابق التوكن المختار.")
        cleaned["public_page_token_raw"] = raw
        return cleaned


class InternalTestForm(forms.Form):
    """Admin-only form: a single phone number for the internal test tool."""

    phone = forms.CharField(
        label="رقم الهاتف",
        max_length=10,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "dir": "ltr",
                "inputmode": "numeric",
                "pattern": PHONE_HTML_PATTERN,
                "maxlength": "10",
                "placeholder": "0555555555",
                "autocomplete": "off",
            }
        ),
    )

    def clean_phone(self) -> str:
        value = (self.cleaned_data.get("phone") or "").strip()
        if not PHONE_RE.match(value):
            raise forms.ValidationError(PHONE_VALIDATION_ERROR_AR)
        return value


class ApiSettingsForm(forms.ModelForm):
    """Form for the API-tab → اعدادات API panel."""

    class Meta:
        model = ApiSettings
        fields = [
            "require_token",
            "rate_limit_per_minute",
            "rate_limit_per_hour",
            "allow_anonymous_test_page",
            "allowed_origins",
        ]
        widgets = {
            "require_token": forms.CheckboxInput(
                attrs={"class": "form-check-input", "role": "switch"}
            ),
            "rate_limit_per_minute": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "1"}
            ),
            "rate_limit_per_hour": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "1"}
            ),
            "allow_anonymous_test_page": forms.CheckboxInput(
                attrs={"class": "form-check-input", "role": "switch"}
            ),
            "allowed_origins": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 4,
                    "dir": "ltr",
                    "placeholder": "https://example.com\nhttps://app.example.com",
                }
            ),
        }


class ApiTokenForm(forms.ModelForm):
    """Create-only form for ``ApiToken``: just collects a friendly name.

    The raw token, hash and prefix are generated server-side in the view
    so the form never has to touch them.
    """

    class Meta:
        model = ApiToken
        fields = ["name"]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "dir": "auto",
                    "autocomplete": "off",
                    "placeholder": "مثال: تطبيق العملاء",
                }
            ),
        }

    def clean_name(self) -> str:
        value = (self.cleaned_data.get("name") or "").strip()
        if not value:
            raise forms.ValidationError("الاسم مطلوب.")
        return value


class ProviderConfigForm(forms.ModelForm):
    """Single-row form used by the providers General tab for the on/off toggle."""

    class Meta:
        model = ProviderConfig
        fields = ["is_enabled"]
        widgets = {
            "is_enabled": forms.CheckboxInput(
                attrs={"class": "form-check-input", "role": "switch"}
            ),
        }


class ProviderTestForm(forms.Form):
    """Admin form: phone + explicit provider key for the per-provider test."""

    phone = forms.CharField(
        label="رقم الهاتف",
        max_length=10,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "dir": "ltr",
                "inputmode": "numeric",
                "pattern": PHONE_HTML_PATTERN,
                "maxlength": "10",
                "placeholder": "0555555555",
                "autocomplete": "off",
            }
        ),
    )
    provider = forms.ChoiceField(
        label="المزوّد",
        choices=PhoneProvider.choices,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def clean_phone(self) -> str:
        value = (self.cleaned_data.get("phone") or "").strip()
        if not PHONE_RE.match(value):
            raise forms.ValidationError(PHONE_VALIDATION_ERROR_AR)
        return value

    def clean_provider(self) -> str:
        value = (self.cleaned_data.get("provider") or "").strip()
        if value not in {p.value for p in PhoneProvider}:
            raise forms.ValidationError("مزوّد غير معروف.")
        return value
