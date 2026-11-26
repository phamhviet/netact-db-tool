"""Short-lived disk snapshots; exports never rerun the submitted SQL."""

import secrets
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException


class SQLResultStore:
    def __init__(self, ttl_seconds=3600):
        if ttl_seconds <= 0:
            raise ValueError("SQL_RESULT_TTL_SECONDS must be a positive integer.")
        self.root = Path(tempfile.mkdtemp(prefix="netact-sql-"))
        self.ttl_seconds = ttl_seconds
        self.lock = threading.Lock()
        self.runs = {}

    def new(self):
        self.cleanup()
        token = secrets.token_urlsafe(24)
        self.root.mkdir(mode=0o700, exist_ok=True)
        folder = self.root / token
        folder.mkdir(mode=0o700)
        return token, folder

    def publish(self, token, folder, metadata):
        with self.lock:
            self.runs[token] = {
                "folder": folder,
                "metadata": metadata,
                "expires_at": time.time() + self.ttl_seconds,
                "leases": 0,
            }

    @contextmanager
    def acquire(self, token):
        self.cleanup()
        with self.lock:
            run = self.runs.get(token)
            if run is None or run["expires_at"] <= time.time():
                raise HTTPException(
                    404, "SQL results expired or are unavailable. Run the script again if appropriate."
                )
            run["leases"] += 1
        try:
            yield run
        finally:
            with self.lock:
                run["leases"] -= 1
            self.cleanup()

    def discard(self, token):
        with self.lock:
            run = self.runs.get(token)
            if run:
                run["expires_at"] = 0
        self.cleanup()

    def cleanup(self):
        expired = []
        with self.lock:
            for token, run in list(self.runs.items()):
                if run["expires_at"] <= time.time() and not run["leases"]:
                    expired.append(run["folder"])
                    del self.runs[token]
        for folder in expired:
            shutil.rmtree(folder, ignore_errors=True)

    def close(self):
        with self.lock:
            self.runs.clear()
        shutil.rmtree(self.root, ignore_errors=True)
