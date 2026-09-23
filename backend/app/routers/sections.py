from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import admin_user, csrf_protected, current_user
from ..models import AuditLog, Document, Section, User
from ..schemas import SectionOrderRequest, SectionRequest


router = APIRouter(prefix="/sections", tags=["板块"])


@router.get("")
def list_sections(_: User = Depends(current_user), db: Session = Depends(get_db)):
    return {"items": db.scalars(select(Section).order_by(Section.sort_order, Section.created_at)).all()}


@router.post("", status_code=201, dependencies=[Depends(csrf_protected)])
def create_section(payload: SectionRequest, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    order = db.scalar(select(func.max(Section.sort_order)))
    section = Section(name=payload.name.strip(), sort_order=(order if order is not None else -1) + 1)
    db.add(section)
    db.flush()
    db.add(AuditLog(action="创建", target_type="板块", target_name=section.name, operator_id=user.id))
    db.commit()
    db.refresh(section)
    return {"item": section}


@router.put("/{section_id}", dependencies=[Depends(csrf_protected)])
def rename_section(section_id: str, payload: SectionRequest, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    section = db.get(Section, section_id)
    if not section:
        raise HTTPException(status_code=404, detail="板块不存在")
    section.name = payload.name.strip()
    db.add(AuditLog(action="重命名", target_type="板块", target_name=section.name, operator_id=user.id))
    db.commit()
    return {"item": section}


@router.put("/order/reorder", dependencies=[Depends(csrf_protected)])
def reorder_sections(payload: SectionOrderRequest, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    sections = {item.id: item for item in db.scalars(select(Section)).all()}
    if set(payload.order) != set(sections):
        raise HTTPException(status_code=400, detail="排序列表必须包含全部板块")
    for index, section_id in enumerate(payload.order):
        sections[section_id].sort_order = index
    db.add(AuditLog(action="排序", target_type="板块", target_name="全部板块", operator_id=user.id))
    db.commit()
    return {"message": "板块顺序已更新"}


@router.delete("/{section_id}", dependencies=[Depends(csrf_protected)])
def delete_section(section_id: str, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    section = db.get(Section, section_id)
    if not section:
        raise HTTPException(status_code=404, detail="板块不存在")
    count = db.scalar(select(func.count()).select_from(Document).where(Document.section_id == section_id, Document.deleted_at.is_(None))) or 0
    if count:
        raise HTTPException(status_code=409, detail="板块中仍有文件，请先移动或删除文件")
    db.delete(section)
    db.add(AuditLog(action="删除", target_type="板块", target_name=section.name, operator_id=user.id))
    db.commit()
    return {"message": "板块已删除"}
