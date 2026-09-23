from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..deps import current_user
from ..models import ChatMessage, ChatSession, User
from ..rate_limit import limit_chat
from ..schemas import ChatRequest
from ..security import decode_token
from ..services.intent import intent_classifier
from .documents import search_documents, serialize


router = APIRouter(prefix="/chat", tags=["智能搜索"])

FIXED_INTENT_REPLIES = {
    "GREETING": "您好，我是教辅知识库智能助手。您可以让我查找文件、解答教辅知识问题、总结资料或比较不同文档。",
    "THANKS": "不客气，很高兴能帮到您。",
    "GOODBYE": "再见！需要查找教辅资料时，随时来找我。",
    "CAPABILITY": "我可以帮您查找和预览教辅文件、回答知识库中的问题、总结资料、比较文档，并提供答案来源。",
    "OUT_OF_SCOPE": "这个问题超出了教辅知识库助手的服务范围。我可以继续帮您查找教辅文件，或解答与教辅资料相关的问题。",
}


def _conversation(session_id: str | None, user: User, db: Session) -> ChatSession:
    session = db.get(ChatSession, session_id) if session_id else None
    if session and (session.user_id != user.id or session.deleted_at):
        raise HTTPException(status_code=404, detail="会话不存在")
    if session is None:
        session = ChatSession(user_id=user.id, title="新会话")
        db.add(session); db.flush()
    return session


def _append_message(db: Session, session: ChatSession, role: str, content: str, payload: dict | None = None) -> None:
    db.add(ChatMessage(
        session_id=session.id, role=role, content=content,
        payload_json=json.dumps(payload or {}, ensure_ascii=False, default=str),
    ))
    session.updated_at = datetime.now(timezone.utc)


def _rag_intent(intent) -> dict:
    mapped = "FOLLOW_UP" if intent.requires_history else "KNOWLEDGE_QUERY"
    return {
        "intent": mapped,
        "confidence": intent.confidence,
        "reason": f"llm_router:{intent.intent.lower()}",
        "requires_rewrite": intent.requires_history,
        "suggested_source": None,
    }


async def _file_search(intent, user: User, db: Session) -> list[dict]:
    # 对泛化的“最新上传”请求按时间排序即可，不要把路由器返回的
    # “最新上传的文件”当作文件名/标签关键词过滤。
    latest_phrases = {
        "最新文件", "最近文件", "最新上传", "最近上传", "最新上传文件",
        "最近上传文件", "最新上传的文件", "最近上传的文件", "刚上传的文件",
    }
    raw_query = (intent.query or "").strip()
    normalized_query = "".join(
        char for char in raw_query.lower()
        if not char.isspace() and char not in "，。！？!?、,.;；：:~～"
    )
    query = _clean_file_query(raw_query)
    if intent.filters.latest and (
        normalized_query in latest_phrases
        or any(phrase in normalized_query for phrase in ("最新上传的文件", "最近上传的文件", "最新上传文件", "最近上传文件"))
    ):
        query = ""
    exact = search_documents(
        query=query, section=intent.filters.section,
        uploader=intent.filters.uploader, latest=True,
        limit=intent.filters.limit, user=user, db=db,
    )["items"]
    if exact:
        return exact
    try:
        from qa_core.application.factory import get_qa_service
        debug = await asyncio.to_thread(
            get_qa_service().debug_retrieval,
            query, None, None, None, "education_kb",
            None, None, "internal", user.role, [user.role],
        )
        version_ids: list[str] = []

        def collect(value):
            if isinstance(value, dict):
                if value.get("document_version_id"):
                    version_ids.append(str(value["document_version_id"]))
                for nested in value.values():
                    collect(nested)
            elif isinstance(value, list):
                for nested in value:
                    collect(nested)

        collect(debug.get("doc_sources", []))
        items = []
        from ..models import DocumentVersion, JobStatus
        for version_id in dict.fromkeys(version_ids):
            version = db.get(DocumentVersion, version_id)
            if not version or version.ingestion_status != JobStatus.published.value:
                continue
            document = version.document
            if document.deleted_at or document.current_version_id != version.id:
                continue
            items.append(serialize(document, version))
        return sorted(items, key=lambda item: item["uploaded_at"], reverse=True)[:intent.filters.limit]
    except Exception:
        return []


