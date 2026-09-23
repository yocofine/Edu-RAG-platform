from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from urllib.parse import parse_qs, urlparse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import admin_user, csrf_protected, current_user
from ..models import AuditLog, Document, DocumentVersion, IngestionJob, RuleItem, ScriptItem, Section, User
from ..schemas import DingTalkSettingsRequest
from ..services.dingtalk import send_test_notification
from ..services.ingestion import process_ingestion
from ..services.system_settings import get_dingtalk_settings, save_dingtalk_settings


router = APIRouter(tags=["管理"])


def _validate_dingtalk_webhook(value: str) -> None:
    if not value:
        return
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not hostname.endswith("dingtalk.com"):
        raise HTTPException(status_code=400, detail="Webhook 必须是钉钉官方 HTTPS 地址")
    if not parse_qs(parsed.query).get("access_token"):
        raise HTTPException(status_code=400, detail="Webhook 缺少 access_token")


def _masked_webhook(value: str) -> str:
    if not value:
        return ""
    parsed = urlparse(value)
    token = (parse_qs(parsed.query).get("access_token") or [""])[0]
    suffix = token[-4:] if token else ""
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?access_token=****{suffix}"


def _job_preview_kind(version: DocumentVersion | None) -> str:
    if version is None:
        return "none"
    file_type = str(version.file_type or "").lower().lstrip(".")
    if file_type in {"jpg", "jpeg", "png", "gif", "webp", "bmp"}:
        return "image"
    if version.preview_object_key or file_type == "pdf":
        return "pdf"
    if file_type in {"txt", "md", "markdown", "csv", "tsv"}:
        return "text"
    return "none"


