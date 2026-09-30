from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import csrf_protected, current_user
from ..models import AuditLog, ContentGroup, Role, RuleItem, ScriptItem, User
from ..schemas import ContentGroupRequest


router = APIRouter(prefix="/content-groups", tags=["话术与规则分组"])


def _model(kind: str):
    return ScriptItem if kind == "scripts" else RuleItem


def _check_write(kind: str, user: User, group: ContentGroup | None = None) -> None:
    if kind == "rules" and user.role != Role.admin.value:
        raise HTTPException(status_code=403, detail="规则分组仅允许管理员修改")
    if kind == "scripts" and group is not None and group.owner_id != user.id:
        raise HTTPException(status_code=403, detail="话术分组已按用户隔离，无权限修改他人分组")


def _visible_group_query(kind: str, user: User):
    query = select(ContentGroup).where(ContentGroup.kind == kind)
    if kind == "scripts":
        query = query.where(ContentGroup.owner_id == user.id)
    return query


def _named_group_query(kind: str, name: str, user: User):
    query = select(ContentGroup).where(ContentGroup.kind == kind, ContentGroup.name == name)
    if kind == "scripts":
        query = query.where(ContentGroup.owner_id == user.id)
    return query


def _duplicate_group_query(kind: str, name: str, user: User):
    query = select(ContentGroup).where(
        ContentGroup.kind == kind,
        func.lower(ContentGroup.name) == name.lower(),
    )
    if kind == "scripts":
        query = query.where(ContentGroup.owner_id == user.id)
    return query


@router.get("/{kind}")
def list_groups(
    kind: Literal["scripts", "rules"],
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    groups = db.scalars(_visible_group_query(kind, user).order_by(ContentGroup.sort_order, ContentGroup.created_at)).all()
    items = [
        {
            "id": group.name,
            "name": group.name,
            "owner": group.owner.username,
            "sort_order": group.sort_order,
        }
        for group in groups
    ]
    names = {item["name"].casefold() for item in items}

    model = _model(kind)
    content_query = select(model).where(model.deleted_at.is_(None))
    if kind == "scripts":
        content_query = content_query.where(model.owner_id == user.id)
    for item in db.scalars(content_query).all():
        name = (item.group_name or ("通知规则" if kind == "rules" else "默认分组")).strip()
        if name and name.casefold() not in names:
            items.append({"id": name, "name": name, "owner": item.owner.username, "sort_order": len(items)})
            names.add(name.casefold())
    return {"items": items}


@router.post("/{kind}", status_code=201, dependencies=[Depends(csrf_protected)])
def create_group(
    kind: Literal["scripts", "rules"],
    payload: ContentGroupRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _check_write(kind, user)
    name = payload.name.strip()
    duplicate = db.scalar(_duplicate_group_query(kind, name, user))
    if duplicate:
        raise HTTPException(status_code=409, detail="已存在同名分组")
    order_query = select(func.count()).select_from(ContentGroup).where(ContentGroup.kind == kind)
    if kind == "scripts":
        order_query = order_query.where(ContentGroup.owner_id == user.id)
    sort_order = db.scalar(order_query) or 0
    group = ContentGroup(kind=kind, name=name, owner_id=user.id, sort_order=sort_order)
    db.add(group)
    db.add(AuditLog(action="创建", target_type="规则分组" if kind == "rules" else "话术分组", target_name=name, operator_id=user.id))
    db.commit()
    return {"item": {"id": name, "name": name, "owner": user.username, "sort_order": sort_order}}


@router.put("/{kind}/{group_name}", dependencies=[Depends(csrf_protected)])
def rename_group(
    kind: Literal["scripts", "rules"],
    group_name: str,
    payload: ContentGroupRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    group = db.scalar(_named_group_query(kind, group_name, user))
    _check_write(kind, user, group)
    new_name = payload.name.strip()
    duplicate_query = _duplicate_group_query(kind, new_name, user).where(ContentGroup.name != group_name)
    duplicate = db.scalar(duplicate_query)
    if duplicate:
        raise HTTPException(status_code=409, detail="已存在同名分组")
    if group:
        group.name = new_name
        group.updated_at = datetime.now(timezone.utc)
    model = _model(kind)
    item_query = select(model).where(model.group_name == group_name, model.deleted_at.is_(None))
    if kind == "scripts":
        item_query = item_query.where(model.owner_id == user.id)
    for item in db.scalars(item_query).all():
        item.group_name = new_name
        item.updated_at = datetime.now(timezone.utc)
    db.add(AuditLog(action="重命名", target_type="规则分组" if kind == "rules" else "话术分组", target_name=new_name, operator_id=user.id))
    db.commit()
    return {"item": {"id": new_name, "name": new_name}}


@router.delete("/{kind}/{group_name}", dependencies=[Depends(csrf_protected)])
def delete_group(
    kind: Literal["scripts", "rules"],
    group_name: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    group = db.scalar(_named_group_query(kind, group_name, user))
    _check_write(kind, user, group)
    now = datetime.now(timezone.utc)
    model = _model(kind)
    item_query = select(model).where(model.group_name == group_name, model.deleted_at.is_(None))
    if kind == "scripts":
        item_query = item_query.where(model.owner_id == user.id)
    for item in db.scalars(item_query).all():
        item.deleted_at = now
    if group:
        db.delete(group)
    db.add(AuditLog(action="删除", target_type="规则分组" if kind == "rules" else "话术分组", target_name=group_name, operator_id=user.id))
    db.commit()
    return {"message": "分组已删除"}
