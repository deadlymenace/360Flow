"""Local-disk object storage used when R2 credentials are not configured."""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path


class LocalStorage:
    name = "local"

    def __init__(self, root: Path):
        self.root = root

    def ensure_ready(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        relative = Path(key)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Invalid storage key: {key}")
        return self.root / relative

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        del content_type
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def put_file(self, key: str, path: Path, content_type: str) -> None:
        """Copy a file in chunks (large world files never sit in memory)."""
        del content_type
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        shutil.copyfile(path, tmp)
        os.replace(tmp, dest)

    def open_stream(self, key: str, chunk_size: int = 1024 * 1024) -> tuple[Iterator[bytes], int]:
        """(chunk iterator, size) for streaming a stored object to a client."""
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        size = path.stat().st_size

        def chunks() -> Iterator[bytes]:
            with path.open("rb") as handle:
                while True:
                    block = handle.read(chunk_size)
                    if not block:
                        return
                    yield block

        return chunks(), size

    def get_bytes(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete_prefix(self, prefix: str) -> None:
        root = self._path(prefix)
        if root.is_file():
            root.unlink()
            return
        if not root.exists():
            return
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        if root.is_dir():
            root.rmdir()

    def list_keys(self, prefix: str) -> list[str]:
        root = self._path(prefix)
        if not root.exists():
            return []
        if root.is_file():
            return [prefix]
        keys = []
        for path in sorted(root.rglob("*")):
            if path.is_file():
                keys.append(path.relative_to(self.root).as_posix())
        return keys

    def presign_get(self, key: str, expires: int = 3600) -> str | None:
        del key, expires
        return None

    def materialize(self, key: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.get_bytes(key))
