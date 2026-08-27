"""Environment-driven configuration. No secrets are hard-coded; all come from platform env vars --
Function App settings (local.settings.json locally, App Settings / Key Vault references in Azure)
or Vercel env vars (.env locally, the Vercel dashboard/CLI in production)."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    storage_connection_string: str
    doc_intelligence_endpoint: str
    doc_intelligence_key: str
    claude_api_key: str
    claude_model: str
    claude_max_tokens: int = 4096
    claude_api_base_url: str | None = None  # optional: route through a proxy/gateway instead of api.anthropic.com
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

        def require_any(*names: str) -> str:
            for name in names:
                val = os.environ.get(name)
                if val:
                    return val
            raise RuntimeError(f"Missing required environment variable: {' or '.join(names)}")

        return cls(
            # AzureWebJobsStorage is the Azure Functions host's own magic name for this connection
            # string; AZURE_STORAGE_CONNECTION_STRING is the platform-neutral name used on Vercel
            # (app.py) and preferred going forward -- both are honored so an existing Azure
            # Functions deployment doesn't need its app settings renamed.
            storage_connection_string=require_any("AZURE_STORAGE_CONNECTION_STRING", "AzureWebJobsStorage"),
            doc_intelligence_endpoint=require("DOCUMENT_INTELLIGENCE_ENDPOINT"),
            doc_intelligence_key=require("DOCUMENT_INTELLIGENCE_KEY"),
            claude_api_key=require("CLAUDE_API_KEY"),
            claude_model=os.environ.get("CLAUDE_MODEL", "claude-sonnet-5"),
            claude_max_tokens=int(os.environ.get("CLAUDE_MAX_TOKENS", "4096")),
            claude_api_base_url=os.environ.get("CLAUDE_API_BASE_URL") or None,
            target_dpi=int(os.environ.get("TARGET_DPI", "300")),
            min_dpi=int(os.environ.get("MIN_DPI", "200")),
            max_pages=int(os.environ.get("MAX_PAGES", "10")),
        )
