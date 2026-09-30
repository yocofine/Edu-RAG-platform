from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

from .config import get_settings


@dataclass(frozen=True)
class ObjectStat:
    size: int
    content_type: str | None = None


class ObjectStore:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.local_root = Path(self.settings.local_storage_path)
        self._client = None
        self._public_client = None

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

    def stat(self, key: str) -> ObjectStat:
        """返回对象元数据，不读取对象正文。"""
        if self.settings.storage_backend == "minio":
            result = self._minio().stat_object(self.settings.minio_bucket, key)
            return ObjectStat(size=int(result.size), content_type=result.content_type)
        target = self._local_path(key)
        return ObjectStat(size=target.stat().st_size)

    def stream(
        self,
        key: str,
        *,
        offset: int = 0,
        length: int | None = None,
        chunk_size: int | None = None,
    ) -> Iterator[bytes]:
        """按块读取整个对象或指定字节区间，避免把大文件一次性放进内存。"""
        chunk_size = chunk_size or max(64 * 1024, self.settings.preview_stream_chunk_kb * 1024)
        remaining = length
        if self.settings.storage_backend == "minio":
            kwargs: dict[str, int] = {}
            if offset:
                kwargs["offset"] = offset
            if length is not None:
                kwargs["length"] = length
            response = self._minio().get_object(self.settings.minio_bucket, key, **kwargs)
            try:
                while remaining is None or remaining > 0:
                    read_size = chunk_size if remaining is None else min(chunk_size, remaining)
                    data = response.read(read_size)
                    if not data:
                        break
                    yield data
                    if remaining is not None:
                        remaining -= len(data)
            finally:
                response.close()
                response.release_conn()
            return

        with self._local_path(key).open("rb") as handle:
            handle.seek(offset)
            while remaining is None or remaining > 0:
                read_size = chunk_size if remaining is None else min(chunk_size, remaining)
                data = handle.read(read_size)
                if not data:
                    break
                yield data
                if remaining is not None:
                    remaining -= len(data)

    def presigned_get_url(self, key: str, *, content_type: str, file_name: str) -> str | None:
        """生成浏览器直连 URL；配置不完整时返回 None 并由 API 代理预览。"""
        if (
            self.settings.storage_backend != "minio"
            or not self.settings.preview_direct_enabled
            or not self.settings.minio_public_endpoint.strip()
        ):
            return None
        if self._public_client is None:
            from minio import Minio

            self._public_client = Minio(
                self.settings.minio_public_endpoint.strip(),
                access_key=self.settings.minio_access_key,
                secret_key=self.settings.minio_secret_key,
                secure=self.settings.minio_public_secure,
            )
        expires = timedelta(seconds=max(60, min(self.settings.preview_url_expiry_seconds, 7 * 24 * 3600)))
        disposition = f"inline; filename*=UTF-8''{quote(file_name)}"
        return self._public_client.presigned_get_object(
            self.settings.minio_bucket,
            key,
            expires=expires,
            response_headers={
                "response-content-type": content_type,
                "response-content-disposition": disposition,
            },
        )

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
