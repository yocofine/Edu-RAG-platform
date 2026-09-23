"""用多模态模型给 MinerU 解析出的图片/流程图补文字说明。

MinerU 的 pipeline 后端会把流程图、示意图判定为 image 区域，只留一个图片占位，
图里的文字不进正文，检索时自然找不到。这里把图片交给视觉模型生成中文说明，
再写回 Markdown，让图内信息重新进入切分和索引。
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import re
from collections.abc import Callable
from pathlib import Path

from openai import AsyncOpenAI

from ..config import get_settings

IMAGE_REF_RE = re.compile(r"!\[[^\]]*\]\((?P<path>[^)\s]+)\)")

IMAGE_PROMPT = (
    "这是一张从文档里截取的图片。请用中文描述它的内容："
    "如果是流程图、流程图或结构图，按顺序列出图中的步骤、判断分支和关键文字；"
    "如果是表格，整理成简洁的文字；如果是照片或图标，简要说明主题。"
    "只输出描述本身，不要添加任何前后缀或解释。"
)

PLACEHOLDER_UNREAD = "【图片说明】该图片未生成文字说明。"


def _mime_for(path: Path) -> str:
    """按扩展名推断图片 MIME 类型。"""
    return mimetypes.guess_type(path.name)[0] or "image/png"


async def _describe_image(client: AsyncOpenAI, model: str, path: Path) -> str:
    """调用多模态模型描述单张图片，返回说明文本。"""
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode()
    response = await client.chat.completions.create(
        model=model,
        max_tokens=1600,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": IMAGE_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{_mime_for(path)};base64,{b64}"},
                    },
                ],
            }
        ],
    )
    return (response.choices[0].message.content or "").strip()


async def analyze_markdown_images(
    markdown: str,
    base_dir: Path,
    on_progress: Callable[[int, int], None] | None = None,
) -> str:
    """把 Markdown 里的图片占位替换成视觉模型生成的文字说明。

    参数：
        markdown: MinerU 解析出的 Markdown 原文。
        base_dir: Markdown 所在目录，图片引用按相对路径解析。
        on_progress: 可选进度回调，签名为 (已完成图片数, 待处理图片总数)。

    返回：
        图片占位已替换为文字说明的 Markdown；未启用或没有图片时原样返回。
    """
    settings = get_settings()
    if not settings.image_analysis_enabled or not markdown:
        return markdown

    refs = list(IMAGE_REF_RE.finditer(markdown))
    if not refs:
        return markdown

    # 同一张图片可能被引用多次，先去重再调用模型，避免重复开销
    unique_paths: list[str] = []
    for match in refs:
        ref = match.group("path").strip()
        if ref and ref not in unique_paths:
            unique_paths.append(ref)
    limit = max(int(settings.image_analysis_max_images), 0)
    unique_paths = unique_paths[:limit]
    if not unique_paths:
        return markdown

    model = settings.image_analysis_model or settings.llm_model
    client = AsyncOpenAI(api_key=settings.dashscope_api_key, base_url=settings.llm_base_url)
    semaphore = asyncio.Semaphore(max(int(settings.image_analysis_concurrency), 1))
    total_images = len(unique_paths)
    completed_images = 0

    def _report_progress() -> None:
        """每处理完一张图片（含跳过与失败）上报一次，进度不会因异常而中断。"""
        nonlocal completed_images
        completed_images += 1
        if on_progress is None:
            return
        try:
            on_progress(completed_images, total_images)
        except Exception:  # noqa: BLE001 - 进度上报是尽力而为
            pass

    async def describe(ref: str) -> tuple[str, str]:
        candidate = (base_dir / ref).resolve()
        if not candidate.is_file():
            _report_progress()
            return ref, ""
        async with semaphore:
            try:
                return ref, await _describe_image(client, model, candidate)
            except Exception:
                return ref, ""
            finally:
                _report_progress()

    results = await asyncio.gather(*(describe(ref) for ref in unique_paths))
    descriptions = {ref: text for ref, text in results}

    def replace(match: re.Match) -> str:
        ref = match.group("path").strip()
        text = descriptions.get(ref)
        if not text:
            return PLACEHOLDER_UNREAD
        return f"【图片说明】{text}"

    return IMAGE_REF_RE.sub(replace, markdown)
