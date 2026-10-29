from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    ollama_url: str
    daily_model: str
    research_model: str
    admin_token: str
    external_feeds: bool
    local_trust: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            data_dir=Path(os.getenv("MR_SMITH_DATA_DIR", "data")),
            ollama_url=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"),
            daily_model=os.getenv("MR_SMITH_DAILY_MODEL", "llama3.1:8b"),
            research_model=os.getenv("MR_SMITH_RESEARCH_MODEL", "gemma4:26b"),
            admin_token=os.getenv("MR_SMITH_ADMIN_TOKEN", ""),
            external_feeds=os.getenv("MR_SMITH_EXTERNAL_FEEDS", "0") == "1",
            local_trust=os.getenv("MR_SMITH_LOCAL_TRUST", "0") == "1",
        )
