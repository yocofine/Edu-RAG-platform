from __future__ import annotations

import hashlib
import mimetypes
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased

from ..config import get_settings
from ..db import get_db
from ..deps import admin_user, csrf_protected, current_user
from ..events import event_hub
from ..models import AuditLog, Document, DocumentVersion, IngestionJob, JobStatus, Section, User
from ..object_store import object_store
from ..rate_limit import limit_upload
from ..services.document_index import remove_document_chunks
from ..schemas import BatchDocumentRequest, DocumentUpdateRequest, ParsedContentRequest
from ..services.job_dispatcher import cancel_job, schedule_ingestion, schedule_reviewed_publish
from ..services.serial import serialized
from qa_core.indexing.encoding import decode_bytes


router = APIRouter(prefix="/documents", tags=["文件"])
settings = get_settings()
ALLOWED_SUFFIXES = {
    ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".csv", ".txt", ".md", ".xmind",
    ".jpg", ".jpeg", ".png",
}


def normalized_name(name: str) -> str:
    stem = Path(name).stem.strip().lower()
    return re.sub(r"\s+", " ", stem)


def safe_filename(name: str) -> str:
    candidate = Path((name or "未命名文件").replace("\\", "/")).name
    candidate = re.sub(r"[\x00-\x1f<>:\"/\\|?*]", "_", candidate).strip(" .")
    return candidate[:240] or "未命名文件"


IMAGE_PREVIEW_TYPES = {"jpg", "jpeg", "png", "gif", "webp", "bmp"}
TEXT_PREVIEW_TYPES = {"txt", "md", "markdown", "csv", "tsv"}


def preview_kind(file_type: str, has_preview_object: bool) -> str:
    """返回浏览器可直接内联渲染的预览方式：image / pdf / text / none。

    Office 文档只有在 LibreOffice 转出 PDF 预览件后才能在线预览，此时原始后缀仍是
    docx/xlsx，所以必须先看 preview_object_key，再回落到按后缀判断。
    """
    value = str(file_type or "").lower().lstrip(".")
    if value in IMAGE_PREVIEW_TYPES:
        return "image"
    if has_preview_object or value == "pdf":
        return "pdf"
    if value in TEXT_PREVIEW_TYPES:
        return "text"
    return "none"


def serialize(document: Document, version: DocumentVersion) -> dict:
    kind = preview_kind(version.file_type, bool(version.preview_object_key))
    return {
        "id": document.id,
        "version_id": version.id,
        "name": version.file_name,
        "file_type": version.file_type,
        "version_no": version.version_no,
        "section_id": document.section_id,
        "section_name": document.section.name,
        "tags": version.tags,
        "uploader": version.uploader.username,
        "uploaded_at": version.uploaded_at,
        "ingestion_status": version.ingestion_status,
        # 真实字节数：前端据此显示文件大小并统计存储占用（不再使用占位值）。
        "size_bytes": int(version.size_bytes or 0),
        "preview_kind": kind,
        "preview_available": kind != "none",
    }


def current_query():
    return (
        select(Document, DocumentVersion)
        .join(DocumentVersion, Document.current_version_id == DocumentVersion.id)
        .where(Document.deleted_at.is_(None))
    )


def _editable_document(document_id: str, user: User, db: Session) -> Document:
    document = db.get(Document, document_id)
    if not document or document.deleted_at:
        raise HTTPException(status_code=404, detail="文件不存在")
    if user.role != "admin" and document.owner_id != user.id:
        raise HTTPException(status_code=403, detail="无权限修改该文件")
    return document


