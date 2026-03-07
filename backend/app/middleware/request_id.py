"""RequestIDMiddleware — inject X-Request-ID on every request and response.

If the client sends an X-Request-ID header, it is echoed back.
If not, a unique req_<hex> ID is generated.
The ID is stored in request.state.request_id for use in error responses.
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex}"
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
