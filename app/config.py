from functools import lru_cache
from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Unknown keys in .env are configuration mistakes, not harmless hints.
    model_config = SettingsConfigDict(env_file=".env", extra="forbid")
    telegram_bot_token: str
    owner_telegram_id: int
    llm_provider: str = "openai"
    llm_api_key: str
    llm_model: str = "gpt-4o-mini"
    llm_base_url: str | None = None
    # None deliberately lets the selected provider/model keep its own default.
    llm_temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    context_recent_max_messages: int = Field(default=24, ge=1, le=200)
    context_recent_token_budget: int = Field(default=1500, ge=100, le=20000)
    context_target_input_tokens: int = Field(default=5000, ge=500, le=100000)
    database_url: str = "sqlite:///data/companion.db"
    debounce_seconds: float = 2.0
    message_split_enabled: bool = True
    message_split_target_chars: int = 70
    message_split_min_chars: int = 25
    message_split_max_parts: int = 4
    initiative_enabled: bool = True
    initiative_check_interval_minutes: int = 15
    initiative_min_idle_minutes: int = 45
    initiative_cooldown_hours: int = 4
    initiative_max_per_day: int = 3
    initiative_max_unanswered: int = 1
    conversation_cooling_minutes: int = 30
    conversation_ended_hours: int = 12
    llm_supports_vision: bool = True
    llm_supports_multiple_images: bool = True
    media_cache_dir: str = "data/media-cache"
    media_cache_max_age_days: int = 14
    max_image_bytes: int = 8 * 1024 * 1024
    max_sticker_bytes: int = 2 * 1024 * 1024
    max_animation_bytes: int = 8 * 1024 * 1024
    sticker_analysis_enabled: bool = True
    sticker_analysis_workers: int = 1
    sticker_analysis_max_frames: int = 5
    sticker_analysis_retry_limit: int = 3
    sticker_analysis_version: str = "v1"
    sticker_current_analysis_timeout_seconds: float = 2.0
    sticker_selection_strategy: str = "ranked"
    sticker_embedding_provider: str = "local"
    sticker_embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    sticker_embedding_device: str = "cpu"
    sticker_repeat_window_hours: int = 24
    sticker_repeat_strong_window_minutes: int = 30
    recent_media_context_hours: int = 24
    timezone: str = "Europe/Kyiv"
    sleep_start_hour: int = 1
    wake_hour: int = 9
    image_generation_enabled: bool = False
    image_generation_daily_limit: int = 2
    image_generation_cooldown_hours: float = 12
    image_generation_provider: str = "openai_compatible"
    image_generation_model: str | None = None
    image_generation_api_key: str | None = None
    image_generation_base_url: str | None = None
    anya_reference_image: str = "assets/anya/reference.jpg"
    image_prompt_debug: bool = False
    daily_life_enabled: bool = True
    daily_life_check_interval_minutes: int = 30
    weather_enabled: bool = True
    weather_latitude: float = 50.4501
    weather_longitude: float = 30.5234
    weather_cache_minutes: int = 20
    @field_validator("llm_provider")
    @classmethod
    def supported_provider(cls, value: str) -> str:
        value = value.lower().strip()
        if value not in {"openai", "openrouter"}:
            raise ValueError("LLM_PROVIDER must be 'openai' or 'openrouter'")
        return value

    @field_validator("sticker_embedding_provider")
    @classmethod
    def supported_embedding_provider(cls, value: str) -> str:
        if value.lower().strip() != "local":
            raise ValueError("STICKER_EMBEDDING_PROVIDER must be 'local'")
        return "local"

    @field_validator("sticker_selection_strategy")
    @classmethod
    def supported_selection_strategy(cls, value: str) -> str:
        value = value.lower().strip()
        if value not in {"ranked", "weighted_top"}:
            raise ValueError("STICKER_SELECTION_STRATEGY must be 'ranked' or 'weighted_top'")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_settings() -> Settings:
    """Raise a short, actionable error instead of a raw Pydantic traceback."""
    try:
        return get_settings()
    except ValidationError as exc:
        fields = ", ".join(".".join(str(x) for x in error["loc"]) for error in exc.errors())
        raise RuntimeError(f"Invalid configuration. Fill these .env variables: {fields}") from exc
