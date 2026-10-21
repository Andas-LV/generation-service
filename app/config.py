from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://gen:gen@localhost:5433/gen"
    webhook_secret: str = "dev-secret"
    public_url: str = "http://localhost:8000"
    providers: str = "alpha=http://localhost:9001,beta=http://localhost:9002"
    provider_timeout: float = 60.0
    dispatch_timeout: float = 5.0
    watchdog_interval: float = 2.0
    max_active_tasks: int = 3
    ledger_tail: int = 20
    db_pool_size: int = 10

    @property
    def provider_urls(self) -> dict[str, str]:
        result = {}
        for item in self.providers.split(","):
            name, _, url = item.strip().partition("=")
            if name and url:
                result[name.strip()] = url.strip().rstrip("/")
        return result


@lru_cache
def get_settings() -> Settings:
    return Settings()