@router.get("")
def list_documents(
    section_id: str | None = None,
    # 普通用户也需要浏览/预览现有文件，所以列表只要求登录；
    # 上传、改名、移动、删除、回滚等写操作仍是管理员专属。
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    query = current_query()
    if section_id:
        query = query.where(Document.section_id == section_id)
    rows = db.execute(query.order_by(DocumentVersion.uploaded_at.desc())).all()
    return {"items": [serialize(doc, version) for doc, version in rows]}


@router.get("/recent")
def recent_documents(
    limit: int = Query(default=10, ge=1, le=50),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(Document, DocumentVersion)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(Document.deleted_at.is_(None))
        .order_by(DocumentVersion.uploaded_at.desc())
        .limit(limit)
    ).all()
    return {"items": [serialize(doc, version) for doc, version in rows]}


@router.get("/search")
def search_documents(
    query: str = "",
    section: str | None = None,
    uploader: str | None = None,
    latest: bool = True,
    limit: int = Query(default=10, ge=1, le=50),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    stmt = current_query() if latest else (
        select(Document, DocumentVersion)
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(Document.deleted_at.is_(None), DocumentVersion.ingestion_status == JobStatus.published.value)
    )
    if query.strip():
        pattern = f"%{query.strip()}%"
        stmt = stmt.where(or_(DocumentVersion.file_name.ilike(pattern), DocumentVersion.tags.ilike(pattern)))
    if section:
        stmt = stmt.join(Section, Document.section_id == Section.id).where(Section.name.ilike(f"%{section}%"))
    if uploader:
        uploader_user = aliased(User)
        stmt = stmt.join(uploader_user, DocumentVersion.uploader_id == uploader_user.id).where(
            uploader_user.username.ilike(f"%{uploader}%")
        )
    rows = db.execute(stmt.order_by(DocumentVersion.uploaded_at.desc()).limit(limit)).all()
    return {"items": [serialize(doc, version) for doc, version in rows]}


def _cancel_document_jobs(document_id: str, db: Session) -> list[str]:
    jobs = db.scalars(
        select(IngestionJob)
        .join(DocumentVersion, IngestionJob.document_version_id == DocumentVersion.id)
        .where(
            DocumentVersion.document_id == document_id,
            IngestionJob.status.not_in({JobStatus.published.value, JobStatus.failed.value, JobStatus.cancelled.value}),
        )
    ).all()
    now = datetime.now(timezone.utc)
    for job in jobs:
        job.status = JobStatus.cancelled.value
        job.error_message = "文件已删除，入库任务已取消"
        job.finished_at = now
    return [job.id for job in jobs]


@router.post("/batch-delete", dependencies=[Depends(csrf_protected)])
async def batch_delete_documents(payload: BatchDocumentRequest, background_tasks: BackgroundTasks, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    deleted = 0
    cancelled_job_ids: list[str] = []
    for document_id in dict.fromkeys(payload.ids):
        document = _editable_document(document_id, user, db)
        document.deleted_at = datetime.now(timezone.utc)
        versions = db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == document.id)).all()
        for version in versions:
            background_tasks.add_task(remove_document_chunks, version.index_chunk_ids)
        cancelled_job_ids.extend(_cancel_document_jobs(document.id, db))
        db.add(AuditLog(action="删除", target_type="文件", target_name=document.normalized_name, detail="批量操作，进入30天回收站", operator_id=user.id))
        deleted += 1
    db.commit()
    for job_id in cancelled_job_ids:
        cancel_job(job_id)
    return {"message": f"已删除{deleted}个文件", "count": deleted}


@router.post("/batch-move", dependencies=[Depends(csrf_protected)])
def batch_move_documents(payload: BatchDocumentRequest, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    if not payload.section_id or not db.get(Section, payload.section_id):
        raise HTTPException(status_code=400, detail="目标板块不存在")
    moved = 0
    for document_id in dict.fromkeys(payload.ids):
        document = _editable_document(document_id, user, db)
        document.section_id = payload.section_id
        db.add(AuditLog(action="移动", target_type="文件", target_name=document.normalized_name, operator_id=user.id))
        moved += 1
    db.commit()
    return {"message": f"已移动{moved}个文件", "count": moved}


@router.post("/upload", status_code=202, dependencies=[Depends(csrf_protected), Depends(limit_upload)])
@serialized("edu_rag_upload", timeout_seconds=3600, distributed=False)
async def upload_document(
    file: UploadFile = File(...),
    section_id: str = Form(...),
    tags: str = Form(default=""),
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    upload_name = safe_filename(file.filename or "")
    suffix = Path(upload_name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=415, detail="不支持该文件格式，首版不允许视频和音频")
    section_obj = db.get(Section, section_id)
    if not section_obj:
        raise HTTPException(status_code=400, detail="板块不存在")
    max_bytes = settings.max_upload_mb * 1024 * 1024
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"单文件不能超过{settings.max_upload_mb}MB")
    checksum = hashlib.sha256(content).hexdigest()
    normalized = normalized_name(upload_name)
    document = db.scalar(
        select(Document).where(
            Document.section_id == section_id,
            Document.normalized_name == normalized,
            Document.deleted_at.is_(None),
        )
    )
    if document is None:
        document = Document(normalized_name=normalized, section_id=section_id, owner_id=user.id)
        db.add(document)
        db.flush()
        version_no = 1
    else:
        duplicate = db.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == document.id,
                DocumentVersion.checksum_sha256 == checksum,
            )
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="相同内容的文件版本已经存在")
        version_no = (db.scalar(select(func.max(DocumentVersion.version_no)).where(DocumentVersion.document_id == document.id)) or 0) + 1

    version = DocumentVersion(
        document_id=document.id,
        version_no=version_no,
        file_name=upload_name,
        file_type=suffix.lstrip("."),
        size_bytes=len(content),
        checksum_sha256=checksum,
        object_key="pending",
        tags=tags.strip(),
        uploader_id=user.id,
    )
    db.add(version)
    db.flush()
    version.object_key = f"originals/{document.id}/{version.id}/{version.file_name}"
    object_store.put(version.object_key, content, file.content_type or "application/octet-stream")
    job = IngestionJob(document_version_id=version.id)
    db.add(job)
    db.add(AuditLog(action="上传", target_type="文件", target_name=version.file_name, detail=f"v{version_no}", operator_id=user.id))
    db.commit()
    await event_hub.broadcast({"type": "document_uploaded", "document": serialize(document, version)})
    schedule_ingestion(job.id)
    return {"document": serialize(document, version), "job_id": job.id}


