"""Canonicalization helpers for ASGI client addresses."""

import ipaddress


def normalize_client_ip(value: str) -> str | None:
    """Normalize an IP address, accepting common forwarded host/port forms."""
    candidate = value.strip().strip('"')
    if candidate.startswith("["):
        closing_bracket = candidate.find("]")
        if closing_bracket < 0:
            return None
        suffix = candidate[closing_bracket + 1 :]
        if suffix and not (suffix.startswith(":") and suffix[1:].isdigit()):
            return None
        candidate = candidate[1:closing_bracket]
    else:
        host, separator, port = candidate.rpartition(":")
        if separator and "." in host and port.isdigit():
            candidate = host

    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return str(address)
