from django.core.management.base import BaseCommand
from board.conf import ARCHIVE_RETENTION_DAYS, ENABLE_ARCHIVE
from board.services import purge_expired_archived_threads


class Command(BaseCommand):
    help = f"Permanently purges archived threads older than {ARCHIVE_RETENTION_DAYS} days and deletes media."

    def handle(self, *args, **options):
        if not ENABLE_ARCHIVE:
            self.stdout.write(self.style.WARNING("Archiving is disabled (ENABLE_ARCHIVE = False). Skipping purge."))
            return

        self.stdout.write(f"Scanning for archived threads older than {ARCHIVE_RETENTION_DAYS} days...")
        purge_expired_archived_threads()
        self.stdout.write(self.style.SUCCESS("Archive purge routine completed."))