@router.get("/stats")
def stats(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {
        "documents": db.scalar(select(func.count()).select_from(Document).where(Document.deleted_at.is_(None))) or 0,
        "scripts": db.scalar(select(func.count()).select_from(ScriptItem).where(ScriptItem.deleted_at.is_(None))) or 0,
        "rules": db.scalar(select(func.count()).select_from(RuleItem).where(RuleItem.deleted_at.is_(None))) or 0,
        "members": db.scalar(select(func.count()).select_from(User).where(User.disabled.is_(False))) or 0,
        "sections": db.scalar(select(func.count()).select_from(Section)) or 0,
    }


@router.get("/settings/dingtalk")
def get_dingtalk_robot_settings(_: User = Depends(admin_user)):
    config = get_dingtalk_settings()
    return {
        "enabled": bool(config.get("enabled")),
        "configured": bool(config.get("webhook")),
        "webhook_masked": _masked_webhook(str(config.get("webhook") or "")),
        "secret_configured": bool(config.get("secret")),
    }


@router.put("/settings/dingtalk", dependencies=[Depends(csrf_protected)])
def update_dingtalk_robot_settings(
    payload: DingTalkSettingsRequest,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    current = get_dingtalk_settings()
    webhook = payload.webhook.strip()
    _validate_dingtalk_webhook(webhook)
    effective_webhook = webhook or str(current.get("webhook") or "")
    if payload.enabled and not effective_webhook:
        raise HTTPException(status_code=400, detail="启用钉钉机器人前请填写 Webhook")
    saved = save_dingtalk_settings(
        enabled=payload.enabled,
        webhook=webhook,
        secret=payload.secret,
        updated_by=user.id,
    )
    db.add(AuditLog(
        action="修改",
        target_type="系统设置",
        target_name="钉钉机器人",
        detail="启用" if saved.get("enabled") else "停用",
        operator_id=user.id,
    ))
    db.commit()
    return {
        "message": "钉钉机器人配置已保存",
        "enabled": bool(saved.get("enabled")),
        "configured": bool(saved.get("webhook")),
        "webhook_masked": _masked_webhook(str(saved.get("webhook") or "")),
        "secret_configured": bool(saved.get("secret")),
    }


@router.post("/settings/dingtalk/test", dependencies=[Depends(csrf_protected)])
async def test_dingtalk_robot_settings(
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    try:
        await send_test_notification(user.username)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"测试消息发送失败：{exc}") from exc
    db.add(AuditLog(
        action="测试",
        target_type="系统设置",
        target_name="钉钉机器人",
        detail="测试消息发送成功",
        operator_id=user.id,
    ))
    db.commit()
    return {"message": "测试消息已发送到钉钉群"}


@router.get("/ingestion-jobs")
def ingestion_jobs(user: User = Depends(current_user), db: Session = Depends(get_db)):
    query = select(IngestionJob).join(DocumentVersion, IngestionJob.document_version_id == DocumentVersion.id)
    if user.role != "admin":
        query = query.where(DocumentVersion.uploader_id == user.id)
    items = db.scalars(query.order_by(IngestionJob.created_at.desc()).limit(100)).all()
    result = []
    for item in items:
        version = db.get(DocumentVersion, item.document_version_id)
        preview = _job_preview_kind(version)
        result.append({
        "id": item.id, "document_version_id": item.document_version_id,
        "document_id": version.document_id if version else None,
        "file_name": version.file_name if version else "未知文件",
        "file_type": version.file_type if version else None,
        "section_id": version.document.section_id if version else None,
        "uploader": version.uploader.username if version else None,
        "size_bytes": int(version.size_bytes or 0) if version else 0,
        "preview_kind": preview,
        "preview_available": preview != "none",
        "status": item.status, "progress": item.progress, "parser": item.parser,
        "error_message": item.error_message, "quality_report": item.quality_report,
        "created_at": item.created_at, "finished_at": item.finished_at,
        })
    return {"items": result}


@router.post("/ingestion-jobs/{job_id}/retry", dependencies=[Depends(csrf_protected)])
def retry_ingestion(
    job_id: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    job = db.get(IngestionJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="入库任务不存在")
    # A published version can be reparsed after parser/encoding/font fixes.
    # Reusing the same job regenerates parsed content, preview PDF and chunks,
    # then atomically replaces the previous index on success.
    if job.status not in {"failed", "needs_review", "interrupted", "published"}:
        raise HTTPException(status_code=409, detail="当前任务状态不能重试")
    job.status = "queued"
    job.progress = 0
    job.error_message = None
    job.retry_count += 1
    db.commit()
    background_tasks.add_task(process_ingestion, job.id)
    return {"message": "任务已重新排队"}


@router.get("/logs")
def logs(operator: str = "", action: str = "", user: User = Depends(admin_user), db: Session = Depends(get_db)):
    query = select(AuditLog).join(User, AuditLog.operator_id == User.id)
    if operator.strip():
        query = query.where(User.username.ilike(f"%{operator.strip()}%"))
    if action.strip():
        query = query.where(AuditLog.action == action.strip())
    items = db.scalars(query.order_by(AuditLog.created_at.desc()).limit(500)).all()
    return {"items": [{
        "id": item.id, "action": item.action, "target_type": item.target_type,
        "target_name": item.target_name, "detail": item.detail,
        "operator": item.operator.username, "created_at": item.created_at,
    } for item in items]}


@router.delete("/logs", dependencies=[Depends(csrf_protected)])
def clear_logs(admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    items = db.scalars(select(AuditLog)).all()
    count = len(items)
    for item in items:
        db.delete(item)
    db.commit()
    return {"message": "日志已清空", "count": count}


@router.get("/members")
def members(user: User = Depends(admin_user), db: Session = Depends(get_db)):
    items = db.scalars(select(User).order_by(User.created_at.desc())).all()
    return {"items": [{
        "id": item.id, "username": item.username, "role": item.role,
        "created_at": item.created_at, "disabled": item.disabled,
    } for item in items]}


@router.put("/members/{user_id}/role", dependencies=[Depends(csrf_protected)])
def change_role(user_id: int, role: str, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    if role not in {"admin", "user"}:
        raise HTTPException(status_code=400, detail="角色无效")
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="成员不存在")
    if target.id == admin.id and role != "admin":
        raise HTTPException(status_code=400, detail="不能移除自己的管理员权限")
    target.role = role
    db.commit()
    return {"message": "角色已更新"}
