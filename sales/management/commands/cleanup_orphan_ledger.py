"""Management command: prune orphan SALE-typed CompanyBalanceTransaction rows.

Usage::

    # Inspect what would be cleaned (no DB writes):
    python manage.py cleanup_orphan_ledger --dry-run

    # Apply (default; will refund company balances and delete orphans):
    python manage.py cleanup_orphan_ledger

    # Restrict to a single company:
    python manage.py cleanup_orphan_ledger --company 4

The cleanup is wrapped in a single ``transaction.atomic`` block, so a
failure midway leaves the database untouched.
"""

from companies.models import Company
from django.core.management.base import BaseCommand, CommandError

from sales.services import (
    cleanup_orphan_sale_balance_transactions,
    find_orphan_sale_balance_transactions,
)


class Command(BaseCommand):
    help = "Remove CompanyBalanceTransaction rows whose linked Sale no longer exists."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the would-be changes without writing to the database.",
        )
        parser.add_argument(
            "--company",
            type=int,
            default=None,
            help="Restrict the cleanup to a single company by primary key.",
        )

    def handle(self, *args, **opts):
        company = None
        if opts["company"] is not None:
            try:
                company = Company.objects.get(pk=opts["company"])
            except Company.DoesNotExist as exc:
                raise CommandError(f"Company #{opts['company']} not found.") from exc

        if opts["dry_run"]:
            orphans = list(find_orphan_sale_balance_transactions(company=company))
            if not orphans:
                self.stdout.write(self.style.SUCCESS("No orphan ledger rows found."))
                return
            self.stdout.write(self.style.WARNING(f"Would delete {len(orphans)} row(s):"))
            for txn in orphans:
                self.stdout.write(
                    f"  #{txn.pk}  company={txn.company_id}  type={txn.entry_type}  "
                    f"ref=sale#{txn.reference_id}  amount={txn.amount}"
                )
            return

        summary = cleanup_orphan_sale_balance_transactions(company=company)
        if summary["orphan_count"] == 0:
            self.stdout.write(self.style.SUCCESS("No orphan ledger rows found."))
            return
        self.stdout.write(
            self.style.SUCCESS(
                "Removed {orphan_count} orphan row(s) across {companies_affected} "
                "company/companies. Net refund posted: {net_refund_total}.".format(
                    **summary
                )
            )
        )
