import logging
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import admin_user, csrf_protected, current_user
from ..models import AuditLog, RuleItem, ScriptItem, User
from ..schemas import ContentRequest
from ..services.content_index import remove_business_content
from ..services.dingtalk import (
    dingtalk_configured,
    send_rule_notification,
    send_rule_notification_safely,
)


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
    if item is not None and user.role != "admin" and item.owner_id != user.id:
        raise HTTPException(status_code=403, detail="无权限修改")


def drop_legacy_chunks(background_tasks: BackgroundTasks, kind: str, item) -> None:
    """清理历史遗留的话术/规则向量片段。

    话术库和规则通知**不再写入向量库**：它们属于业务数据，只在业务库里维护、
    供前端浏览，不参与 RAG 知识检索。早期版本曾把它们索引进 doc collection，
    这里在内容变更时顺手把残留 chunk 摘掉，避免旧内容还能被检索到。
    """
    chunk_ids = item.index_chunk_ids
    if not chunk_ids or chunk_ids == "[]":
        return
    background_tasks.add_task(remove_business_content, kind, chunk_ids)
    item.index_chunk_ids = "[]"


def queue_rule_notification(background_tasks: BackgroundTasks, item: RuleItem, user: User) -> bool:
    if not dingtalk_configured():
        return False
    background_tasks.add_task(
        send_rule_notification_safely,
        title=item.title,
        content=item.content,
        group_name=item.group_name,
        tags=item.tags,
        operator=user.username,
    )
    return True


@router.get("/{kind}")
def list_items(kind: Literal["scripts", "rules"], user: User = Depends(current_user), db: Session = Depends(get_db)):
    model = model_for(kind)
    query = select(model).where(model.deleted_at.is_(None))
    if kind == "scripts" and user.role != "admin":
        # 话术库按用户隔离，规则如下：
        #   * 管理员创建的话术 = 平台公共话术，所有登录用户可见；
        #   * 普通用户创建的话术 = 私有，只有本人可见。
        # 这样普通用户既能用上管理员维护的内容，又不会互相看到对方的私有话术。
        # 若想改成「严格只看自己」，把下面这行换成 query.where(model.owner_id == user.id) 即可。
        admin_ids = select(User.id).where(User.role == "admin")
        query = query.where(or_(model.owner_id == user.id, model.owner_id.in_(admin_ids)))
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
    # 话术/规则不入向量库：它们只在业务库中维护与浏览。
    queued = queue_rule_notification(background_tasks, item, user) if kind == "rules" else False
    return {"item": serialize(item), "dingtalk_queued": queued}


@router.put("/{kind}/{item_id}", dependencies=[Depends(csrf_protected)])
def update_item(kind: Literal["scripts", "rules"], item_id: str, payload: ContentRequest, background_tasks: BackgroundTasks, user: User = Depends(current_user), db: Session = Depends(get_db)):
    model = model_for(kind)
    item = db.get(model, item_id)
    if not item or item.deleted_at:
        raise HTTPException(status_code=404, detail="内容不存在")
    require_write_permission(kind, user, item)
    for key, value in payload.model_dump().items():
        setattr(item, key, value)
    item.updated_at = datetime.now(timezone.utc)
    # 话术/规则不入向量库，并顺手清理历史残留 chunk。
    drop_legacy_chunks(background_tasks, kind, item)
    db.commit()
    queued = queue_rule_notification(background_tasks, item, user) if kind == "rules" else False
    return {"item": serialize(item), "dingtalk_queued": queued}


@router.delete("/{kind}/{item_id}", dependencies=[Depends(csrf_protected)])
def delete_item(kind: Literal["scripts", "rules"], item_id: str, background_tasks: BackgroundTasks, user: User = Depends(current_user), db: Session = Depends(get_db)):
    model = model_for(kind)
    item = db.get(model, item_id)
    if not item or item.deleted_at:
        raise HTTPException(status_code=404, detail="内容不存在")
    require_write_permission(kind, user, item)
    item.deleted_at = datetime.now(timezone.utc)
    # 删除时同样摘掉可能残留的向量片段（正常情况下应已为空）。
    drop_legacy_chunks(background_tasks, kind, item)
    db.commit()
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
