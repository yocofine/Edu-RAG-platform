from __future__ import annotations

from .text_quality import validate_extracted_text

import asyncio
import json
import shutil
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import fitz
from sqlalchemy import update

from ..config import get_settings
from ..db import SessionLocal
from ..events import event_hub
from ..models import DocumentVersion, IngestionJob, JobStatus
from ..object_store import object_store
from .mineru import mineru_client
from qa_core.indexing.encoding import decode_bytes


settings = get_settings()
_lock = asyncio.Lock()


def detect_pdf_route(path: Path) -> str:
    """Choose native extraction, OCR, or MinerU at page-document level."""
    suffix = path.suffix.lower()
    if suffix == ".xmind":
        return "mindmap"
    if suffix in {".csv", ".xls", ".xlsx"}:
        return "table"
    if suffix == ".docx" or (suffix == ".pptx" and not settings.mineru_enabled):
        return "office"
    if suffix in {".txt", ".md"}:
        return "native"
    if suffix != ".pdf":
        return "mineru"
    with fitz.open(path) as pdf:
        pages = max(len(pdf), 1)
        text_pages = 0
        image_rich_pages = 0
        for page in pdf:
            text = page.get_text("text").strip()
            if len(text) >= 20:
                text_pages += 1
            image_area = 0.0
            page_area = max(page.rect.width * page.rect.height, 1)
            for image in page.get_images(full=True):
                for rect in page.get_image_rects(image[0]):
                    image_area += rect.width * rect.height
            if page.get_images(full=True) and image_area / page_area >= 0.12:
                image_rich_pages += 1
        if image_rich_pages / pages >= 0.15:
            return "mineru"
        if text_pages / pages < 0.5:
            return "ocr"
        return "native"


async def _set_status(job_id: str, version_id: str, status: JobStatus, progress: int, **values) -> None:
    with SessionLocal() as db:
        job = db.get(IngestionJob, job_id)
        version = db.get(DocumentVersion, version_id)
        if not job or not version:
            return
        job.status = status.value
        job.progress = progress
        version.ingestion_status = status.value
        for key, value in values.items():
            setattr(job, key, value)
        db.commit()
        payload = {
            "type": "ingestion_status_changed",
            "document_id": version.document_id,
            "version_id": version.id,
            "file_name": version.file_name,
            "section_id": version.document.section_id,
            "status": status.value,
            "progress": progress,
        }
    await event_hub.broadcast(payload)


def _current_progress(job_id: str) -> int:
    """读取任务当前进度，供失败收尾时保留真实进度（而不是直接跳到 100%）。"""
    with SessionLocal() as db:
        job = db.get(IngestionJob, job_id)
        return int(job.progress or 0) if job else 0


def _scaled_progress(low: int, high: int, completed: int, total: int) -> int:
    """把阶段内的 (completed, total) 映射到 [low, high] 总进度区间。

    解析类阶段耗时差异极大（几页文本 vs 上百页扫描件），所以每个阶段都只在自己
    的区间内推进，阶段之间用固定锚点衔接，前端进度条不会长时间停在同一个数值上。
    """
    if total <= 0:
        return low
    ratio = max(0.0, min(1.0, float(completed) / float(total)))
    return int(round(low + (high - low) * ratio))


def _thread_progress_bridge(async_report, *, min_interval: float = 0.4):
    """把 async 进度上报包装成可在工作线程中安全调用的同步回调。

    PyMuPDF 逐页提取和 LibreOffice 转换都跑在 `asyncio.to_thread` 里，不能直接
    await 上报协程，因此这里：
    1. 按 min_interval 节流——逐页回调可能每毫秒一次，不节流会造成海量 DB 写入和
       WebSocket 广播；阶段结束时（completed >= total）总是放行，保证终点不丢。
    2. 用 run_coroutine_threadsafe 把上报协程投递回发起请求的事件循环。
    """
    loop = asyncio.get_running_loop()
    last = [0.0]

    def report(completed: int, total: int) -> None:
        now = time.monotonic()
        if now - last[0] < min_interval and completed < total:
            return
        last[0] = now
        try:
            asyncio.run_coroutine_threadsafe(async_report(completed, total), loop)
        except RuntimeError:
            # 事件循环已关闭（请求已结束）时静默放弃剩余进度上报。
            pass

    return report


