from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_name: str = "劳动文书助手"
    api_prefix: str = "/api"
    frontend_origin: str = "http://localhost:3000"
    database_url: str = "sqlite:///./labor_docs.db"
    session_secret: str = "change-me-before-production"
    admin_key: str = "change-me-admin"
    local_storage_dir: str = "./storage"
    storage_backend: str = "local"
    s3_endpoint_url: str | None = None
    s3_bucket: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "cn-east-1"
    max_file_bytes: int = 50 * 1024 * 1024
    max_case_files: int = 20
    max_case_bytes: int = 300 * 1024 * 1024
    session_max_age_seconds: int = 30 * 24 * 60 * 60
    openai_base_url: str | None = None
    openai_api_key: str | None = None
    openai_model: str | None = None
    yuandian_endpoint: str | None = None
    yuandian_token: str | None = None
    yuandian_law_mcp_url: str = "https://open.chineselaw.com/mcp/law/stream"
    yuandian_case_mcp_url: str = "https://open.chineselaw.com/mcp/case/stream"
    yuandian_company_mcp_url: str = "https://open.chineselaw.com/mcp/company/stream"
    generator_internal_token: str | None = None
    worker_internal_url: str | None = None
    worker_internal_token: str | None = None
    redis_url: str = "redis://localhost:6379/0"
    soffice_path: str | None = None
    antivirus_required: bool = False
    brevo_api_key: str | None = None
    brevo_sender_email: str | None = None
    brevo_sender_name: str = "劳动文书助手"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def storage_path(self) -> Path:
        return Path(self.local_storage_dir).resolve()


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.app_env not in {"development", "test"}:
        if len(settings.session_secret) < 32 or settings.session_secret.startswith("change-me"):
            raise RuntimeError("非开发环境必须配置至少32位随机 SESSION_SECRET")
        if len(settings.admin_key) < 16 or settings.admin_key.startswith("change-me"):
            raise RuntimeError("非开发环境必须配置随机 ADMIN_KEY")
    return settings
