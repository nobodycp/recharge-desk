"""
One-shot backfill: re-encode every legacy uploaded icon as a capped WebP.

New uploads are normalized inside each model's ``save()`` (see
:func:`core.image_utils.optimize_image`); this command exists for files that
were stored before that hook landed. Safe to re-run — already-optimized files
are skipped automatically.

Usage::

    python manage.py optimize_icons              # rewrites everything in place
    python manage.py optimize_icons --dry-run    # report only, do not touch files
"""

from __future__ import annotations

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import models as dj_models

from core.image_utils import optimize_field_file


class Command(BaseCommand):
    help = "Re-encode existing ImageField uploads as size-capped WebP files."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List candidates and projected savings without writing.",
        )

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))
        if dry_run:
            self.stdout.write(self.style.NOTICE("Dry run — no files will be modified."))

        total_before = 0
        total_after = 0
        rewritten = 0
        skipped = 0
        scanned = 0

        for model in apps.get_models():
            image_fields = [
                f for f in model._meta.get_fields()
                if isinstance(f, dj_models.ImageField)
            ]
            if not image_fields:
                continue

            qs = model._default_manager.all()
            for instance in qs.iterator():
                for field in image_fields:
                    field_file = getattr(instance, field.attname, None)
                    if not field_file or not getattr(field_file, "name", ""):
                        continue

                    scanned += 1
                    try:
                        size_before = field_file.size
                    except (OSError, NotImplementedError, FileNotFoundError):
                        size_before = 0

                    label = (
                        f"{model._meta.label}#{instance.pk}.{field.attname} "
                        f"({field_file.name}, {_human(size_before)})"
                    )

                    if dry_run:
                        self.stdout.write(f"  candidate: {label}")
                        total_before += size_before
                        continue

                    changed = optimize_field_file(field_file)
                    if not changed:
                        skipped += 1
                        self.stdout.write(f"  skipped:  {label}")
                        continue

                    instance.refresh_from_db(fields=[field.attname])
                    new_file = getattr(instance, field.attname)
                    try:
                        size_after = new_file.size
                    except (OSError, NotImplementedError, FileNotFoundError):
                        size_after = 0

                    rewritten += 1
                    total_before += size_before
                    total_after += size_after
                    self.stdout.write(self.style.SUCCESS(
                        f"  rewrote:  {label} -> {new_file.name} "
                        f"({_human(size_after)}, {_pct(size_before, size_after)})"
                    ))

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Scanned {scanned} icon(s); "
            f"rewrote {rewritten}, skipped {skipped}."
        ))
        if not dry_run and rewritten:
            self.stdout.write(self.style.SUCCESS(
                f"Total {_human(total_before)} -> {_human(total_after)} "
                f"({_pct(total_before, total_after)})."
            ))


def _human(num_bytes: int) -> str:
    if not num_bytes:
        return "0 B"
    units = ["B", "KB", "MB", "GB"]
    value = float(num_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} {units[-1]}"


def _pct(before: int, after: int) -> str:
    if not before:
        return "n/a"
    saved = (1 - (after / before)) * 100
    return f"-{saved:.0f}%"