def make_progress_reporter(
    job_id: str,
    version_id: str,
    status: "JobStatus",
    low: int,
    high: int,
):
    """生成某阶段的同步进度回调：(completed, total) -> 总进度百分比。"""

    async def report(completed: int, total: int) -> None:
        await _set_status(
            job_id, version_id, status, _scaled_progress(low, high, completed, total)
        )

    return _thread_progress_bridge(report)


MINDMAP_MAX_HEADING_DEPTH = 6


def _mindmap_topic_to_markdown(topic: dict, depth: int, lines: list[str]) -> None:
    """把一个 XMind 主题（含递归子主题）渲染成 Markdown 片段。"""
    title = str(topic.get("title") or "").strip()
    if title:
        lines.append(f"{'#' * min(depth, MINDMAP_MAX_HEADING_DEPTH)} {title}")
    notes = topic.get("notes")
    note = ""
    if isinstance(notes, dict):
        plain = notes.get("plain")
        if isinstance(plain, dict):
            note = str(plain.get("content") or "").strip()
        elif isinstance(plain, str):
            note = plain.strip()
    if note:
        lines.append(note)
    if title or note:
        lines.append("")
    children = topic.get("children")
    attached = children.get("attached") if isinstance(children, dict) else None
    if isinstance(attached, list):
        for child in attached:
            if isinstance(child, dict):
                _mindmap_topic_to_markdown(child, depth + 1, lines)


def _mindmap_from_json(payload: object) -> str:
    """解析 XMind Zen/2020+ 的 content.json。"""
    sheets = payload if isinstance(payload, list) else [payload]
    lines: list[str] = []
    for sheet in sheets:
        if not isinstance(sheet, dict):
            continue
        if len(sheets) > 1:
            sheet_title = str(sheet.get("title") or "").strip()
            if sheet_title:
                lines.extend([f"# {sheet_title}", ""])
        root_topic = sheet.get("rootTopic")
        if isinstance(root_topic, dict):
            _mindmap_topic_to_markdown(root_topic, 1, lines)
    return "\n".join(lines).strip()


def _mindmap_from_xml(raw: bytes) -> str:
    """解析旧版 XMind 8 的 content.xml（命名空间不敏感）。"""
    import xml.etree.ElementTree as ElementTree

    def local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    root = ElementTree.fromstring(raw)
    lines: list[str] = []

    def attached_topics(element):
        """XMind 8 的子主题路径是 <children><topics type="attached"><topic>…"""
        for child in element:
            if local_name(child.tag) != "children":
                continue
            for group in child:
                if local_name(group.tag) != "topics":
                    continue
                for node in group:
                    if local_name(node.tag) == "topic":
                        yield node

    def walk(element, depth: int) -> None:
        title = ""
        note = ""
        for child in element:
            name = local_name(child.tag)
            if name == "title" and child.text:
                title = child.text.strip()
            elif name == "notes":
                for node in child.iter():
                    if local_name(node.tag) == "plain" and node.text:
                        note = node.text.strip()
        if title:
            lines.append(f"{'#' * min(depth, MINDMAP_MAX_HEADING_DEPTH)} {title}")
        if note:
            lines.append(note)
        if title or note:
            lines.append("")
        for node in attached_topics(element):
            walk(node, depth + 1)

    for element in root.iter():
        if local_name(element.tag) == "topic":
            walk(element, 1)
            break
    return "\n".join(lines).strip()


def _extract_mindmap(source: Path) -> tuple[str, dict]:
    """把 XMind 思维导图转成 Markdown。

    .xmind 本质是 ZIP：新版（Zen / 2020+）用 content.json，旧版（XMind 8）用 content.xml，
    两种都尝试，保证历史文件也能解析。
    """
    if not zipfile.is_zipfile(source):
        raise RuntimeError("不是有效的 XMind 文件（应为 ZIP 压缩包）")
    markdown = ""
    parser_name = ""
    with zipfile.ZipFile(source) as bundle:
        names = set(bundle.namelist())
        if "content.json" in names:
            try:
                markdown = _mindmap_from_json(json.loads(bundle.read("content.json").decode("utf-8-sig")))
                parser_name = "xmind_json"
            except Exception:  # noqa: BLE001 - 回退到旧版 content.xml
                markdown = ""
        if not markdown and "content.xml" in names:
            markdown = _mindmap_from_xml(bundle.read("content.xml"))
            parser_name = "xmind_xml"
    if not markdown:
        raise RuntimeError("XMind 文件里没有提取到任何主题内容")
    topic_count = sum(1 for line in markdown.splitlines() if line.startswith("#"))
    return markdown, {"parser": parser_name, "topic_count": topic_count, "output_chars": len(markdown)}


