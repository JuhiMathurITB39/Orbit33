"""Environment-based configuration. No secrets are ever hard-coded; see .env.example."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _csv(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default)
    return [x.strip() for x in raw.split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("ORBIT_DATA_DIR", ROOT / "data")))
    seed: int = field(default_factory=lambda: int(os.environ.get("ORBIT_SEED", "42")))
    # Real M5 files (calendar.csv, sales_train_evaluation.csv, sell_prices.csv) if the user provides them.
    m5_dir: Path | None = field(
        default_factory=lambda: Path(os.environ["ORBIT_M5_DIR"]) if os.environ.get("ORBIT_M5_DIR") else None
    )
    # Optional subset of the data (useful for the full real M5 on a laptop): e.g. ORBIT_STORES=CA_1,TX_1,WI_1 and
    # ORBIT_ITEMS_PER_DEPT=8 keeps the first 8 items (sorted by id) in each department. Unset = use everything.
    stores: list[str] = field(default_factory=lambda: _csv("ORBIT_STORES"))
    items_per_dept: int | None = field(default_factory=lambda: int(os.environ["ORBIT_ITEMS_PER_DEPT"]) if os.environ.get("ORBIT_ITEMS_PER_DEPT") else None)
    # API
    api_keys: list[str] = field(default_factory=lambda: _csv("ORBIT_API_KEYS"))
    cors_origins: list[str] = field(default_factory=lambda: _csv("ORBIT_CORS_ORIGINS", "http://localhost:8000"))
    # LLM provider: "none" (deterministic, grounded templates) or "anthropic".
    llm_provider: str = field(default_factory=lambda: os.environ.get("ORBIT_LLM_PROVIDER", "none"))
    llm_api_key: str | None = field(default_factory=lambda: os.environ.get("ORBIT_LLM_API_KEY"))
    llm_model: str = field(default_factory=lambda: os.environ.get("ORBIT_LLM_MODEL", ""))
    log_level: str = field(default_factory=lambda: os.environ.get("ORBIT_LOG_LEVEL", "INFO"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "orbit.db"

    @property
    def bronze_dir(self) -> Path:
        return self.data_dir / "bronze"

    @property
    def silver_dir(self) -> Path:
        return self.data_dir / "silver"

    @property
    def gold_dir(self) -> Path:
        return self.data_dir / "gold"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"


def get_settings() -> Settings:
    return Settings()
