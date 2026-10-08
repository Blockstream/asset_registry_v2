import re
from datetime import UTC, datetime
from email.utils import format_datetime, parsedate_to_datetime
from typing import Annotated

from fastapi import Header, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from registry_api.models import Asset

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

    A registry-wide timestamp intentionally invalidates every list/filter/page.
    Include inactive records so removals also invalidate previously cached lists.
    Asset writes update assets.updated_at, including metadata and icon changes.
    """
    query = select(Asset.updated_at).order_by(Asset.updated_at.desc()).limit(1)
    if asset_id is not None:
        query = query.where(Asset.asset_id == asset_id.lower(), Asset.status == "active")
    modified_at = db.scalar(query)
    # Force revalidation rather than heuristic freshness based on Last-Modified.
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Vary"] = "Accept-Encoding"
    if modified_at is None:
        # Unknown modification time (or a missing asset): never return 304.
        return None

    modified_at = min(modified_at.astimezone(UTC), datetime.now(UTC)).replace(
        microsecond=0
    )
    response.headers["Last-Modified"] = format_datetime(modified_at, usegmt=True)
    if (
        if_modified_since is None
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
    if modified_at <= since:
        return Response(status_code=304, headers=dict(response.headers))
    return None
