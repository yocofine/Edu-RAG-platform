from __future__ import annotations

from io import BytesIO
from pathlib import Path

from .config import get_settings


class ObjectStore:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.local_root = Path(self.settings.local_storage_path)
        self._client = None

    def _local_path(self, key: str) -> Path:
        root = self.local_root.resolve()
        target = (root / key).resolve()
        if root != target and root not in target.parents:
            raise ValueError("非法对象路径")
        return target

    def _minio(self):
        if self._client is None:
            from minio import Minio
            self._client = Minio(
                self.settings.minio_endpoint,
                access_key=self.settings.minio_access_key,
                secret_key=self.settings.minio_secret_key,
                secure=self.settings.minio_secure,
            )
            if not self._client.bucket_exists(self.settings.minio_bucket):
                self._client.make_bucket(self.settings.minio_bucket)
        return self._client

    def put(self, key: str, data: bytes, content_type: str) -> None:
        if self.settings.storage_backend == "minio":
            self._minio().put_object(
                self.settings.minio_bucket, key, BytesIO(data), len(data), content_type=content_type
            )
            return
        target = self._local_path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def get(self, key: str) -> bytes:
        if self.settings.storage_backend == "minio":
            response = self._minio().get_object(self.settings.minio_bucket, key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()
        return self._local_path(key).read_bytes()

    def delete(self, key: str) -> None:
        if self.settings.storage_backend == "minio":
            self._minio().remove_object(self.settings.minio_bucket, key)
            return
        self._local_path(key).unlink(missing_ok=True)


object_store = ObjectStore()
