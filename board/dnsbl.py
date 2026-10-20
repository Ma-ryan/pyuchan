import concurrent.futures
import ipaddress
import socket
from django.core.cache import cache
from .conf import DNSBL_TIMEOUT_SECONDS, DNSBL_ZONES, ENABLE_DNSBL, ENABLE_TOR_BLOCKING, TOR_DNSEL_ZONE

# Constants for Tor caching and management commands
TOR_CACHE_KEY = "tor_exit_nodes_bulk_list"
TOR_CACHE_TTL = 3600 * 24
TOR_BULK_URL = "https://check.torproject.org/torbulkexitlist"

_dns_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)


class ProxyBlockedException(Exception):
    pass


def _resolve_host_with_timeout(host: str, timeout: float) -> list[str]:
    """Resolves DNS queries using an executor without mutating global socket timeout."""
    def _lookup():
        try:
            return socket.gethostbyname_ex(host)[2]
        except Exception:
            return []

    future = _dns_executor.submit(_lookup)
    try:
        return future.result(timeout=timeout)
    except (concurrent.futures.TimeoutError, Exception):
        return []


def is_tor_exit_node(ip_address: str) -> bool:
    if not ENABLE_TOR_BLOCKING:
        return False

    try:
        ip_obj = ipaddress.ip_address(ip_address)
        if ip_obj.version != 4 or ip_obj.is_private or ip_obj.is_loopback:
            return False
    except ValueError:
        return False

    # Check fast in-memory / redis cache first if populated
    cached_exits = cache.get(TOR_CACHE_KEY)
    if cached_exits and ip_address in cached_exits:
        return True

    rev_octets = ".".join(reversed(ip_address.split(".")))
    query = f"{rev_octets}.{TOR_DNSEL_ZONE}"

    addrs = _resolve_host_with_timeout(query, DNSBL_TIMEOUT_SECONDS)
    return "127.0.0.2" in addrs


def is_ip_listed_in_dnsbl(ip_address: str) -> tuple[bool, str]:
    if not ENABLE_DNSBL:
        return False, ""

    try:
        ip_obj = ipaddress.ip_address(ip_address)
        if ip_obj.version != 4 or ip_obj.is_private or ip_obj.is_loopback:
            return False, ""
    except ValueError:
        return False, ""

    rev_octets = ".".join(reversed(ip_address.split(".")))

    for zone in DNSBL_ZONES:
        query = f"{rev_octets}.{zone}"
        addrs = _resolve_host_with_timeout(query, DNSBL_TIMEOUT_SECONDS)
        for addr in addrs:
            if addr.startswith("127.0.0."):
                return True, zone

    return False, ""


def check_ip_reputation(ip_address: str):
    if not ip_address:
        return

    try:
        ip_obj = ipaddress.ip_address(ip_address)
        if ip_obj.is_private or ip_obj.is_loopback:
            return
    except ValueError:
        return

    if is_tor_exit_node(ip_address):
        raise ProxyBlockedException("Posting from Tor exit nodes is not permitted.")

    is_listed, zone = is_ip_listed_in_dnsbl(ip_address)
    if is_listed:
        raise ProxyBlockedException(f"Posting rejected: IP listed on public proxy blacklist ({zone}).")
