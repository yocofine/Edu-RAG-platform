from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import admin_user, csrf_protected
from ..models import AuditLog, FAQItem, User
from ..schemas import FAQRequest
from ..services.faq_index import index_faq_item, remove_faq_item


router = APIRouter(prefix="/faqs", tags=["FAQ库"])


def normalize_question(value: str) -> str:
    return re.sub(r"[，。！？!?、,.;；：:\s]+", "", value.strip().lower())


def question_hash(value: str) -> str:
    return hashlib.sha256(normalize_question(value).encode("utf-8")).hexdigest()


def serialize(item: FAQItem) -> dict:
    return {
        "id": item.id,
        "question": item.question,
        "answer": item.answer,
        "category": item.category,
        "tags": item.tags,
        "owner": item.owner.username,
        "indexed": item.index_chunk_ids not in {"", "[]"},
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


@router.get("")
def list_faqs(
    q: str = Query(default="", max_length=200),
    _: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    query = select(FAQItem).where(FAQItem.deleted_at.is_(None))
    keyword = q.strip()
    if keyword:
        pattern = f"%{keyword}%"
        query = query.where(or_(
            FAQItem.question.ilike(pattern),
            FAQItem.answer.ilike(pattern),
            FAQItem.category.ilike(pattern),
            FAQItem.tags.ilike(pattern),
        ))
    items = db.scalars(query.order_by(FAQItem.updated_at.desc()).limit(1000)).all()
    return {"items": [serialize(item) for item in items]}


@router.post("", status_code=201, dependencies=[Depends(csrf_protected)])
def create_faq(
    payload: FAQRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    digest = question_hash(payload.question)
    existing = db.scalar(select(FAQItem).where(FAQItem.question_hash == digest))
    if existing and existing.deleted_at is None:
        raise HTTPException(status_code=409, detail="相同问题的 FAQ 已存在")
    if existing:
        item = existing
        item.deleted_at = None
        item.question = payload.question.strip()
        item.answer = payload.answer.strip()
        item.category = payload.category.strip() or "通用"
        item.tags = payload.tags.strip()
        item.owner_id = user.id
        item.updated_at = datetime.now(timezone.utc)
    else:
        item = FAQItem(
            question_hash=digest,
            question=payload.question.strip(),
            answer=payload.answer.strip(),
            category=payload.category.strip() or "通用",
            tags=payload.tags.strip(),
            owner_id=user.id,
        )
        db.add(item)
    db.flush()
    db.add(AuditLog(action="创建", target_type="FAQ", target_name=item.question[:255], operator_id=user.id))
    db.commit()
    db.refresh(item)
    background_tasks.add_task(index_faq_item, item.id)
    return {"item": serialize(item)}


@router.put("/{item_id}", dependencies=[Depends(csrf_protected)])
def update_faq(
    item_id: str,
    payload: FAQRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    item = db.get(FAQItem, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(status_code=404, detail="FAQ 不存在")
    digest = question_hash(payload.question)
    duplicate = db.scalar(select(FAQItem).where(
        FAQItem.question_hash == digest,
        FAQItem.id != item_id,
        FAQItem.deleted_at.is_(None),
    ))
    if duplicate:
        raise HTTPException(status_code=409, detail="相同问题的 FAQ 已存在")
    item.question_hash = digest
    item.question = payload.question.strip()
    item.answer = payload.answer.strip()
    item.category = payload.category.strip() or "通用"
    item.tags = payload.tags.strip()
    item.updated_at = datetime.now(timezone.utc)
    db.add(AuditLog(action="修改", target_type="FAQ", target_name=item.question[:255], operator_id=user.id))
    db.commit()
    background_tasks.add_task(index_faq_item, item.id)
    return {"item": serialize(item)}


@router.delete("/{item_id}", dependencies=[Depends(csrf_protected)])
def delete_faq(
    item_id: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
):
    item = db.get(FAQItem, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(status_code=404, detail="FAQ 不存在")
    item.deleted_at = datetime.now(timezone.utc)
    old_ids = item.index_chunk_ids
    item.index_chunk_ids = "[]"
    db.add(AuditLog(action="删除", target_type="FAQ", target_name=item.question[:255], operator_id=user.id))
    db.commit()
    background_tasks.add_task(remove_faq_item, old_ids)
    return {"message": "FAQ 已删除"}