@router.put("/{document_id}", dependencies=[Depends(csrf_protected)])
def update_document(document_id: str, payload: DocumentUpdateRequest, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    document = _editable_document(document_id, user, db)
    version = db.get(DocumentVersion, document.current_version_id) if document.current_version_id else None
    if not version:
        raise HTTPException(status_code=409, detail="文件没有可修改的当前版本")
    if payload.name:
        new_name = safe_filename(payload.name)
        if Path(new_name).suffix.lower() != Path(version.file_name).suffix.lower():
            raise HTTPException(status_code=400, detail="重命名不能修改文件扩展名")
        version.file_name = new_name
        document.normalized_name = normalized_name(new_name)
    if payload.section_id:
        if not db.get(Section, payload.section_id):
            raise HTTPException(status_code=400, detail="目标板块不存在")
        document.section_id = payload.section_id
    db.add(AuditLog(action="修改", target_type="文件", target_name=version.file_name, operator_id=user.id))
    db.commit()
    db.refresh(document); db.refresh(version)
    return {"item": serialize(document, version)}


def _resolve_version(document_id: str, version_id: str | None, db: Session) -> tuple[Document, DocumentVersion]:
    document = db.get(Document, document_id)
    if not document or document.deleted_at:
        raise HTTPException(status_code=404, detail="文件不存在")
    resolved = version_id or document.current_version_id
    version = db.get(DocumentVersion, resolved) if resolved else None
    if not version or version.document_id != document.id:
        raise HTTPException(status_code=404, detail="文件版本不存在")
    return document, version


@router.get("/{document_id}/versions")
def versions(document_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="文件不存在")
    items = db.scalars(
        select(DocumentVersion).where(DocumentVersion.document_id == document_id).order_by(DocumentVersion.version_no.desc())
    ).all()
    return {"items": [serialize(document, item) for item in items]}


@router.get("/{document_id}/quality-report")
def quality_report(document_id: str, version_id: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _, version = _resolve_version(document_id, version_id, db)
    job = db.scalar(select(IngestionJob).where(IngestionJob.document_version_id == version.id).order_by(IngestionJob.created_at.desc()))
    return {"version_id": version.id, "status": version.ingestion_status, "quality_report": job.quality_report if job else None, "error_message": job.error_message if job else None}


@router.get("/{document_id}/parsed-content")
def parsed_content(document_id: str, version_id: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _, version = _resolve_version(document_id, version_id, db)
    try:
        content = object_store.get(f"parsed/{version.id}/content.md").decode("utf-8")
    except Exception as exc:
        raise HTTPException(status_code=404, detail="尚无可复核的解析内容") from exc
    return {"version_id": version.id, "content": content}


@router.put("/{document_id}/parsed-content", dependencies=[Depends(csrf_protected)])
def update_parsed_content(document_id: str, payload: ParsedContentRequest, version_id: str | None = None, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    document, version = _resolve_version(document_id, version_id, db)
    if user.role != "admin" and document.owner_id != user.id:
        raise HTTPException(status_code=403, detail="无权限修改解析内容")
    object_store.put(f"parsed/{version.id}/content.md", payload.content.encode("utf-8"), "text/markdown")
    version.ingestion_status = JobStatus.needs_review.value
    db.commit()
    return {"message": "解析内容已保存，确认后可发布"}


@router.post("/{document_id}/publish", status_code=202, dependencies=[Depends(csrf_protected)])
async def publish_reviewed(document_id: str, version_id: str | None = None, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    document, version = _resolve_version(document_id, version_id, db)
    if user.role != "admin" and document.owner_id != user.id:
        raise HTTPException(status_code=403, detail="无权限发布")
    try:
        object_store.get(f"parsed/{version.id}/content.md")
    except Exception as exc:
        raise HTTPException(status_code=409, detail="没有可发布的解析内容") from exc
    job = IngestionJob(document_version_id=version.id, status=JobStatus.queued.value)
    db.add(job); db.commit()
    schedule_reviewed_publish(job.id)
    return {"message": "已提交人工复核发布任务", "job_id": job.id}


@router.post("/{document_id}/versions/{version_id}/rollback", status_code=202, dependencies=[Depends(csrf_protected)])
async def rollback_version(
    document_id: str,
    version_id: str,
    # 回滚会新建一个版本并替换当前版本，属于写操作：普通用户只能查看/预览文件。
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    document, source_version = _resolve_version(document_id, version_id, db)
    if user.role != "admin" and document.owner_id != user.id:
        raise HTTPException(status_code=403, detail="无权限回滚")
    next_no = (db.scalar(select(func.max(DocumentVersion.version_no)).where(DocumentVersion.document_id == document.id)) or 0) + 1
    version = DocumentVersion(
        document_id=document.id, version_no=next_no, file_name=source_version.file_name,
        file_type=source_version.file_type, size_bytes=source_version.size_bytes,
        checksum_sha256=source_version.checksum_sha256, object_key="pending",
        tags=source_version.tags, uploader_id=user.id,
    )
    db.add(version); db.flush()
    version.object_key = f"originals/{document.id}/{version.id}/{version.file_name}"
    object_store.put(
        version.object_key, object_store.get(source_version.object_key),
        mimetypes.guess_type(version.file_name)[0] or "application/octet-stream",
    )
    job = IngestionJob(document_version_id=version.id)
    db.add(job)
    db.add(AuditLog(action="回滚", target_type="文件", target_name=version.file_name, detail=f"从v{source_version.version_no}生成v{next_no}", operator_id=user.id))
    db.commit()
    await event_hub.broadcast({"type": "document_uploaded", "document": serialize(document, version)})
    schedule_ingestion(job.id)
    return {"document": serialize(document, version), "job_id": job.id}


@router.get("/{document_id}/download")
def download(document_id: str, version_id: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _, version = _resolve_version(document_id, version_id, db)
    media = mimetypes.guess_type(version.file_name)[0] or "application/octet-stream"
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(version.file_name)}"}
    return Response(object_store.get(version.object_key), media_type=media, headers=headers)


BYTE_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _parse_byte_range(value: str, total_size: int) -> tuple[int, int]:
    """解析浏览器常用的单段 bytes Range；多段 Range 明确返回 416。"""
    match = BYTE_RANGE_RE.fullmatch(value.strip())
    if not match or total_size <= 0:
        raise ValueError("invalid range")
    first, last = match.groups()
    if not first and not last:
        raise ValueError("empty range")
    if not first:
        suffix_length = int(last)
        if suffix_length <= 0:
            raise ValueError("invalid suffix range")
        start = max(total_size - suffix_length, 0)
        return start, total_size - 1
    start = int(first)
    if start >= total_size:
        raise ValueError("range starts after object")
    end = total_size - 1 if not last else min(int(last), total_size - 1)
    if end < start:
        raise ValueError("range ends before start")
    return start, end


def _inline_headers(file_name: str, content_length: int) -> dict[str, str]:
    return {
        "Accept-Ranges": "bytes",
        "X-Accel-Buffering": "no",
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(file_name)}",
        "Content-Length": str(content_length),
        "Cache-Control": "private, max-age=300",
        "X-Content-Type-Options": "nosniff",
    }


def _text_preview_body(version: DocumentVersion) -> tuple[bytes, str]:
    try:
        return object_store.get(f"parsed/{version.id}/content.md"), "text/markdown"
    except Exception:
        # 解析任务尚未完成时也要保证文本预览不会把 GBK/ANSI 原始字节直接交给浏览器。
        decoded = decode_bytes(object_store.get(version.object_key))
        return decoded.text.encode("utf-8"), "text/plain"


def _binary_preview_target(version: DocumentVersion) -> tuple[str, str, str]:
    key = version.preview_object_key or version.object_key
    media = "application/pdf" if version.preview_object_key else (
        mimetypes.guess_type(version.file_name)[0] or "application/octet-stream"
    )
    file_name = f"{Path(version.file_name).stem}.pdf" if version.preview_object_key else version.file_name
    return key, media, file_name


@router.head("/{document_id}/preview")
def preview_head(
    document_id: str,
    version_id: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _, version = _resolve_version(document_id, version_id, db)
    kind = preview_kind(version.file_type, bool(version.preview_object_key))
    if kind == "none":
        raise HTTPException(status_code=409, detail="该格式暂不支持在线预览，请下载后查看")
    if kind == "text":
        body, media = _text_preview_body(version)
        return Response(status_code=200, media_type=media, headers=_inline_headers(version.file_name, len(body)))
    key, media, file_name = _binary_preview_target(version)
    object_stat = object_store.stat(key)
    return Response(status_code=200, media_type=media, headers=_inline_headers(file_name, object_stat.size))


@router.get("/{document_id}/preview")
def preview(
    document_id: str,
    request: Request,
    version_id: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _, version = _resolve_version(document_id, version_id, db)
    kind = preview_kind(version.file_type, bool(version.preview_object_key))
    # 不可内联预览的格式（如尚未转出 PDF 的 Office 原件）不能把原始字节塞给 iframe，
    # 否则浏览器会当成下载。这里显式拒绝，让前端提示"下载后查看"。
    if kind == "none":
        raise HTTPException(status_code=409, detail="该格式暂不支持在线预览，请下载后查看")
    if kind == "text":
        body, media = _text_preview_body(version)
        return Response(body, media_type=media, headers={"Content-Disposition": "inline"})

    key, media, file_name = _binary_preview_target(version)
    direct_url = object_store.presigned_get_url(key, content_type=media, file_name=file_name)
    if direct_url:
        return RedirectResponse(
            direct_url,
            status_code=307,
            headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
        )

    object_stat = object_store.stat(key)
    total_size = object_stat.size
    range_header = request.headers.get("range")
    if not range_header:
        return StreamingResponse(
            object_store.stream(key),
            media_type=media,
            headers=_inline_headers(file_name, total_size),
        )

    try:
        start, end = _parse_byte_range(range_header, total_size)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=416,
            detail="请求的文件区间不可用",
            headers={"Accept-Ranges": "bytes", "Content-Range": f"bytes */{total_size}"},
        )
    length = end - start + 1
    headers = _inline_headers(file_name, length)
    headers["Content-Range"] = f"bytes {start}-{end}/{total_size}"
    return StreamingResponse(
        object_store.stream(key, offset=start, length=length),
        status_code=206,
        media_type=media,
        headers=headers,
    )


@router.delete("/{document_id}", dependencies=[Depends(csrf_protected)])
async def recycle(document_id: str, background_tasks: BackgroundTasks, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="文件不存在")
    if user.role != "admin" and document.owner_id != user.id:
        raise HTTPException(status_code=403, detail="无权限删除")
    document.deleted_at = datetime.now(timezone.utc)
    versions = db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == document.id)).all()
    chunk_sets = [version.index_chunk_ids for version in versions]
    cancelled_job_ids = _cancel_document_jobs(document.id, db)
    db.add(AuditLog(action="删除", target_type="文件", target_name=document.normalized_name, detail="进入30天回收站", operator_id=user.id))
    db.commit()
    for job_id in cancelled_job_ids:
        cancel_job(job_id)
    for chunk_ids in chunk_sets:
        background_tasks.add_task(remove_document_chunks, chunk_ids)
    return {"message": "文件已进入回收站"}


@router.post("/{document_id}/restore", status_code=202, dependencies=[Depends(csrf_protected)])
async def restore(
    document_id: str,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    document = db.get(Document, document_id)
    if not document or not document.deleted_at:
        raise HTTPException(status_code=404, detail="回收站中不存在该文件")
    if user.role != "admin" and document.owner_id != user.id:
        raise HTTPException(status_code=403, detail="无权限恢复")
    version = db.get(DocumentVersion, document.current_version_id) if document.current_version_id else None
    if not version:
        version = db.scalar(select(DocumentVersion).where(DocumentVersion.document_id == document.id).order_by(DocumentVersion.version_no.desc()))
    document.deleted_at = None
    version.ingestion_status = JobStatus.queued.value
    job = IngestionJob(document_version_id=version.id)
    db.add(job)
    db.commit()
    schedule_ingestion(job.id)
    return {"message": "文件已恢复并重新入库", "job_id": job.id}
