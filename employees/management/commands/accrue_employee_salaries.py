"""
Accrue monthly salaries for all active employees.

Run on the 1st of each month via cron, e.g.::

    python manage.py accrue_employee_salaries

Optional ``--month=YYYY-MM`` targets a specific month (defaults to current
calendar month in the active timezone). Idempotent: existing accrual rows
for that month are skipped.
"""

from datetime import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from employees.services import accrue_salaries_for_month, default_accrual_month


class Command(BaseCommand):
    help = "Accrue monthly salary ledger entries for active employees."

    def add_arguments(self, parser):
        parser.add_argument(
            "--month",
            type=str,
            default="",
            help="Accrual month as YYYY-MM (default: current month).",
        )

    def handle(self, *args, **options):
        month_opt = (options.get("month") or "").strip()
        if month_opt:
            dt = datetime.strptime(month_opt, "%Y-%m")
            salary_month = default_accrual_month().replace(year=dt.year, month=dt.month, day=1)
        else:
            salary_month = default_accrual_month()

        count = accrue_salaries_for_month(salary_month=salary_month, user=None)
        self.stdout.write(
            self.style.SUCCESS(
                f"Accrued salaries for {salary_month:%Y-%m}: {count} new ledger row(s)."
            )
        )
