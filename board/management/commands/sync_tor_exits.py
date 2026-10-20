import urllib.request
from django.core.cache import cache
from django.core.management.base import BaseCommand
from board.dnsbl import TOR_BULK_URL, TOR_CACHE_KEY, TOR_CACHE_TTL


class Command(BaseCommand):
    help = "Fetches the latest official Tor exit node list and pre-warms the cache."

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force a cache update even if an existing cache entry exists.',
        )

    def handle(self, *args, **options):
        self.stdout.write("Fetching Tor exit list from Tor Project directory...")

        if not options['force'] and cache.get(TOR_CACHE_KEY) is not None:
            count = len(cache.get(TOR_CACHE_KEY))
            self.stdout.write(self.style.SUCCESS(
                f"Tor exit cache is already fresh ({count} nodes). Use --force to override."
            ))
            return

        try:
            req = urllib.request.Request(
                TOR_BULK_URL,
                headers={'User-Agent': 'Mozilla/5.0 (compatible; pyuchan-security/1.0)'}
            )
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                text = resp.read().decode('utf-8', errors='ignore')
                exits = {
                    line.strip()
                    for line in text.splitlines()
                    if line.strip() and not line.startswith('#')
                }

            if not exits:
                self.stderr.write(self.style.ERROR("Received empty exit list from Tor Project."))
                return

            # Store in Django's active cache backend
            cache.set(TOR_CACHE_KEY, exits, timeout=TOR_CACHE_TTL)
            self.stdout.write(self.style.SUCCESS(
                f"Successfully cached {len(exits)} active Tor exit nodes (TTL: {TOR_CACHE_TTL}s)."
            ))

        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Failed to fetch Tor exit list: {e}"))
