"""Shared CSV serialization and streaming response cleanup."""

import base64
import csv
import io
import json
from urllib.parse import quote

import anyio
from fastapi.responses import FileResponse, StreamingResponse


class CleanupResponse:
    def __init__(self, *args, cleanup, **kwargs):
        super().__init__(*args, **kwargs)
        self.cleanup = cleanup

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Background tasks are not guaranteed to run after a disconnected or
            # failed send. Always release DB resources / snapshot leases here.
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(self.cleanup)


class CleanupStreamingResponse(CleanupResponse, StreamingResponse):
    pass


class CleanupFileResponse(CleanupResponse, FileResponse):
    pass


class CSVEncoder:
    def __init__(self):
        self.buffer = io.StringIO(newline="")
        self.writer = csv.writer(self.buffer)

    def row(self, values):
        self.buffer.seek(0)
        self.buffer.truncate(0)
        self.writer.writerow(values)
        return self.buffer.getvalue().encode("utf-8")


def display_value(value):
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "base64:" + base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def csv_response(resources, result, first, filename):
    columns = list(result.keys())

    def generate():
        encoder = CSVEncoder()
        try:
            yield b"\xef\xbb\xbf" + encoder.row(columns)
            batch = first
            while batch:
                for row in batch:
                    yield encoder.row([display_value(value) for value in row])
                batch = result.fetchmany(1000)
        finally:
            resources.close()

    return CleanupStreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename=table.csv; filename*=UTF-8''"
            + quote(filename, safe=""),
            "Cache-Control": "no-store",
        },
        cleanup=resources.close,
    )
