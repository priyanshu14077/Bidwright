"""Blob storage. Local disk for the PoC; Azure Blob Storage implements the same protocol later."""

import hashlib
from pathlib import Path
from typing import Protocol

from sog.config import settings


class BlobStore(Protocol):
    def put(self, data: bytes, file_name: str) -> tuple[str, str]:
        """Store bytes unchanged. Returns (sha256, uri)."""
        ...

    def get(self, uri: str) -> bytes: ...


class LocalBlobStore:
    """Content-addressed: the same bytes always land at the same path."""

    def __init__(self, root: Path):
        self.root = root

    def put(self, data: bytes, file_name: str) -> tuple[str, str]:
        sha = hashlib.sha256(data).hexdigest()
        path = self.root / sha[:2] / sha / Path(file_name).name
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return sha, f"local://{path.relative_to(self.root)}"

    def get(self, uri: str) -> bytes:
        return (self.root / uri.removeprefix("local://")).read_bytes()

    def path(self, uri: str) -> Path:
        return self.root / uri.removeprefix("local://")


blob_store = LocalBlobStore(settings.storage_dir)
