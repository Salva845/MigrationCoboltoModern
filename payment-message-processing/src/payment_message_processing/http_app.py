"""Minimal stdlib HTTP front end exposing POST /transactions/validate.

Kept dependency-free (``http.server``) to mirror the zero-runtime-deps stance of
payment-processing-core. The controller holds all logic; this module only does
JSON transport, UTF-8 encoding and routing.
"""

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from payment_processing_core import ErrorCode

from .controller import TransactionValidationController
from .processing_controller import TransactionProcessingController

logger = logging.getLogger("payment_message_processing")

VALIDATE_PATH = "/transactions/validate"
PROCESS_PATH = "/transactions/process"


def make_handler(
    controller: TransactionValidationController,
    processing_controller: TransactionProcessingController | None = None,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _write_json(self, status_code: int, body: dict) -> None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:  # noqa: N802 - http.server naming.
            route = self.path.rstrip("/")
            if route == VALIDATE_PATH:
                handler = controller.validate
            elif route == PROCESS_PATH and processing_controller is not None:
                handler = processing_controller.process
            else:
                self._write_json(404, {"status": "not found", "path": self.path})
                return

            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else None
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                self._write_json(
                    400,
                    {
                        "success": False,
                        "status": ErrorCode.INVALID_FORMAT.value,
                        "error_code": ErrorCode.INVALID_FORMAT.value,
                        "message": f"invalid JSON body: {exc}",
                    },
                )
                return

            response = handler(payload)
            self._write_json(response.status_code, response.body)

        def log_message(self, fmt: str, *args) -> None:
            logger.info("%s - %s", self.address_string(), fmt % args)

    return Handler


def build_server(
    controller: TransactionValidationController,
    host: str = "127.0.0.1",
    port: int = 8080,
    processing_controller: TransactionProcessingController | None = None,
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(controller, processing_controller))
