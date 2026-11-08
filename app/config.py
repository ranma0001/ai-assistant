"""All configuration comes from environment / .env. Nothing machine-specific is hardcoded."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    db_url: str = "sqlite:///data/assistant.db"
    log_dir: str = "logs"
    owner_psid: str = "console-user"
    channel: str = "console"  # console | messenger

    # Messenger (filled by the user, see docs/USER_TODO.md)
    fb_page_token: str = ""
    fb_app_secret: str = ""
    fb_verify_token: str = ""
    fb_page_id: str = ""
    fb_graph_version: str = "v21.0"

    # LLM (OpenAI-compatible)
    llm_base_url: str = ""
    llm_api_key: str = "ollama"
    llm_model: str = "qwen3:4b-instruct-2507"
    llm_report_model: str = "qwen3:8b"
    llm_timeout_s: float = 60.0
    parser: str = "auto"  # auto | llm | rule

    # Window / quiet hours
    window_limit_hours: float = 23
    warning_after_hours: float = 21
    quiet_start_hour: int = 23
    quiet_end_hour: int = 6
    quiet_shift_hour: int = 22
    warn_on_off_days: bool = True

    # Daily flow (HH:MM)
    briefing_time: str = "07:30"
    briefing_followup_first: str = "08:30"
    briefing_followup_last: str = "12:00"
    followup_interval_min: int = 60
    checkin_time: str = "16:30"
    checkin_followup_first: str = "17:30"
    checkin_followup_last: str = "22:30"
    late_briefing_cutoff: str = "16:30"
    meeting_lead_minutes: str = "30,10"
    reminder_grace_minutes: int = 120
    default_reminder_hour: int = 9
    default_meeting_minutes: int = 60
    # Optional read-only calendar feed(s): secret iCal/ICS URL(s), comma-separated. Empty = off.
    calendar_ics_urls: str = ""
    calendar_sync_minutes: int = 15
    calendar_horizon_days: int = 14
    seed_days_ahead: int = 3

    # Report
    report_dir: str = "data/reports"
    report_author: str = ""
    report_office: str = ""

    # Optional urgent e-mail fallback
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_to: str = ""

    def lead_minutes(self) -> list[int]:
        return [int(x) for x in self.meeting_lead_minutes.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
