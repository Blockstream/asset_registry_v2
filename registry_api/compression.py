from collections.abc import Collection

from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipMiddleware as StarletteGZipMiddleware
from starlette.middleware.gzip import GZipResponder
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class _ContentTypeAwareGZipResponder(GZipResponder):
    def __init__(
        self,
        app: ASGIApp,
        minimum_size: int,
        *,
        compresslevel: int,
        excluded_content_types: Collection[str],
    ) -> None:
        super().__init__(app, minimum_size, compresslevel=compresslevel)
        self.excluded_content_types = excluded_content_types

    async def send_with_compression(self, message: Message) -> None:
        await super().send_with_compression(message)
        if message["type"] != "http.response.start":
            return
        content_type = Headers(raw=message["headers"]).get("content-type", "")
        media_type = content_type.partition(";")[0].strip().lower()
        if media_type in self.excluded_content_types:
            self.content_type_is_excluded = True


class GZipMiddleware(StarletteGZipMiddleware):
    """Starlette gzip middleware with opt-outs for pre-compressed media.

    Formats such as PNG are already compressed, so wrapping them in gzip usually
    adds CPU and can increase their size. Excluding them also preserves strong
    ETags that identify the original response bytes across Accept-Encoding values.
    """

    def __init__(
        self,
        app: ASGIApp,
        minimum_size: int = 500,
        compresslevel: int = 9,
        excluded_content_types: Collection[str] = (),
    ) -> None:
        super().__init__(app, minimum_size=minimum_size, compresslevel=compresslevel)
        self.excluded_content_types = frozenset(
            content_type.lower() for content_type in excluded_content_types
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.excluded_content_types:
            await super().__call__(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if "gzip" not in headers.get("Accept-Encoding", ""):
            await self.app(scope, receive, send)
            return

        responder = _ContentTypeAwareGZipResponder(
            self.app,
            self.minimum_size,
            compresslevel=self.compresslevel,
            excluded_content_types=self.excluded_content_types,
        )
        await responder(scope, receive, send)
