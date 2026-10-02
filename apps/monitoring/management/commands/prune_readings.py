"""Bound the size of the readings time series.

Readings are appended roughly once per second while a simulation or the
monitor loop is running, and nothing else ever deletes them, so the table grows
without limit. Two independent bounds are applied:

* ``--days``  — drop everything older than N days (age bound)
* ``--keep``  — retain only the N most recent rows (size bound)

Either can be disabled with 0. Both are safe to run while the monitor is live:
only rows outside the retention window are deleted, and ``Reading.created_at``
is indexed so the age predicate is an index range scan rather than a table scan.

The two rules are applied as separate passes rather than as one combined
predicate: the age rule is a plain indexed ``filter().delete()``, and the size
rule materialises only the overflow primary keys (a sliced queryset cannot be
passed straight to ``delete()``).

Usage::

    python manage.py prune_readings                 # >30d old, max 10000 rows
    python manage.py prune_readings --days 7 --keep 5000
    python manage.py prune_readings --dry-run
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.monitoring.models import Reading


class Command(BaseCommand):
    help = "Delete old readings to keep the table bounded (by age and/or row count)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--days", type=float, default=30.0,
            help="Delete readings older than this many days (0 disables the age bound)",
        )
        parser.add_argument(
            "--keep", type=int, default=10_000,
            help="Keep at most this many most-recent readings (0 disables the count bound)",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would be deleted without deleting anything",
        )

    def handle(self, *args, **options):
        days: float = options["days"]
        keep: int = options["keep"]
        dry_run: bool = bool(options["dry_run"])

        if days <= 0 and keep <= 0:
            self.stdout.write(self.style.WARNING("Nothing to do: both --days and --keep are 0."))
            return

        total_before = Reading.objects.count()
        removed = 0

        # ------------------------------------------------------ age bound
        if days > 0:
            cutoff = timezone.now() - timedelta(days=days)
            stale = Reading.objects.filter(created_at__lt=cutoff)
            stale_count = stale.count()
            if stale_count:
                self.stdout.write(
                    f"Older than {days:g} day(s) (before {cutoff.isoformat()}): {stale_count}"
                )
                removed += stale_count
                if not dry_run:
                    stale.delete()
            else:
                self.stdout.write(f"Age bound: nothing older than {days:g} day(s).")

        # ------------------------------------------------------ size bound
        if keep > 0:
            remaining = Reading.objects.count() if not dry_run else total_before - removed
            if remaining <= keep:
                self.stdout.write(f"Count bound: {remaining} <= {keep}, nothing to trim.")
            else:
                # Order newest-first and take everything past `keep`. Slicing a
                # values_list and materialising it is supported; calling
                # delete() on the slice directly is not.
                overflow = list(
                    Reading.objects.order_by("-created_at").values_list("id", flat=True)[keep:]
                )
                self.stdout.write(f"Count bound: keeping newest {keep}, pruning {len(overflow)}")
                removed += len(overflow)
                if not dry_run and overflow:
                    Reading.objects.filter(id__in=overflow).delete()

        if not removed:
            self.stdout.write(self.style.SUCCESS(f"Nothing to prune ({total_before} readings)."))
            return

        verb = "Would delete" if dry_run else "Deleted"
        remaining = Reading.objects.count() if not dry_run else total_before - removed
        self.stdout.write(
            self.style.SUCCESS(f"{verb} {removed} of {total_before}; {remaining} remain.")
        )
