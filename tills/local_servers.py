import ipaddress
import logging
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)
PRIVATE_IPV4_NETWORKS = tuple(map(ipaddress.ip_network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")))


def normalize_local_server_urls(values):
    if isinstance(values, str):
        lines = values.splitlines()
    elif isinstance(values, list):
        lines = values
    else:
        raise ValidationError("Enter one local server URL per line.")

    urls = []
    for line_number, value in enumerate(lines, start=1):
        if not isinstance(value, str):
            raise ValidationError(f"Line {line_number}: enter a URL.")
        value = value.strip()
        if not value:
            continue
        try:
            parts = urlsplit(value)
            address = ipaddress.ip_address(parts.hostname or "")
            port = parts.port
        except ValueError as exc:
            raise ValidationError(
                f"Line {line_number}: use a private IPv4 address with an optional port, such as "
                "http://192.168.1.10:8000."
            ) from exc
        if (not isinstance(address, ipaddress.IPv4Address)
                or not any(address in network for network in PRIVATE_IPV4_NETWORKS)
                or port is not None and not 1 <= port <= 65535
                or parts.scheme not in {"http", "https"} or parts.username or parts.password
                or parts.path not in {"", "/"} or parts.query or parts.fragment):
            raise ValidationError(
                f"Line {line_number}: use only a private IPv4 server URL, with no path, login, query or fragment."
            )
        normalized = f"{parts.scheme}://{address.compressed}:{port or (443 if parts.scheme == 'https' else 80)}"
        if normalized not in urls:
            urls.append(normalized)
    return urls


def local_server_hosts():
    from .models import Till

    hosts = []
    for urls in Till.objects.values_list("local_server_urls", flat=True):
        try:
            normalized = normalize_local_server_urls(urls)
        except ValidationError as exc:
            logger.warning("Skipping invalid local server URLs on a till: %s", exc)
            continue
        for url in normalized:
            host = urlsplit(url).hostname
            if host and host not in hosts:
                hosts.append(host)
    return hosts
