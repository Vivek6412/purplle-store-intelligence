from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str
    redis_url: str
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"
    store_layout_path: str = "data/store_layout.json"
    pos_csv_path: str = "data/pos_transactions.csv"
    clips_dir: str = "data/clips"
    output_dir: str = "output"
    metrics_cache_ttl: int = 30  # seconds
    heatmap_cache_ttl: int = 60
    anomalies_cache_ttl: int = 15
    queue_spike_threshold: int = 5
    conversion_drop_threshold: float = 0.80
    dead_zone_minutes: int = 30
    stale_feed_minutes: int = 10
    reid_sim_threshold: float = 0.85
    billing_window_minutes: int = 5
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

@lru_cache
def get_settings() -> Settings:
    return Settings()
