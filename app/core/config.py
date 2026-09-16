from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # OpenRouter (OpenAI-compatible endpoint)
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "deepseek/deepseek-v4-flash-0731"  # confirmed slug, see .env.example

    # GitHub MCP
    github_pat: str = ""

    # Auth
    jwt_secret: str = "change-this-in-your-.env-to-something-long-and-random"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60


settings = Settings()