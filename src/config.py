"""Environment-driven configuration. No secrets are hard-coded; all come from Function App settings
(local.settings.json locally, App Settings / Key Vault references in Azure)."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    storage_connection_string: str
    doc_intelligence_endpoint: str
    doc_intelligence_key: str
    azure_openai_endpoint: str
    azure_openai_key: str
    azure_openai_deployment: str
    azure_openai_api_version: str = "2024-08-01-preview"
    target_dpi: int = 300
    min_dpi: int = 200
    max_pages: int = 10

    @classmethod
    def from_env(cls) -> "Settings":
        def require(name: str) -> str:
            val = os.environ.get(name)
            if not val:
                raise RuntimeError(f"Missing required environment variable: {name}")
            return val

        return cls(
            storage_connection_string=require("AzureWebJobsStorage"),
            doc_intelligence_endpoint=require("DOCUMENT_INTELLIGENCE_ENDPOINT"),
            doc_intelligence_key=require("DOCUMENT_INTELLIGENCE_KEY"),
            azure_openai_endpoint=require("AZURE_OPENAI_ENDPOINT"),
            azure_openai_key=require("AZURE_OPENAI_KEY"),
            azure_openai_deployment=require("AZURE_OPENAI_DEPLOYMENT"),
            azure_openai_api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
            target_dpi=int(os.environ.get("TARGET_DPI", "300")),
            min_dpi=int(os.environ.get("MIN_DPI", "200")),
            max_pages=int(os.environ.get("MAX_PAGES", "10")),
        )
