"""Delete old SMS inbound/outbound rows per the retention setting.

Schedule via cron, e.g.:
    python manage.py sms_purge_logs
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from sms_gateway.models import InboundSms, OutboundSms, SmsGatewaySettings


class Command(BaseCommand):
    help = "Purge inbound/outbound SMS rows older than the configured retention window."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Override retention days (defaults to SmsGatewaySettings.log_retention_days).",
        )

    def handle(self, *args, **options):
        settings_obj = SmsGatewaySettings.load()
        days = options["days"] if options["days"] is not None else settings_obj.log_retention_days
        if not days:
            self.stdout.write("Retention disabled (0 days); nothing purged.")
            return
        cutoff = timezone.now() - timedelta(days=days)
        out_deleted, _ = OutboundSms.objects.filter(created_at__lt=cutoff).delete()
        in_deleted, _ = InboundSms.objects.filter(received_at__lt=cutoff).delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Purged {in_deleted} inbound and {out_deleted} outbound rows older than {days} days."
            )
        )