def _stream_event(event_type: str, **data) -> str:
    return json.dumps({"type": event_type, **data}, ensure_ascii=False, default=str) + "\n"


def _merge_token_usage(total: dict, usage: dict | None) -> dict:
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        total[key] = int(total.get(key) or 0) + int((usage or {}).get(key) or 0)
    return total


def _estimated_token_usage(query: str, answer: str) -> dict:
    """仅在模型供应商未返回 usage 时提供明确标记的近似值。"""
    input_tokens = max(1, round(len((query or "").encode("utf-8")) / 4))
    output_tokens = max(1, round(len((answer or "").encode("utf-8")) / 4))
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "estimated": True,
    }


def _normalize_citations(sources: list[dict] | None) -> list[dict]:
    """把核心检索来源压平成前端可预览的文件、版本和页码结构。"""
    items: list[dict] = []
    seen: set[tuple] = set()
    for source in sources or []:
        metadata = dict(source.get("metadata") or {})
        item = {
            **source,
            "document_id": source.get("document_id") or metadata.get("document_id"),
            "version_id": source.get("version_id") or metadata.get("document_version_id"),
            "file_name": source.get("file_name") or metadata.get("file_name") or metadata.get("source"),
            "page_number": source.get("page_number") or metadata.get("page_number"),
        }
        key = (item.get("document_id"), item.get("version_id"), item.get("page_number"), item.get("file_name"))
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
    return items


def _clean_file_query(value: str) -> str:
    """去掉文件检索中的口语前后缀，避免“的文件”导致精确匹配失败。"""
    cleaned = (value or "").strip()
    if not cleaned:
        return ""
    cleaned = re.sub(r"^(?:请|麻烦)?(?:帮我)?(?:找|查找|查询|搜索)(?:一下|下)?", "", cleaned)
    cleaned = re.sub(r"(?:的)?(?:相关)?(?:文件|资料)$", "", cleaned)
    return cleaned.strip(" \t，。！？!?、,.;；：:")


