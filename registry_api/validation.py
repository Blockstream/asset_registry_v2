import re
from urllib.parse import urlsplit, urlunsplit

from pydantic_core import PydanticCustomError

from registry_api.errors import ErrorCode, RegistryError

ASSET_ID_RE = re.compile(r"^[0-9a-f]{64}$")
PUBKEY_RE = re.compile(r"^(02|03)[0-9a-f]{64}$")
TICKER_RE = re.compile(r"^[A-Za-z0-9.\-]{1,24}$")
LEGACY_TICKER_RE = re.compile(r"^[A-Za-z0-9.\-]{3,24}$")
ASCII_NAME_RE = re.compile(r"^[\x00-\x7f]{1,255}$")
DOMAIN_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
DOMAIN_PATTERN = (
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?\.?$"
)
CASE_INSENSITIVE_DOMAIN_PATTERN = (
    r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.?$"
)

TRADING_VENUES = {"sideswap", "bitfinex"}
CATEGORY_TAGS = {"stablecoin", "bond", "fixed-income", "tokenized"}
ASSET_TYPES = {"AMP_asset", "stablecoin", "security_token", "other"}
DOMAIN_VERIFICATION_METHODS = {"http", "dns"}
INITIAL_ISSUER_PUBKEY_SOURCES = {
    "contract",
    "registry_registration",
    "migrated_legacy_record",
}
ADMIN_PERMISSIONS = {
    "root",
    "annotate_assets",
    "delist_assets",
    "review_icons",
    "manage_icons",
    "manage_admins",
    "migrate_assets",
}


def normalize_asset_id(asset_id: str) -> str:
    normalized = asset_id.lower()
    if not ASSET_ID_RE.fullmatch(normalized):
        raise ValueError("asset_id must be 64 lowercase hex characters")
    return normalized


def normalize_pubkey(pubkey: str) -> str:
    normalized = pubkey.lower()
    if not PUBKEY_RE.fullmatch(normalized):
        raise ValueError("pubkey must be a compressed secp256k1 public key")
    return normalized


def validate_ticker(ticker: str, *, legacy: bool = False) -> str:
    regex = LEGACY_TICKER_RE if legacy else TICKER_RE
    if not regex.fullmatch(ticker):
        raise ValueError("ticker contains unsupported characters or length")
    return ticker


def validate_name(name: str) -> str:
    reject_nul_characters(name, "name")
    if not ASCII_NAME_RE.fullmatch(name):
        raise ValueError("name must be 1-255 ASCII characters")
    return name


def reject_nul_characters(value: object, field: str) -> None:
    if isinstance(value, str):
        if "\x00" in value:
            raise ValueError(f"{field} must not contain NUL characters")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            reject_nul_characters(key, field)
            reject_nul_characters(item, field)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            reject_nul_characters(item, field)


def validate_precision(precision: int, *, maximum: int = 18) -> int:
    if precision < 0 or precision > maximum:
        raise ValueError(f"precision must be between 0 and {maximum}")
    return precision


def normalize_domain(domain: str) -> str:
    if domain != domain.lower():
        raise ValueError("domain must be lower-case ASCII/Punycode")
    if domain.startswith("."):
        raise ValueError("domain cannot start with a dot")
    if len(domain) > 255:
        raise ValueError("domain must be no longer than 255 characters")

    labels = domain[:-1].split(".") if domain.endswith(".") else domain.split(".")
    if len(labels) <= 1:
        raise ValueError("domain must have at least two labels")
    if len(labels) > 127:
        raise ValueError("domain must not have more than 127 labels")
    if not labels[-1].startswith(tuple("abcdefghijklmnopqrstuvwxyz")):
        raise ValueError("domain tld must start with a letter")

    try:
        domain.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("domain must be ASCII/Punycode, not Unicode IDNA") from exc

    for label in labels:
        if not DOMAIN_LABEL_RE.fullmatch(label):
            raise ValueError(
                "domain labels must contain only letters, numbers, and interior hyphens"
            )

    return domain


def normalize_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        raise ValueError("url scheme must be http or https")
    if not parts.netloc:
        raise ValueError("url must include a host")

    scheme = parts.scheme.lower()
    host = parts.hostname.lower() if parts.hostname else ""
    port = f":{parts.port}" if parts.port else ""
    if (scheme, parts.port) in {("http", 80), ("https", 443)}:
        port = ""

    path = parts.path or "/"
    return urlunsplit((scheme, f"{host}{port}", path, parts.query, ""))


def require_controlled_value(value: str, allowed: set[str], field: str) -> str:
    if value not in allowed:
        raise ValueError(f"unsupported {field}")
    return value


def require_case_insensitive_controlled_value(
    value: str, allowed: set[str], field: str
) -> str:
    normalized = next(
        (
            candidate
            for candidate in allowed
            if candidate.casefold() == value.casefold()
        ),
        None,
    )
    if normalized is None:
        raise ValueError(f"unsupported {field}")
    return normalized


def available_trading_venues() -> list[str]:
    return sorted(TRADING_VENUES)


def require_category_tag(value: str) -> str:
    normalized = value.lower()
    if normalized not in CATEGORY_TAGS:
        raise ValueError("unsupported category tag")
    return normalized


def require_trading_venue(value: str) -> str:
    normalized = value.lower()
    if normalized not in TRADING_VENUES:
        raise PydanticCustomError(
            "unsupported_trading_venue",
            "unsupported trading venue",
            {"available_trading_venues": available_trading_venues()},
        )
    return normalized


def registry_validation_error(message: str, **details: object) -> RegistryError:
    return RegistryError(ErrorCode.VALIDATION_ERROR, message, details or None)
