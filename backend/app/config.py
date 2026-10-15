"""Application settings, loaded from environment variables and backend/.env."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Tests (APP_ENV=test) ignore backend/.env so a developer's local values cannot leak in.
        env_file=None if os.getenv("APP_ENV") == "test" else BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # General
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    log_json: bool = True

    # Database (SQLite for the prototype, Postgres later - only this URL changes)
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'data' / 'app.db').as_posix()}"

    # Auth
    jwt_secret: str = "change-me-in-env"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 12

    # Meta (webhooks). The app secret signs every webhook body (X-Hub-Signature-256).
    meta_app_secret: str = "change-me-in-env"
    meta_verify_token: str = "change-me-in-env"
    # Set false only for local experiments; never in production.
    meta_verify_signatures: bool = True

    # Background worker that processes stored webhook events
    worker_enabled: bool = True
    worker_poll_seconds: float = 2.0
    worker_max_attempts: int = 5
    worker_backoff_base_seconds: float = 5.0

    # LLM
    llm_provider: Literal["ollama", "fake"] = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    ollama_timeout_seconds: float = 60.0

    # Embeddings for product search (RAG)
    # "hashing" needs no model (works out of the box, weaker on meaning);
    # "ollama" uses a local embedding model, chosen with scripts/eval_retrieval.py
    embedding_provider: Literal["ollama", "hashing"] = "hashing"
    embedding_model: str = "bge-m3"
    rag_top_k: int = 3
    rag_min_score: float = 0.12  # below this a product is not considered relevant

    # Product catalog
    catalog_path: str = str(BACKEND_DIR / "data" / "catalog.sample.json")

    # Business details used in replies
    business_name: str = "our store"
    reply_max_words: int = 90

    # Meta Graph API / WhatsApp Cloud API. "fake" records sends locally instead
    # of calling Meta (sandbox, tests, and before the Meta app is approved).
    meta_api_mode: Literal["live", "fake"] = "fake"
    meta_graph_version: str = "v25.0"
    meta_graph_base_url: str = "https://graph.facebook.com"
    meta_page_access_token: str = ""
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_business_account_id: str = ""  # WABA ID: lets the dashboard list approved templates
    meta_api_timeout_seconds: float = 20.0

    # Lead form (Instant Form) field names - must match the form built in Ads Manager
    leadform_optin_field: str = "whatsapp_opt_in"
    leadform_product_field: str = "product_interest"
    leadform_optin_wording: str = (
        "Yes, the business may contact me on WhatsApp at this number about my enquiry "
        "and related offers. I can reply STOP at any time."
    )
    default_country_code: str = "91"

    # WhatsApp templates (must be approved in WhatsApp Manager before live use)
    template_language: str = "en"
    template_lead_form_welcome: str = "lead_form_welcome"

    # Estimated WhatsApp cost per delivered message, INR (India, Oct 2026).
    # VERIFY against Meta's current rate card before budgeting - see docs/meta_setup.md.
    # Since 1 Oct 2026 free-form (service) replies are billed at the utility rate.
    wa_rate_marketing_inr: float = 0.8631
    wa_rate_utility_inr: float = 0.12
    wa_rate_service_inr: float = 0.12

    # Outbound message delivery
    outbox_max_attempts: int = 3
    outbox_backoff_seconds: float = 10.0

    # ---- Week 3: Instagram / Messenger ----
    meta_page_id: str = ""  # Facebook Page ID (sends for Messenger, and Instagram via Facebook Login)
    instagram_account_id: str = ""  # Instagram professional account ID (to ignore our own comments)
    # Instagram API host: graph.facebook.com (Instagram via Facebook Login, default)
    # or graph.instagram.com (Instagram API with Instagram Login - then INSTAGRAM_ACCESS_TOKEN is used)
    instagram_api_base_url: str = "https://graph.facebook.com"
    instagram_access_token: str = ""  # empty = use META_PAGE_ACCESS_TOKEN
    comment_public_reply: bool = True  # also post a short public reply ("Sent you a message!")
    comment_public_reply_text: str = "Thanks! We've sent you a message 😊"
    comment_hide_spam: bool = True
    private_reply_window_days: int = 7

    # ---- Week 3: alerts to the salesperson ----
    # "log" = write alerts to the log and database only; "live" = really send
    alerts_mode: Literal["log", "live"] = "log"
    alert_channels: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["email", "telegram"])
    dashboard_url: str = "http://127.0.0.1:8000"  # links in alerts point here (/leads/<id>)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    alert_email_from: str = ""
    alert_email_to: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_api_base_url: str = "https://api.telegram.org"

    # ---- Week 4: dashboard ----
    # Built React app (npm run build in /frontend). Served at / when the folder exists.
    frontend_dist: str = str(BACKEND_DIR.parent / "frontend" / "dist")

    # ---- Week 3: Google Sheets mirror ----
    # "off", "csv" (writes data/leads_mirror.csv - opens in Excel), or "google"
    sheets_mode: Literal["off", "csv", "google"] = "csv"
    sheets_sync_seconds: float = 60.0
    sheets_csv_path: str = str(BACKEND_DIR / "data" / "leads_mirror.csv")
    google_service_account_file: str = ""  # JSON key of a service account with access to the sheet
    google_sheet_id: str = ""
    google_sheet_tab: str = "Leads"

    # Tenancy: the prototype serves one business. Leave empty to use the first
    # user in the database; set it to pin events to a specific user_id.
    default_user_id: str = ""

    extra_cors_origins: list[str] = Field(default_factory=list)

    # Relative paths in .env are relative to the backend folder, not to wherever
    # the command was started from.
    @field_validator("database_url")
    @classmethod
    def _absolute_sqlite_path(cls, v: str) -> str:
        prefix = "sqlite:///"
        if v.startswith(prefix):
            path = v[len(prefix) :]
            if path and path != ":memory:" and not Path(path).is_absolute():
                return prefix + (BACKEND_DIR / path).resolve().as_posix()
        return v

    @field_validator("catalog_path", "sheets_csv_path", "google_service_account_file", "frontend_dist")
    @classmethod
    def _absolute_file_path(cls, v: str) -> str:
        if not v:
            return v
        return v if Path(v).is_absolute() else str((BACKEND_DIR / v).resolve())

    @field_validator("alert_channels", mode="before")
    @classmethod
    def _split_channels(cls, v: object) -> object:
        # Allow ALERT_CHANNELS=email,telegram in .env as well as JSON lists.
        if isinstance(v, str):
            text = v.strip()
            if text.startswith("["):
                return json.loads(text)
            return [c.strip() for c in text.split(",") if c.strip()]
        return v

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