@router.post("/stream", dependencies=[Depends(limit_chat)])
async def chat_stream(payload: ChatRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """统一流式对话端点，所有回答类型均使用 NDJSON 事件输出。"""
    request_started = time.perf_counter()
    session = _conversation(payload.session_id, user, db)
    history = "\n".join(f"{message.role}: {message.content[:240]}" for message in session.messages[-6:])
    _append_message(db, session, "user", payload.query)
    if session.title == "新会话":
        session.title = payload.query.strip()[:36]
    db.commit()

    async def generate():
        answer_parts: list[str] = []
        final_answer: str | None = None
        citations: list[dict] = []
        file_items: list[dict] | None = None
        token_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        try:
            yield _stream_event("session", session_id=session.id)
            yield _stream_event("status", message="正在识别意图")
            intent = await intent_classifier.classify(payload.query, history)
            _merge_token_usage(token_usage, intent.token_usage)
            yield _stream_event("intent", intent=intent.model_dump())

            if intent.intent in FIXED_INTENT_REPLIES:
                answer = FIXED_INTENT_REPLIES[intent.intent]
                for index in range(0, len(answer), 3):
                    token = answer[index:index + 3]
                    answer_parts.append(token)
                    yield _stream_event("token", content=token)
                    await asyncio.sleep(.018)
            elif intent.intent == "CHAT":
                async for part in intent_classifier.stream_chat(payload.query, history):
                    token = str(part.get("content") or "")
                    if token:
                        answer_parts.append(token)
                        yield _stream_event("token", content=token)
                    if part.get("usage"):
                        _merge_token_usage(token_usage, part["usage"])
            elif intent.intent in {"FILE_SEARCH", "FILE_PREVIEW", "FILE_DOWNLOAD"}:
                yield _stream_event("status", message="正在查找文件")
                file_items = await _file_search(intent, user, db)
                answer = f"找到 {len(file_items)} 个相关文件。" if file_items else "没有找到符合条件的文件。"
                for index in range(0, len(answer), 3):
                    token = answer[index:index + 3]
                    answer_parts.append(token)
                    yield _stream_event("token", content=token)
                yield _stream_event("files", items=file_items)
            else:
                yield _stream_event("status", message="正在检索知识库")
                from qa_core.application.factory import get_qa_service
                iterator = iter(get_qa_service().stream_query(
                    payload.query, None, session.id,
                    scenario_id="education_kb", visibility="internal",
                    user_role=user.role, user_roles=[user.role],
                    intent_override=_rag_intent(intent),
                ))
                while True:
                    event = await asyncio.to_thread(next, iterator, None)
                    if event is None:
                        break
                    if event.get("type") == "token":
                        token = str(event.get("content") or event.get("token") or "")
                        if token:
                            answer_parts.append(token)
                            yield _stream_event("token", content=token)
                    elif event.get("type") == "end":
                        citations = _normalize_citations(event.get("sources") or event.get("citations") or [])
                        _merge_token_usage(token_usage, event.get("token_usage"))
                        final_answer = event.get("answer") or final_answer
                yield _stream_event("citations", items=citations)

            answer = final_answer or "".join(answer_parts)
            if token_usage["total_tokens"] == 0 and intent.intent in {"CHAT", "KNOWLEDGE_QUERY", "SUMMARY", "COMPARISON"}:
                token_usage = _estimated_token_usage(payload.query, answer)
            processing_time = round(time.perf_counter() - request_started, 3)
            metrics = {"processing_time": processing_time, "token_usage": token_usage}
            payload_data = {"type": "answer", "citations": citations, "metrics": metrics}
            if file_items is not None:
                payload_data = {"type": "file_results", "items": file_items, "metrics": metrics}
            _append_message(db, session, "assistant", answer, payload_data)
            db.commit()
            yield _stream_event(
                "end", session_id=session.id, answer=answer,
                processing_time=processing_time, token_usage=token_usage,
            )
        except Exception as exc:
            message = f"服务暂不可用：{exc}"
            _append_message(db, session, "assistant", message, {"type": "error"})
            db.commit()
            yield _stream_event("error", message=message)

    return StreamingResponse(
        generate(), media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.post("", dependencies=[Depends(limit_chat)])
async def chat(payload: ChatRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    session = _conversation(payload.session_id, user, db)
    history = "\n".join(f"{message.role}: {message.content[:240]}" for message in session.messages[-6:])
    _append_message(db, session, "user", payload.query)
    if session.title == "新会话":
        session.title = payload.query.strip()[:36]
    db.commit()
    try:
        intent = await intent_classifier.classify(payload.query, history)
    except RuntimeError as exc:
        _append_message(db, session, "assistant", str(exc), {"type": "error"}); db.commit()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if intent.intent in FIXED_INTENT_REPLIES:
        answer = FIXED_INTENT_REPLIES[intent.intent]
        response = {
            "type": "answer", "intent": intent.model_dump(), "answer": answer,
            "citations": [], "session_id": session.id,
        }
        _append_message(db, session, "assistant", answer, {"type": "answer", "citations": []}); db.commit()
        return response
    if intent.intent == "CHAT":
        try:
            answer = await intent_classifier.reply_chat(payload.query, history)
        except Exception as exc:
            _append_message(db, session, "assistant", f"对话服务暂不可用：{exc}", {"type": "error"}); db.commit()
            raise HTTPException(status_code=503, detail=f"对话服务暂不可用：{exc}") from exc
        response = {
            "type": "answer", "intent": intent.model_dump(), "answer": answer,
            "citations": [], "session_id": session.id,
        }
        _append_message(db, session, "assistant", answer, {"type": "answer", "citations": []}); db.commit()
        return response
    if intent.intent in {"FILE_SEARCH", "FILE_PREVIEW", "FILE_DOWNLOAD"}:
        items = await _file_search(intent, user, db)
        content = f"找到 {len(items)} 个相关文件。" if items else "没有找到符合条件的文件。"
        _append_message(db, session, "assistant", content, {"type": "file_results", "items": items}); db.commit()
        return {"type": "file_results", "intent": intent.model_dump(), "items": items, "answer": content, "session_id": session.id}
    try:
        from qa_core.application.factory import get_qa_service
        events = await asyncio.to_thread(
            lambda: list(get_qa_service().stream_query(
                payload.query, None, payload.session_id or str(uuid4()),
                scenario_id="education_kb", visibility="internal",
                user_role=user.role, user_roles=[user.role],
                intent_override=_rag_intent(intent),
            ))
        )
    except Exception as exc:
        _append_message(db, session, "assistant", f"RAG服务暂不可用：{exc}", {"type": "error"}); db.commit()
        raise HTTPException(status_code=503, detail=f"RAG服务暂不可用：{exc}") from exc
    answer = "".join(
        str(event.get("content") or event.get("token") or "")
        for event in events if event.get("type") == "token"
    )
    end = next((event for event in reversed(events) if event.get("type") == "end"), {})
    answer = end.get("answer") or answer
    response = {
        "type": "answer", "intent": intent.model_dump(), "answer": answer,
        "citations": end.get("sources") or end.get("citations") or [],
        "session_id": session.id,
    }
    _append_message(db, session, "assistant", answer, {"type": "answer", "citations": response["citations"]}); db.commit()
    return response


@router.websocket("/ws")
async def chat_ws(websocket: WebSocket):
    try:
        token = decode_token(websocket.cookies.get("access_token") or "", "access")
    except Exception:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    try:
        while True:
            message = await websocket.receive_json()
            query = str(message.get("query") or "").strip()
            if not query:
                await websocket.send_json({"type": "error", "error": "查询内容不能为空"})
                continue
            with SessionLocal() as db:
                user = db.get(User, int(token["sub"]))
                if not user or user.disabled:
                    await websocket.send_json({"type": "error", "error": "账号不可用"})
                    continue
                intent = await intent_classifier.classify(query, str(message.get("history_summary") or ""))
                await websocket.send_json({"type": "intent", "intent": intent.model_dump()})
                if intent.intent in FIXED_INTENT_REPLIES:
                    await websocket.send_json({"type": "token", "content": FIXED_INTENT_REPLIES[intent.intent]})
                    await websocket.send_json({"type": "end", "sources": []})
                    continue
                if intent.intent == "CHAT":
                    answer = await intent_classifier.reply_chat(query, str(message.get("history_summary") or ""))
                    await websocket.send_json({"type": "token", "content": answer})
                    await websocket.send_json({"type": "end", "sources": []})
                    continue
                if intent.intent in {"FILE_SEARCH", "FILE_PREVIEW", "FILE_DOWNLOAD"}:
                    await websocket.send_json({"type": "file_results", "items": await _file_search(intent, user, db)})
                    await websocket.send_json({"type": "end"})
                    continue
                role = user.role
            from qa_core.application.factory import get_qa_service
            iterator = iter(get_qa_service().stream_query(
                query, None, message.get("session_id") or str(uuid4()),
                scenario_id="education_kb", visibility="internal",
                user_role=role, user_roles=[role],
                intent_override=_rag_intent(intent),
            ))
            while True:
                event = await asyncio.to_thread(next, iterator, None)
                if event is None:
                    break
                await websocket.send_json(event)
    except WebSocketDisconnect:
        return
    except Exception as exc:
        try:
            await websocket.send_json({"type": "error", "error": str(exc)})
        except Exception:
            return
