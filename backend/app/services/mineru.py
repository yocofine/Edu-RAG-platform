from __future__ import annotations

import asyncio
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
        """
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


mineru_client = MinerUClient()
