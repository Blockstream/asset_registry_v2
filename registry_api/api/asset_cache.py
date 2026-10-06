import re
from datetime import UTC, timedelta
from email.utils import format_datetime, parsedate_to_datetime
from typing import Annotated

from fastapi import Header, Request, Response
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from registry_api.models import Asset, AssetRegistryVersion

IfModifiedSince = Annotated[
    str | None,
    Header(
        alias="If-Modified-Since",
        description="HTTP date from Last-Modified; returns 304 if assets are unchanged.",
    ),
]

# Accept all three HTTP-date formats, but reject trailing data and date lists.
_DAY = r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)"
_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
_TIME = r"\d{2}:\d{2}:\d{2}"
_HTTP_DATE = re.compile(
    rf"(?:{_DAY}, \d{{2}} {_MONTH} \d{{4}} {_TIME} GMT"
    rf"|(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), "
    rf"\d{{2}}-{_MONTH}-\d{{2}} {_TIME} GMT"
    rf"|{_DAY} {_MONTH} (?: \d|\d{{2}}) {_TIME} \d{{4}})"
)


def prepare_asset_read(
    request: Request,
    response: Response,
    db: Session,
    if_modified_since: str | None,
    *,
    asset_id: str | None = None,
) -> Response | None:
    """Set cache validators and short-circuit unchanged asset reads.

    Every read uses the registry-wide modification time, which deliberately
    invalidates every list, filter, page, and lookup on any asset change,
    including removals. Triggers advance it by at least a second per commit, so
    in-flight commits get a later date. Changes and dates in the current or a
    future database second are never treated as fresh.
    """
    query = select(
        AssetRegistryVersion.modified_at,
        func.date_trunc("second", func.clock_timestamp()),
    )
    if asset_id is not None:
        query = query.where(
            exists().where(
                Asset.asset_id == asset_id.lower(), Asset.status == "active"
            )
        )
    row = db.execute(query).one_or_none()
    # Force revalidation rather than heuristic freshness based on Last-Modified.
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Vary"] = "Accept-Encoding"
    if row is None:
        # Unknown modification time (or a missing asset): never return 304.
        return None

    modified_at, current_second = (value.astimezone(UTC) for value in row)
    # Last-Modified must not be in the future, and changes in the current second
    # (or a database clock that moved backwards) are too recent to validate.
    too_recent = modified_at >= current_second
    if too_recent:
        modified_at = current_second - timedelta(seconds=1)
    response.headers["Last-Modified"] = format_datetime(modified_at, usegmt=True)
    if (
        too_recent
        or if_modified_since is None
        or "if-none-match" in request.headers
        or len(request.headers.getlist("if-modified-since")) != 1
        or _HTTP_DATE.fullmatch(if_modified_since.strip()) is None
    ):
        return None
    try:
        since = parsedate_to_datetime(if_modified_since.strip())
        if since.tzinfo is None:  # Obsolete asctime format is implicitly UTC.
            since = since.replace(tzinfo=UTC)
    except (ValueError, TypeError, OverflowError):
        return None
    # Ignore current/future dates so a skewed client clock cannot pin stale data.
    if modified_at <= since < current_second:
        return Response(status_code=304, headers=dict(response.headers))
    return None
