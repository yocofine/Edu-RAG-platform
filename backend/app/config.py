from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "教辅知识库"
    app_env: str = "dev"
    database_url: str = "sqlite:///./edu_rag.db"
    jwt_secret: str = Field(default="change-me-in-production", min_length=16)
    access_token_minutes: int = 30
    refresh_token_days: int = 7
    cookie_secure: bool = False
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    storage_backend: str = "local"
    local_storage_path: str = str(ROOT / "storage")
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "edu-rag-documents"
    minio_secure: bool = False

    dashscope_api_key: str = ""
    llm_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        validation_alias="DASHSCOPE_BASE_URL",
    )
    llm_model: str = "qwen-plus"
    llm_intent_model: str = "qwen-plus"
    active_scenario_id: str = "education_kb"

    mineru_url: str = "http://mineru:8000"
    mineru_enabled: bool = False
    mineru_timeout_seconds: int = 1800
    # 用多模态模型给图片/流程图补文字说明，避免图内信息在检索里丢失
    image_analysis_enabled: bool = True
    image_analysis_model: str = ""
    image_analysis_max_images: int = 60
    image_analysis_concurrency: int = 4
    max_upload_mb: int = 100
    recent_upload_limit: int = 10
    recycle_days: int = 30

    dingtalk_robot_webhook: str = ""
    dingtalk_robot_secret: str = ""
    dingtalk_robot_timeout_seconds: float = 10.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
