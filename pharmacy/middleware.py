"""Request-id middleware: one id per request, attached to every log line."""

import uuid
from contextvars import ContextVar

_request_id: ContextVar[str] = ContextVar("request_id", default="")

HEADER = "X-Request-ID"


def get_request_id():
    return _request_id.get()


class RequestIDMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get(HEADER) or str(uuid.uuid4())
        token = _request_id.set(incoming)
        try:
            request.request_id = incoming
            response = self.get_response(request)
            response[HEADER] = incoming
            return response
        finally:
            _request_id.reset(token)
