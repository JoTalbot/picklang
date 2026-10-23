"""CAS-слой доказуемости в стиле WEAVE: артефакты адресуются хэшем содержимого (SHA-256).

Свойства:
- один и тот же байтовый контент хранится ровно один раз (дедупликация);
- любой артефакт можно проверить: пересчитать хэш и сравнить с адресом;
- подмена байта в хранилище обнаруживается верификацией, а не «верой в лог».
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(obj: Any) -> bytes:
    """Детерминированная сериализация: один и тот же объект -> одни и те же байты -> один хэш."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


class ContentAddressedStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.objects = self.root / "objects"
        self.objects.mkdir(parents=True, exist_ok=True)

    def _path(self, ref: str) -> Path:
        return self.objects / ref[:2] / ref[2:]

    def put_bytes(self, data: bytes) -> str:
        ref = sha256_hex(data)
        path = self._path(ref)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(dir=path.parent, prefix=".cas-", suffix=".tmp")
            tmp = Path(name)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp, path)  # уникальный файл для каждого писателя
            finally:
                tmp.unlink(missing_ok=True)
        return ref

    def put_json(self, obj: Any) -> str:
        return self.put_bytes(canonical_json(obj))

    def put_text(self, text: str) -> str:
        return self.put_bytes(text.encode("utf-8"))

    def has(self, ref: str) -> bool:
        return self._path(ref).exists()

    def get_bytes(self, ref: str) -> bytes:
        path = self._path(ref)
        if not path.exists():
            raise KeyError(f"CAS object missing: {ref}")
        return path.read_bytes()

    def get_json(self, ref: str) -> Any:
        return json.loads(self.get_bytes(ref).decode("utf-8"))

    def verify(self, ref: str) -> bool:
        """Пересчитать хэш хранимых байтов и сравнить с адресом."""
        try:
            return sha256_hex(self.get_bytes(ref)) == ref
        except KeyError:
            return False

    def stats(self) -> dict[str, Any]:
        files = [p for p in self.objects.rglob("*") if p.is_file()]
        return {"objects": len(files), "bytes": sum(p.stat().st_size for p in files)}
