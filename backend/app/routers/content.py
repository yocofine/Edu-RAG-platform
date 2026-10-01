import logging
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import admin_user, csrf_protected, current_user
from ..models import AuditLog, RuleItem, ScriptItem, User
from ..schemas import ContentRequest
from ..services.content_index import delete_content_item, index_content_item
from ..services.dingtalk import dingtalk_configured, send_rule_notification


logger = logging.getLogger(__name__)


router = APIRouter(tags=["话术与规则"])


def model_for(kind: str):
    return ScriptItem if kind == "scripts" else RuleItem


def serialize(item) -> dict:
    return {
        "id": item.id, "title": item.title, "content": item.content,
        "group_name": item.group_name, "tags": item.tags,
        "owner": item.owner.username, "created_at": item.created_at, "updated_at": item.updated_at,
    }


def require_write_permission(kind: str, user: User, item=None) -> None:
    if kind == "rules" and user.role != "admin":
        raise HTTPException(status_code=403, detail="规则通知仅允许管理员修改")
    if kind == "scripts" and item is not None and item.owner_id != user.id:
        raise HTTPException(status_code=403, detail="话术库已按用户隔离，无权限修改他人话术")


def owned_item(kind: str, item_id: str, user: User, db: Session):
    """话术直接在 SQL 层按 owner_id 取数，管理员也不例外。"""
    if kind == "scripts":
        return db.scalar(
            select(ScriptItem).where(
                ScriptItem.id == item_id,
                ScriptItem.owner_id == user.id,
                ScriptItem.deleted_at.is_(None),
            )
        )
    return db.scalar(
        select(RuleItem).where(
            RuleItem.id == item_id,
            RuleItem.deleted_at.is_(None),
        )
    )


def queue_content_index(background_tasks: BackgroundTasks, kind: str, item_id: str) -> None:
    """在后台任务里刷新话术/规则的向量索引。

    话术写入独立的 script collection（检索时按 owner_id 隔离），
    规则写入独立的 rule collection（全员可读）。写库成功后以 BackgroundTask 异步建索引，
    避免 Milvus 写入耗时拖慢接口响应；失败只影响该条目的可检索性，不影响业务读写。
    """
    background_tasks.add_task(index_content_item, kind, item_id)


@router.get("/{kind}")
def list_items(kind: Literal["scripts", "rules"], user: User = Depends(current_user), db: Session = Depends(get_db)):
    model = model_for(kind)
    query = select(model).where(model.deleted_at.is_(None))
    if kind == "scripts":
        # 所有角色（包括管理员）都只能看到自己的话术。
        query = query.where(model.owner_id == user.id)
    # 规则通知不隔离：普通用户可读全量，写入仍限管理员。
    items = db.scalars(query.order_by(model.updated_at.desc())).all()
    return {"items": [serialize(item) for item in items]}


@router.post("/{kind}", status_code=201, dependencies=[Depends(csrf_protected)])
def create_item(kind: Literal["scripts", "rules"], payload: ContentRequest, background_tasks: BackgroundTasks, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_write_permission(kind, user)
    model = model_for(kind)
    item = model(**payload.model_dump(), owner_id=user.id)
    db.add(item)
    db.flush()
    db.add(AuditLog(action="创建", target_type="话术" if kind == "scripts" else "规则", target_name=item.title, operator_id=user.id))
    db.commit()
    db.refresh(item)
    # 写库成功后异步建/刷新向量索引（独立板块集合）。
    queue_content_index(background_tasks, kind, item.id)
    # 保存规则只落库，不再自动推送钉钉；推送请走 POST /rules/{id}/notify-dingtalk。
    return {"item": serialize(item)}


@router.put("/{kind}/{item_id}", dependencies=[Depends(csrf_protected)])
def update_item(kind: Literal["scripts", "rules"], item_id: str, payload: ContentRequest, background_tasks: BackgroundTasks, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = owned_item(kind, item_id, user, db)
    if not item:
        raise HTTPException(status_code=404, detail="内容不存在")
    require_write_permission(kind, user, item)
    for key, value in payload.model_dump().items():
        setattr(item, key, value)
    item.updated_at = datetime.now(timezone.utc)
    db.commit()
    # 内容变更后异步重建该条目的向量索引（旧 chunk 由索引服务按 index_chunk_ids 清理）。
    queue_content_index(background_tasks, kind, item_id)
    # 保存规则只落库，不再自动推送钉钉；推送请走 POST /rules/{id}/notify-dingtalk。
    return {"item": serialize(item)}


@router.delete("/{kind}/{item_id}", dependencies=[Depends(csrf_protected)])
def delete_item(kind: Literal["scripts", "rules"], item_id: str, background_tasks: BackgroundTasks, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = owned_item(kind, item_id, user, db)
    if not item:
        raise HTTPException(status_code=404, detail="内容不存在")
    require_write_permission(kind, user, item)
    item.deleted_at = datetime.now(timezone.utc)
    db.commit()
    # 逻辑删除后异步摘掉向量索引（含早期版本遗留在 doc collection 的残留 chunk）。
    background_tasks.add_task(delete_content_item, kind, item_id)
    return {"message": "已进入回收站"}


@router.post("/rules/{item_id}/notify-dingtalk", dependencies=[Depends(csrf_protected)])
async def notify_rule_dingtalk(
    item_id: str,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    item = db.get(RuleItem, item_id)
    if not item or item.deleted_at:
        raise HTTPException(status_code=404, detail="规则不存在")
    # 推送失败必须转成可读的 HTTP 错误：直接抛出原始异常会变成 500，
    # 前端只能显示"请求失败（HTTP 500）"，看不出是配置问题、签名问题还是网络问题。
    try:
        if not dingtalk_configured():
            raise HTTPException(status_code=503, detail="尚未配置钉钉机器人 Webhook，请先到「系统设置」里填写")
        await send_rule_notification(
            title=item.title,
            content=item.content,
            group_name=item.group_name,
            tags=item.tags,
            operator=user.username,
        )
    except HTTPException:
        raise
    except RuntimeError as exc:
        # 钉钉侧返回的失败：webhook 失效、access_token 过期、加签不匹配等
        raise HTTPException(status_code=502, detail=f"钉钉推送失败：{exc}") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("推送规则到钉钉失败：rule=%s", item_id)
        raise HTTPException(
            status_code=502,
            detail="钉钉推送失败：无法连接钉钉服务器或服务内部错误，请查看后端日志",
        ) from exc
    return {"message": "已推送到钉钉群"}