def _extract_native_pdf(path: Path, on_progress=None) -> tuple[str, dict]:
    """按页提取 PDF 文本层，并逐页回调解析进度（完成页数 / 总页数）。"""
    parts = []
    with fitz.open(path) as pdf:
        total = len(pdf)
        for idx, page in enumerate(pdf, start=1):
            text = page.get_text("text").strip()
            if text:
                parts.append(f"## 第{idx}页\n\n{text}")
            if on_progress is not None:
                on_progress(idx, total)
    return "\n\n".join(parts), {"parser": "pymupdf", "page_count": total, "page_success_ratio": 1.0 if parts else 0.0}


async def _convert_office_preview(source: Path, output_dir: Path) -> Path | None:
    if source.suffix.lower() not in {".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}:
        return None
    process = await asyncio.create_subprocess_exec(
        "libreoffice", "--headless", "--convert-to", "pdf", "--outdir", str(output_dir), str(source),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await process.communicate()
    candidate = output_dir / f"{source.stem}.pdf"
    return candidate if process.returncode == 0 and candidate.exists() else None


def _find_markdown(root: Path) -> Path:
    candidates = list(root.rglob("*.md"))
    if not candidates:
        raise RuntimeError("MinerU结果中没有Markdown文件")
    return max(candidates, key=lambda item: item.stat().st_size)


def _mineru_content_list_to_markdown(root: Path) -> tuple[str, int] | None:
    """使用 MinerU content_list 中的 page_idx 重建带真实页码标记的 Markdown。"""
    candidates = list(root.rglob("*_content_list.json"))
    if not candidates:
        return None
    content_list_path = max(candidates, key=lambda item: item.stat().st_size)
    items = json.loads(content_list_path.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        return None

    pages: dict[int, list[str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        page_index = int(item.get("page_idx") or 0)
        item_type = str(item.get("type") or "").lower()
        if item_type in {"header", "footer", "page_number"}:
            continue
        blocks = pages.setdefault(page_index, [])
        if item_type == "text":
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            level = int(item.get("text_level") or 0)
            blocks.append(f"{'#' * min(max(level, 1), 6)} {text}" if level else text)
        elif item_type == "list":
            list_items = [str(value).strip() for value in item.get("list_items") or [] if str(value).strip()]
            blocks.extend(list_items)
        elif item_type == "image":
            path = str(item.get("img_path") or "").strip()
            captions = [str(value).strip() for value in item.get("image_caption") or [] if str(value).strip()]
            footnotes = [str(value).strip() for value in item.get("image_footnote") or [] if str(value).strip()]
            if captions:
                blocks.extend(captions)
            if path:
                blocks.append(f"![]({path})")
            if footnotes:
                blocks.extend(footnotes)
        elif item_type == "table":
            captions = [str(value).strip() for value in item.get("table_caption") or [] if str(value).strip()]
            body = str(item.get("table_body") or "").strip()
            footnotes = [str(value).strip() for value in item.get("table_footnote") or [] if str(value).strip()]
            blocks.extend(captions)
            if body:
                blocks.append(body)
            blocks.extend(footnotes)

    if not pages:
        return None
    markdown_pages = []
    for page_index in sorted(pages):
        content = "\n\n".join(block for block in pages[page_index] if block.strip()).strip()
        markdown_pages.append(f"## 第{page_index + 1}页\n\n{content}".strip())
    return "\n\n".join(markdown_pages), max(pages) + 1


def _safe_extract(bundle: zipfile.ZipFile, destination: Path) -> None:
    root = destination.resolve()
    for member in bundle.infolist():
        target = (destination / member.filename).resolve()
        if root != target and root not in target.parents:
            raise RuntimeError("MinerU返回了不安全的压缩包路径")
    bundle.extractall(destination)


async def process_ingestion(job_id: str) -> None:
    async with _lock:
        with SessionLocal() as db:
            job = db.get(IngestionJob, job_id)
            if not job:
                return
            version = db.get(DocumentVersion, job.document_version_id)
            if not version:
                return
            version_id = version.id
            object_key = version.object_key
            file_name = version.file_name
            source_metadata = {
                "document_id": version.document_id,
                "document_version_id": version.id,
                "file_name": version.file_name,
                "original_file_name": version.file_name,
                "file_type": f".{version.file_type.lstrip('.')}",
                "original_file_type": version.file_type,
                "section_id": version.document.section_id,
                "uploader": version.uploader.username,
            }
            job.started_at = datetime.now(timezone.utc)
            db.commit()
        try:
            await _set_status(job_id, version_id, JobStatus.detecting, 5)
            with tempfile.TemporaryDirectory(prefix="edu-rag-ingest-") as tmp:
                root = Path(tmp)
                source = root / file_name
                source.write_bytes(object_store.get(object_key))
                parser = detect_pdf_route(source)
                with SessionLocal() as db:
                    db.get(IngestionJob, job_id).parser = parser
                    db.commit()

                parsed_markdown: str
                quality: dict
                index_source = source
                if parser == "native" and source.suffix.lower() == ".pdf":
                    await _set_status(job_id, version_id, JobStatus.extracting_text, 25)
                    parsed_markdown, quality = await asyncio.to_thread(
                        _extract_native_pdf,
                        source,
                        make_progress_reporter(job_id, version_id, JobStatus.extracting_text, 25, 44),
                    )
                elif parser == "native":
                    await _set_status(job_id, version_id, JobStatus.extracting_text, 25)
                    decoded = await asyncio.to_thread(decode_bytes, source.read_bytes())
                    parsed_markdown = decoded.text
                    quality = {
                        "parser": "native_text",
                        "encoding": decoded.encoding,
                        "output_chars": len(parsed_markdown),
                    }
                    await _set_status(job_id, version_id, JobStatus.extracting_text, 44)
                elif parser == "table":
                    await _set_status(job_id, version_id, JobStatus.table_normalizing, 25)
                    from qa_core.indexing.document_loaders import load_file
                    table_docs = await asyncio.to_thread(load_file, source)
                    parsed_markdown = "\n\n".join(doc.page_content for doc in table_docs)
                    quality = {"parser": "native_table", "table_blocks": len(table_docs), "output_chars": len(parsed_markdown)}
                    await _set_status(job_id, version_id, JobStatus.table_normalizing, 44)
                elif parser == "mindmap":
                    await _set_status(job_id, version_id, JobStatus.extracting_text, 25)
                    parsed_markdown, quality = await asyncio.to_thread(_extract_mindmap, source)
                    await _set_status(job_id, version_id, JobStatus.extracting_text, 44)
                elif parser == "office":
                    await _set_status(job_id, version_id, JobStatus.extracting_text, 25)
                    from qa_core.indexing.document_loaders import load_file
                    office_docs = await asyncio.to_thread(load_file, source)
                    if source.suffix.lower() == ".pptx":
                        parsed_markdown = "\n\n".join(
                            f"## 第{int((doc.metadata or {}).get('page', index)) + 1}页\n\n{doc.page_content}"
                            for index, doc in enumerate(office_docs)
                        )
                    else:
                        parsed_markdown = "\n\n".join(doc.page_content for doc in office_docs)
                    quality = {
                        "parser": "python_docx" if source.suffix.lower() == ".docx" else "python_pptx",
                        "document_blocks": len(office_docs),
                        "output_chars": len(parsed_markdown),
                    }
                    await _set_status(job_id, version_id, JobStatus.extracting_text, 44)
                elif not settings.mineru_enabled:
                    if source.suffix.lower() in {".doc", ".docx", ".ppt", ".pptx"}:
                        await _set_status(job_id, version_id, JobStatus.converting_preview, 25)
                        fallback_pdf = await _convert_office_preview(source, root)
                        if not fallback_pdf:
                            raise RuntimeError("MinerU未启用且Office文件转换失败")
                        parsed_markdown, quality = await asyncio.to_thread(
                            _extract_native_pdf,
                            fallback_pdf,
                            make_progress_reporter(job_id, version_id, JobStatus.converting_preview, 30, 44),
                        )
                        quality["parser"] = "libreoffice_pymupdf"
                    else:
                        parsed_markdown = ""
                        quality = {
                            "parser": "deferred",
                            "passed": False,
                            "reason": "MinerU暂未启用，扫描件或图片资料需要稍后重新解析",
                        }
                else:
                    stage = JobStatus.ocr_processing if parser == "ocr" else JobStatus.mineru_parsing
                    await _set_status(job_id, version_id, stage, 25)
                    archive = root / "mineru-result.zip"
                    await mineru_client.parse(
                        source,
                        archive,
                        ocr_only=parser == "ocr",
                        on_progress=make_progress_reporter(job_id, version_id, stage, 25, 44),
                    )
                    result_dir = root / "mineru"
                    result_dir.mkdir()
                    with zipfile.ZipFile(archive) as bundle:
                        _safe_extract(bundle, result_dir)
                    markdown_path = _find_markdown(result_dir)
                    paged_result = _mineru_content_list_to_markdown(markdown_path.parent)
                    if paged_result is not None:
                        parsed_markdown, mineru_page_count = paged_result
                    else:
                        parsed_markdown = markdown_path.read_text(encoding="utf-8")
                        mineru_page_count = 0
                    # 图片/流程图交给多模态模型补文字说明，避免图内信息在检索里丢失
                    if settings.image_analysis_enabled:
                        await _set_status(job_id, version_id, JobStatus.image_analyzing, 45)
                        from .image_analysis import analyze_markdown_images

                        parsed_markdown = await analyze_markdown_images(
                            parsed_markdown,
                            markdown_path.parent,
                            on_progress=make_progress_reporter(job_id, version_id, JobStatus.image_analyzing, 45, 59),
                        )
                    quality = {
                        "parser": "mineru",
                        "output_chars": len(parsed_markdown),
                        "page_count": mineru_page_count,
                    }
                    parsed_key = f"parsed/{version_id}/result.zip"
                    object_store.put(parsed_key, archive.read_bytes(), "application/zip")

                await _set_status(job_id, version_id, JobStatus.quality_checking, 60)
                non_whitespace = len("".join(parsed_markdown.split()))
                text_quality = validate_extracted_text(parsed_markdown, minimum_characters=20)
                quality["non_whitespace_chars"] = non_whitespace
                quality["text_quality"] = text_quality
                quality["passed"] = bool(quality.get("passed", non_whitespace >= 20) and text_quality["healthy"])
                if not quality["passed"]:
                    await _set_status(
                        job_id, version_id, JobStatus.needs_review, 100,
                        quality_report=json.dumps(quality, ensure_ascii=False),
                        finished_at=datetime.now(timezone.utc),
                    )
                    return

                parsed_key = f"parsed/{version_id}/content.md"
                object_store.put(parsed_key, parsed_markdown.encode("utf-8"), "text/markdown")
                await _set_status(job_id, version_id, JobStatus.converting_preview, 65)
                preview = await _convert_office_preview(source, root)
                if preview:
                    preview_key = f"previews/{version_id}.pdf"
                    object_store.put(preview_key, preview.read_bytes(), "application/pdf")
                    with SessionLocal() as db:
                        db.get(DocumentVersion, version_id).preview_object_key = preview_key
                        db.commit()
                await _set_status(job_id, version_id, JobStatus.chunking, 70)
                ingest_dir = root / "index" / "documents_data"
                ingest_dir.mkdir(parents=True)
                if parser == "table":
                    index_target = ingest_dir / f"{version_id}{source.suffix.lower()}"
                    shutil.copy2(source, index_target)
                else:
                    (ingest_dir / f"{version_id}.md").write_text(parsed_markdown, encoding="utf-8")
                await _set_status(job_id, version_id, JobStatus.indexing, 85)
                from .document_index import index_document
                event_loop = asyncio.get_running_loop()

                def report_embedding_progress(completed: int, total: int) -> None:
                    ratio = completed / max(total, 1)
                    progress = min(98, 85 + round(ratio * 13))
                    future = asyncio.run_coroutine_threadsafe(
                        _set_status(job_id, version_id, JobStatus.indexing, progress),
                        event_loop,
                    )
                    future.result()

                chunk_ids = await asyncio.to_thread(
                    index_document,
                    index_target if parser == "table" else ingest_dir / f"{version_id}.md",
                    version_id=version_id,
                    metadata_overrides=source_metadata,
                    progress_callback=report_embedding_progress,
                )

                with SessionLocal() as db:
                    version = db.get(DocumentVersion, version_id)
                    previous = db.get(DocumentVersion, version.document.current_version_id) if version.document.current_version_id else None
                    previous_chunk_ids = previous.index_chunk_ids if previous and previous.id != version.id else "[]"
                    version.published_at = datetime.now(timezone.utc)
                    version.index_chunk_ids = json.dumps(chunk_ids)
                    version.document.current_version_id = version.id
                    db.commit()
                if previous_chunk_ids != "[]":
                    from .document_index import remove_document_chunks
                    await asyncio.to_thread(remove_document_chunks, previous_chunk_ids)
                await _set_status(
                    job_id, version_id, JobStatus.published, 100,
                    quality_report=json.dumps(quality, ensure_ascii=False),
                    finished_at=datetime.now(timezone.utc),
                )
                await event_hub.broadcast({
                    "type": "document_published",
                    "document_id": version.document_id,
                    "version_id": version_id,
                })
        except Exception as exc:
            # 失败时保留最后真实进度：跳到 100% 会让前端进度条看起来像"已完成"。
            await _set_status(
                job_id, version_id, JobStatus.failed, _current_progress(job_id),
                error_message=str(exc)[:4000], finished_at=datetime.now(timezone.utc),
            )


async def publish_reviewed_content(job_id: str) -> None:
    async with _lock:
        with SessionLocal() as db:
            job = db.get(IngestionJob, job_id)
            version = db.get(DocumentVersion, job.document_version_id) if job else None
            if not job or not version:
                return
            version_id = version.id
            source_metadata = {
                "document_id": version.document_id,
                "document_version_id": version.id,
                "file_name": version.file_name,
                "original_file_name": version.file_name,
                "file_type": f".{version.file_type.lstrip('.')}",
                "original_file_type": version.file_type,
                "section_id": version.document.section_id,
                "uploader": version.uploader.username,
            }
        try:
            markdown = object_store.get(f"parsed/{version_id}/content.md").decode("utf-8")
            with tempfile.TemporaryDirectory(prefix="edu-rag-reviewed-") as tmp:
                path = Path(tmp) / f"{version_id}.md"
                path.write_text(markdown, encoding="utf-8")
                await _set_status(job_id, version_id, JobStatus.chunking, 60)
                from .document_index import index_document, remove_document_chunks
                event_loop = asyncio.get_running_loop()

                def report_reviewed_embedding_progress(completed: int, total: int) -> None:
                    ratio = completed / max(total, 1)
                    progress = min(98, 85 + round(ratio * 13))
                    future = asyncio.run_coroutine_threadsafe(
                        _set_status(job_id, version_id, JobStatus.indexing, progress),
                        event_loop,
                    )
                    future.result()

                await _set_status(job_id, version_id, JobStatus.indexing, 85)
                chunk_ids = await asyncio.to_thread(
                    index_document,
                    path,
                    version_id=version_id,
                    metadata_overrides=source_metadata,
                    progress_callback=report_reviewed_embedding_progress,
                )
                with SessionLocal() as db:
                    version = db.get(DocumentVersion, version_id)
                    previous = db.get(DocumentVersion, version.document.current_version_id) if version.document.current_version_id else None
                    previous_ids = previous.index_chunk_ids if previous and previous.id != version.id else "[]"
                    version.index_chunk_ids = json.dumps(chunk_ids)
                    version.published_at = datetime.now(timezone.utc)
                    version.document.current_version_id = version.id
                    db.commit()
                if previous_ids != "[]":
                    await asyncio.to_thread(remove_document_chunks, previous_ids)
                await _set_status(
                    job_id, version_id, JobStatus.published, 100,
                    quality_report=json.dumps({"reviewed": True, "non_whitespace_chars": len("".join(markdown.split()))}, ensure_ascii=False),
                    finished_at=datetime.now(timezone.utc),
                )
        except Exception as exc:
            # 失败时保留最后真实进度：跳到 100% 会让前端进度条看起来像"已完成"。
            await _set_status(
                job_id, version_id, JobStatus.failed, _current_progress(job_id),
                error_message=str(exc)[:4000], finished_at=datetime.now(timezone.utc),
            )
