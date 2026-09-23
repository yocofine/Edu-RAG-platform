import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, update

from .config import get_settings
from .db import Base, SessionLocal, engine
from .events import event_hub
from .models import IngestionJob, JobStatus, Section
from .routers import admin, ai, auth, chat, content, conversations, documents, faqs, sections
from .security import decode_token


settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        terminal = [
            JobStatus.published.value, JobStatus.failed.value,
            JobStatus.needs_review.value, JobStatus.interrupted.value,
        ]
        db.execute(
            update(IngestionJob).where(IngestionJob.status.not_in(terminal)).values(
                status=JobStatus.interrupted.value,
                error_message="服务重启导致任务中断，请手动重试",
            )
        )
        if not db.scalar(select(Section).limit(1)):
            for order, name in enumerate(["📚 教案课件", "📝 教辅资料", "📄 其他资料"]):
                db.add(Section(name=name, sort_order=order))
        db.commit()
    # 启动时预热检索栈：BGE embedding、CrossEncoder reranker、Milvus 集合。
    # 把模型加载和首次集合初始化的冷启动开销从第一个用户请求挪到启动阶段；
    # 预热失败不阻断启动，只记录告警并按需懒加载。
    try:
        from qa_core.retrieval.factory import warmup_retrieval_stack

        warmup_retrieval_stack()
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("检索栈预热失败，将按需懒加载：%s", exc)
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注意注册顺序：FastAPI 按注册顺序匹配路由。
# content.router 的 `/{kind}` 是通配路由，会抢先匹配任何单段路径
# （例如 /faqs 会被当成 kind="faqs"，直接报 422 而不是进 FAQ 路由），
# 因此它必须放在最后。新增路由请一律加在 content.router 之前。
for api_router in (
    auth.router,
    sections.router,
    documents.router,
    conversations.router,
    chat.router,
    ai.router,
    admin.router,
    faqs.router,
    content.router,
):
    app.include_router(api_router, prefix="/api/v1")


@app.get("/health")
def health():
    return {"status": "ok", "service": "edu-rag-api"}


@app.websocket("/api/v1/events/ws")
async def event_stream(websocket: WebSocket):
    try:
        payload = decode_token(websocket.cookies.get("access_token") or "", "access")
    except Exception:
        await websocket.close(code=4401)
        return
    user_id = int(payload["sub"])
    await event_hub.connect(user_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await event_hub.disconnect(user_id, websocket)
