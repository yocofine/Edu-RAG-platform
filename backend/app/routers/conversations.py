from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import csrf_protected, current_user
from ..models import ChatSession, User
from ..schemas import ConversationCreate, ConversationRename


router = APIRouter(prefix="/conversations", tags=["历史会话"])


def serialize_session(item: ChatSession) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "message_count": len(item.messages),
    }


def owned_session(session_id: str, user: User, db: Session) -> ChatSession:
    item = db.get(ChatSession, session_id)
    if not item or item.user_id != user.id or item.deleted_at:
        raise HTTPException(status_code=404, detail="会话不存在")
    return item


@router.get("")
def list_conversations(user: User = Depends(current_user), db: Session = Depends(get_db)):
    items = db.scalars(
        select(ChatSession).where(
            ChatSession.user_id == user.id, ChatSession.deleted_at.is_(None)
        ).order_by(ChatSession.updated_at.desc())
    ).all()
    return {"items": [serialize_session(item) for item in items]}


@router.post("", status_code=201, dependencies=[Depends(csrf_protected)])
def create_conversation(payload: ConversationCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = ChatSession(user_id=user.id, title=payload.title.strip())
    db.add(item); db.commit(); db.refresh(item)
    return {"item": serialize_session(item)}


@router.get("/{session_id}/messages")
def conversation_messages(session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = owned_session(session_id, user, db)
    return {"session": serialize_session(item), "items": [{
        "id": message.id, "role": message.role, "content": message.content,
        "payload": message.payload_json, "created_at": message.created_at,
    } for message in item.messages]}


@router.put("/{session_id}", dependencies=[Depends(csrf_protected)])
def rename_conversation(session_id: str, payload: ConversationRename, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = owned_session(session_id, user, db)
    item.title = payload.title.strip(); item.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"item": serialize_session(item)}


@router.delete("/{session_id}", dependencies=[Depends(csrf_protected)])
def delete_conversation(session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = owned_session(session_id, user, db)
    item.deleted_at = datetime.now(timezone.utc); db.commit()
    return {"message": "会话已删除"}


@router.post("/{session_id}/restore", dependencies=[Depends(csrf_protected)])
def restore_conversation(session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    item = db.get(ChatSession, session_id)
    if not item or item.user_id != user.id or not item.deleted_at:
        raise HTTPException(status_code=404, detail="已删除会话不存在")
    item.deleted_at = None; item.updated_at = datetime.now(timezone.utc); db.commit()
    return {"item": serialize_session(item)}
