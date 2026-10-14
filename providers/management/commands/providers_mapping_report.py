"""Read-only pre-deploy check of the company → provider mapping."""
from __future__ import annotations

from django.core.management.base import BaseCommand

from companies.models import Company
from providers.name_rules import provider_for_name


class Command(BaseCommand):
    help = (
        "Print companies with an empty phone_refresh_provider and companies whose "
        "name suggests a different provider than the field. Changes nothing."
    )

    def handle(self, *args, **options):
        empty = []
        mismatched = []
        for company in Company.objects.order_by("name", "pk"):
            field = (company.phone_refresh_provider or "").strip()
            suggested = provider_for_name(company.name)
            if not field:
                empty.append((company, suggested))
            elif suggested and suggested != field:
                mismatched.append((company, field, suggested))

        self.stdout.write(f"Companies with empty provider ({len(empty)}):")
        for company, suggested in empty:
            hint = f" (name suggests: {suggested})" if suggested else ""
            self.stdout.write(f"  #{company.pk} {company.name}{hint}")
        self.stdout.write(f"Companies whose name suggests another provider ({len(mismatched)}):")
        for company, field, suggested in mismatched:
            self.stdout.write(
                f"  #{company.pk} {company.name}: field={field}, name suggests {suggested}"
            )
