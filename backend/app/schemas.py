from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=80, pattern=r"^[\w\u4e00-\u9fff.-]+$")
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def bcrypt_length(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("密码UTF-8编码后不能超过72字节")
        return value


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def password_byte_length(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("密码UTF-8编码后不能超过72字节")
        return value


class SectionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class SectionOrderRequest(BaseModel):
    order: list[str] = Field(min_length=1)


class DocumentUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    section_id: str | None = None


class BatchDocumentRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)
    section_id: str | None = None


class AnalyzeRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20000)


class DocumentItem(BaseModel):
    id: str
    version_id: str
    name: str
    file_type: str
    version_no: int
    section_id: str
    section_name: str
    tags: str
    uploader: str
    uploaded_at: datetime
    ingestion_status: str
    preview_available: bool


class IntentFilters(BaseModel):
    section: str | None = None
    uploader: str | None = None
    latest: bool = False
    limit: int = Field(default=10, ge=1, le=50)


class IntentResult(BaseModel):
    intent: Literal[
        "FILE_SEARCH", "FILE_PREVIEW", "FILE_DOWNLOAD", "KNOWLEDGE_QUERY",
        "SUMMARY", "COMPARISON", "CHAT", "GREETING", "THANKS", "GOODBYE",
        "CAPABILITY", "OUT_OF_SCOPE"
    ]
    confidence: float = Field(ge=0, le=1)
    query: str
    filters: IntentFilters = Field(default_factory=IntentFilters)
    requires_history: bool = False
    token_usage: dict[str, int] = Field(default_factory=lambda: {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    })


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None


class ConversationCreate(BaseModel):
    title: str = Field(default="新会话", min_length=1, max_length=160)


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=160)


class ContentRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    content: str = ""
    group_name: str = "默认分组"
    tags: str = ""


class ParsedContentRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2_000_000)


class DingTalkSettingsRequest(BaseModel):
    enabled: bool = False
    webhook: str = Field(default="", max_length=2000)
    secret: str = Field(default="", max_length=1000)


class FAQRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    answer: str = Field(min_length=1, max_length=20_000)
    category: str = Field(default="通用", min_length=1, max_length=120)
    tags: str = Field(default="", max_length=1000)


class ApiMessage(BaseModel):
    message: str
    data: dict[str, Any] | None = None
