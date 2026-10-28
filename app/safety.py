import ipaddress
import re
from urllib.parse import urlsplit

_NUMERIC_HOST_LABEL = re.compile(r"(?:[0-9]+|0x[0-9a-f]+)", re.IGNORECASE)
_NON_PUBLIC_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home")


def validate_redirect_destination(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only absolute HTTP and HTTPS destinations are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Destination URLs must not contain credentials")

    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(_NON_PUBLIC_SUFFIXES) or "." not in host:
        raise ValueError("Private and local destinations are not allowed")

    labels = host.split(".")
    if all(_NUMERIC_HOST_LABEL.fullmatch(label) for label in labels):
        raise ValueError("Non-public IP address destinations are not allowed")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise ValueError("Private and reserved IP address destinations are not allowed")
