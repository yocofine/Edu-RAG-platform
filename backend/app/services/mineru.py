from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import httpx

from ..config import get_settings


ProgressCallback = Callable[[int, int], None]


def _ratio_from_status(payload: dict) -> float | None:
    """尝试从 MinerU 任务状态里读出真实进度（0..1）。

    不同 MinerU 版本返回的字段不一致，所以递归检查常见字段名；都拿不到时返回 None。
    None 表示服务端没有提供可验证的百分比，前端应展示不定进度状态，而不是伪造数字。
    """
    candidates = [payload]
    for key in ("data", "task", "result"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)
    for candidate in candidates:
        for key in ("progress", "progress_percent", "percent", "percentage", "ratio"):
            raw = candidate.get(key)
            if raw is None or isinstance(raw, bool):
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value > 1.0:
                value = value / 100.0
            return max(0.0, min(1.0, value))

        total = candidate.get("total_pages") or candidate.get("page_count")
        done = candidate.get("extracted_pages") or candidate.get("parsed_pages") or candidate.get("pages_done")
        try:
            total_value = float(total)
            done_value = float(done)
        except (TypeError, ValueError):
            continue
        if total_value > 0:
            return max(0.0, min(1.0, done_value / total_value))
    return None


def _notify(on_progress: ProgressCallback | None, completed: int, total: int) -> None:
    """调用进度回调；进度上报失败绝不能影响解析本身。"""
    if on_progress is None:
        return
    try:
        on_progress(completed, total)
    except Exception:  # noqa: BLE001 - 进度上报是尽力而为，不能中断解析
        pass


_parse_semaphore: "asyncio.Semaphore | None" = None
_parse_semaphore_limit: int = 0


def _get_parse_semaphore() -> asyncio.Semaphore:
    """返回进程级解析闸门，限制同时在跑的 MinerU 解析数量。

    为什么要闸门：每个入库任务都是一个独立的 asyncio 任务（见 job_dispatcher），
    批量上传时 N 个文件会同时向 MinerU 提交解析，容易把云端并发/带宽打满并互相拖慢。
    这里按 MINERU_MAX_CONCURRENT_PARSES 排队，超出上限的任务等待前面的解析结束。

    返回：
        asyncio.Semaphore: 并发上限取自配置，进程内复用同一个实例。
    """
    global _parse_semaphore, _parse_semaphore_limit
    limit = max(int(getattr(get_settings(), "mineru_max_concurrent_parses", 2) or 2), 1)
    if _parse_semaphore is None or _parse_semaphore_limit != limit:
        _parse_semaphore = asyncio.Semaphore(limit)
        _parse_semaphore_limit = limit
    return _parse_semaphore


class MinerUClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def parse(
        self,
        path: Path,
        output_zip: Path,
        *,
        ocr_only: bool = False,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        """提交 MinerU 解析任务并轮询到完成，全程回报解析进度。

        进度只取服务端返回的真实指标。服务端只回 status 时不上报虚假百分比，
        前端改用明确的不定进度状态。

        并发控制：每个入库任务都是独立的 asyncio 任务（job_dispatcher 里 1 job = 1 task），
        批量上传时会把云端并发和带宽一次性打满，所以这里用进程级信号量把同时在跑的
        解析数限制在 ``MINERU_MAX_CONCURRENT_PARSES``（默认 2）；超出的任务排队等待。
        """
        async with _get_parse_semaphore():
            backend = (self.settings.mineru_backend or "api").strip().lower()
            if backend == "api":
                await self._parse_api(path, output_zip, ocr_only=ocr_only, on_progress=on_progress)
                return
            if backend == "local":
                await self._parse_local(path, output_zip, ocr_only=ocr_only, on_progress=on_progress)
                return
            raise RuntimeError(f"不支持的 MINERU_BACKEND：{backend}")

    async def _parse_local(
        self,
        path: Path,
        output_zip: Path,
        *,
        ocr_only: bool,
        on_progress: ProgressCallback | None,
    ) -> None:
        timeout = httpx.Timeout(self.settings.mineru_timeout_seconds)
        async with httpx.AsyncClient(base_url=self.settings.mineru_url, timeout=timeout) as client:
            with path.open("rb") as handle:
                response = await client.post(
                    "/tasks",
                    files={"files": (path.name, handle, "application/octet-stream")},
                    data={
                        "backend": "pipeline",
                        "lang": "ch",
                        "formula_enable": "true",
                        "table_enable": "true",
                        "effort": "medium",
                        "return_md": "true",
                        "return_middle_json": "true",
                        "return_content_list": "true",
                        "return_images": "true",
                        "response_format_zip": "true",
                    },
                )
            response.raise_for_status()
            task = response.json()
            task_id = task["task_id"]
            while True:
                status_response = await client.get(f"/tasks/{task_id}")
                status_response.raise_for_status()
                status = status_response.json()
                state = str(status.get("status") or "").lower()
                if state == "completed":
                    _notify(on_progress, 1, 1)
                    break
                if state == "failed":
                    raise RuntimeError(status.get("error") or "MinerU解析失败")
                ratio = _ratio_from_status(status)
                if ratio is not None:
                    # 用千分比保持整数精度，避免 int(0.42) 之类的取整丢精度。
                    _notify(on_progress, int(round(ratio * 1000)), 1000)
                await asyncio.sleep(1)
            result = await client.get(f"/tasks/{task_id}/result")
            result.raise_for_status()
            output_zip.write_bytes(result.content)

    @staticmethod
    def _api_data(payload: dict, operation: str) -> dict:
        if int(payload.get("code", -1)) != 0:
            raise RuntimeError(f"MinerU API {operation}失败：{payload.get('msg') or payload.get('code')}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError(f"MinerU API {operation}返回缺少 data")
        return data

    async def _upload_file(self, client: httpx.AsyncClient, upload_url: str, path: Path) -> None:
        async def chunks():
            with path.open("rb") as handle:
                while True:
                    chunk = await asyncio.to_thread(handle.read, 1024 * 1024)
                    if not chunk:
                        break
                    yield chunk

        # 官方预签名地址要求 PUT 时不设置 Content-Type。
        response = await client.put(
            upload_url,
            content=chunks(),
            headers={"Content-Length": str(path.stat().st_size)},
        )
        response.raise_for_status()

    async def _download_zip(self, client: httpx.AsyncClient, url: str, output_zip: Path) -> None:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            with output_zip.open("wb") as handle:
                async for chunk in response.aiter_bytes(1024 * 1024):
                    await asyncio.to_thread(handle.write, chunk)

    async def _parse_api(
        self,
        path: Path,
        output_zip: Path,
        *,
        ocr_only: bool,
        on_progress: ProgressCallback | None,
    ) -> None:
        token = (self.settings.mineru_api_key or "").strip()
        if not token:
            raise RuntimeError("MINERU_BACKEND=api 但未配置 MINERU_API_KEY")
        base_url = (self.settings.mineru_api_base_url or "https://mineru.net/api/v4").rstrip("/")
        model_version = (self.settings.mineru_api_model_version or "vlm").strip()
        if model_version not in {"pipeline", "vlm", "MinerU-HTML"}:
            raise RuntimeError(f"MINERU_API_MODEL_VERSION 无效：{model_version}")
        data_id = f"edu-rag-{uuid.uuid4().hex}"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        timeout = httpx.Timeout(
            connect=30.0,
            read=float(self.settings.mineru_timeout_seconds),
            write=float(self.settings.mineru_timeout_seconds),
            pool=30.0,
        )
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            request_payload = {
                "files": [{"name": path.name, "data_id": data_id, "is_ocr": bool(ocr_only)}],
                "model_version": model_version,
                "enable_formula": True,
                "enable_table": True,
                "language": "ch",
            }
            response = await client.post(f"{base_url}/file-urls/batch", headers=headers, json=request_payload)
            response.raise_for_status()
            task_data = self._api_data(response.json(), "申请上传链接")
            batch_id = str(task_data.get("batch_id") or "").strip()
            file_urls = task_data.get("file_urls") or []
            if not batch_id or not file_urls:
                raise RuntimeError("MinerU API 未返回 batch_id 或文件上传地址")
            await self._upload_file(client, str(file_urls[0]), path)

            deadline = time.monotonic() + float(self.settings.mineru_timeout_seconds)
            poll_interval = max(float(self.settings.mineru_api_poll_interval_seconds), 0.5)
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"MinerU API 解析超时（{self.settings.mineru_timeout_seconds} 秒）")
                status_response = await client.get(
                    f"{base_url}/extract-results/batch/{batch_id}",
                    headers={"Authorization": f"Bearer {token}", "Accept": "*/*"},
                )
                status_response.raise_for_status()
                status_data = self._api_data(status_response.json(), "查询任务")
                results = status_data.get("extract_result") or []
                result = next(
                    (
                        item for item in results
                        if str(item.get("data_id") or "") == data_id
                        or str(item.get("file_name") or "") == path.name
                    ),
                    results[0] if len(results) == 1 else None,
                )
                if not isinstance(result, dict):
                    await asyncio.sleep(poll_interval)
                    continue
                state = str(result.get("state") or "").strip().lower()
                if state == "done":
                    zip_url = str(result.get("full_zip_url") or "").strip()
                    if not zip_url:
                        raise RuntimeError("MinerU API 任务已完成但缺少 full_zip_url")
                    await self._download_zip(client, zip_url, output_zip)
                    _notify(on_progress, 1, 1)
                    return
                if state == "failed":
                    raise RuntimeError(result.get("err_msg") or "MinerU API 解析失败")
                progress = result.get("extract_progress") or {}
                completed = int(progress.get("extracted_pages") or 0)
                total = int(progress.get("total_pages") or 0)
                if total > 0:
                    _notify(on_progress, completed, total)
                await asyncio.sleep(poll_interval)


mineru_client = MinerUClient()
