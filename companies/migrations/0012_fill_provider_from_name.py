"""Fill ``Company.phone_refresh_provider`` from the legacy company-name rules.

Routing now comes only from the explicit field. The name rules below are a
frozen copy of what the code matched before this migration (kept inline so the
migration never depends on live app code):

* phone refresh (``COMPANY_TO_PROVIDER``): lower-cased, stripped name;
  first of ``sky``, ``aloha``, ``layan``, ``areen`` that is a substring wins.
* Sky reconcile (``company_supports_sky_reconcile``): ``sky`` in the lower-cased
  name, or ``سكاي`` in the name.
* Layan reconcile (``company_supports_layan_reconcile``): ``layan`` in the
  lower-cased name.

Only companies whose field is empty are touched; existing values are never
overwritten. The reverse is a no-op.
"""

from django.db import migrations

# Same order as the old COMPANY_TO_PROVIDER dict (first match wins).
_NAME_RULES = (
    ("sky", "sky"),
    ("aloha", "aloha"),
    ("layan", "layan"),
    ("areen", "areen"),
)
_SKY_ARABIC = "سكاي"


def provider_for_name(name):
    lowered = (name or "").strip().lower()
    for needle, provider_key in _NAME_RULES:
        if needle in lowered:
            return provider_key
    if _SKY_ARABIC in (name or ""):
        return "sky"
    return ""


def fill_provider_from_name(apps, schema_editor):
    Company = apps.get_model("companies", "Company")
    for company in Company.objects.all():
        if (company.phone_refresh_provider or "").strip():
            continue
        provider_key = provider_for_name(company.name)
        if provider_key:
            Company.objects.filter(pk=company.pk).update(
                phone_refresh_provider=provider_key
            )


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0011_allow_partial_days"),
    ]

    operations = [
        migrations.RunPython(fill_provider_from_name, migrations.RunPython.noop),
    ]
