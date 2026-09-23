import re
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import csrf_protected, current_user
from ..models import ChatMessage, User
from ..schemas import AnalyzeRequest


router = APIRouter(prefix="/ai", tags=["AI辅助"])
settings = get_settings()


IGNORED_QUESTIONS = {
    "你好", "您好", "nihao", "hello", "hi", "早上好", "晚上好",
    "谢谢", "感谢", "再见", "拜拜", "你是谁", "你好你是谁",
}


def normalize_question(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip().lower())
    text = re.sub(r"^[请麻烦]*(?:帮我|给我)?", "", text)
    text = re.sub(r"[，。！？!?、,.;；：:\s]+", "", text)
    return text


@router.get("/keyword-cloud")
def keyword_cloud(_: User = Depends(current_user), db: Session = Depends(get_db)):
    questions = db.scalars(
        select(ChatMessage.content)
        .where(ChatMessage.role == "user")
        .order_by(ChatMessage.created_at.desc())
        .limit(5000)
    ).all()
    counts: Counter[str] = Counter()
    representatives: dict[str, str] = {}
    for question in questions:
        normalized = normalize_question(question)
        if len(normalized) < 2 or normalized in IGNORED_QUESTIONS:
            continue
        counts[normalized] += 1
        representatives.setdefault(normalized, re.sub(r"\s+", " ", str(question).strip())[:48])
    items = [
        {"word": representatives[key], "weight": count, "count": count, "kind": "question"}
        for key, count in counts.most_common(30)
    ]
    return {
        "items": items,
        "total_questions": sum(counts.values()),
        "unique_questions": len(counts),
    }


@router.post("/analyze", dependencies=[Depends(csrf_protected)])
async def analyze_content(payload: AnalyzeRequest, _: User = Depends(current_user)):
    if not settings.dashscope_api_key:
        raise HTTPException(status_code=503, detail="尚未配置大模型API Key")
    client = AsyncOpenAI(api_key=settings.dashscope_api_key, base_url=settings.llm_base_url)
    try:
        response = await client.chat.completions.create(
            model=settings.llm_model, temperature=0.2, max_tokens=1600,
            messages=[
                {"role": "system", "content": "你是教辅内容编辑助手。请检查输入内容的清晰度、完整性和潜在歧义，输出简洁的修改建议，不要编造事实。"},
                {"role": "user", "content": payload.content},
            ],
        )
        return {"analysis": (response.choices[0].message.content or "暂无建议").strip()}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"AI分析失败：{exc}") from exc
