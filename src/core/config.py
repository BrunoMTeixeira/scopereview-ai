"""Configuration module with strong validation using Pydantic Settings.

This module provides environment-aware configuration with fail-fast validation
for production deployments while allowing flexibility for development/testing.
"""
import base64
import os
from typing import Optional

from pydantic import Field, field_validator, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralized configuration with strong validation and environment awareness.

    Critical settings (Azure credentials, ADO tokens) are required in production mode.
    Development mode (ENVIRONMENT=dev) relaxes these constraints for local testing.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ──────────────────────────────────────────────────────────────────────────
    # Environment Control
    # ──────────────────────────────────────────────────────────────────────────
    ENVIRONMENT: str = Field(default="production", description="Deployment environment: 'production' or 'dev'")

    # ──────────────────────────────────────────────────────────────────────────
    # Azure AI Configuration (Code Review Agent)
    # ──────────────────────────────────────────────────────────────────────────
    AZURE_ENDPOINT_CR: str = Field(default="", description="Azure OpenAI endpoint for Code Review agent")
    AZURE_API_KEY_CR: str = Field(default="", description="Azure OpenAI API key for Code Review agent")
    AZURE_MODEL_CR: str = Field(default="o4-mini", description="Model deployment name for Code Review")

    # ──────────────────────────────────────────────────────────────────────────
    # Azure AI Configuration (Requirements Agent)
    # ──────────────────────────────────────────────────────────────────────────
    AZURE_ENDPOINT_REQ: str = Field(default="", description="Azure OpenAI endpoint for Requirements agent")
    AZURE_API_KEY_REQ: str = Field(default="", description="Azure OpenAI API key for Requirements agent")
    AZURE_MODEL_REQ: str = Field(default="DeepSeek-V3.2", description="Model deployment name for Requirements")

    # ──────────────────────────────────────────────────────────────────────────
    # Azure DevOps Integration
    # ──────────────────────────────────────────────────────────────────────────
    ADO_ORGANIZATION: str = Field(default="", description="Azure DevOps organization name")
    ADO_PAT: str = Field(default="", description="Azure DevOps Personal Access Token")
    ADO_REQUEST_TIMEOUT: int = Field(default=20, description="HTTP timeout for ADO API calls (seconds)")

    # ──────────────────────────────────────────────────────────────────────────
    # Security & Rate Limiting
    # ──────────────────────────────────────────────────────────────────────────
    WEBHOOK_SECRET: str = Field(default="", description="Shared secret for webhook signature validation")
    WEBHOOK_RATE_LIMIT: int = Field(default=10, ge=1, le=100, description="Max webhooks per minute")

    # ──────────────────────────────────────────────────────────────────────────
    # Processing Limits
    # ──────────────────────────────────────────────────────────────────────────
    MAX_FILES: int = Field(default=5, ge=1, le=50, description="Maximum files to analyze per PR")
    MAX_LINES: int = Field(default=400, ge=50, le=2000, description="Maximum lines to fetch per file")
    MAX_TENTATIVAS: int = Field(default=3, ge=1, le=10, description="Max retry attempts for HTTP calls")
    MAX_HIGH_BLOCK: int = Field(default=3, ge=0, le=10, description="Max HIGH severity issues before blocking PR")
    MAX_TOKEN_BUDGET: int = Field(default=50000, ge=1000, description="Max cumulative LLM tokens per PR analysis")
    REQUIREMENTS_MAX_COMPLETION_TOKENS: int = Field(
        default=24000, ge=1000, description="Max completion tokens for Requirements agent"
    )

    # ──────────────────────────────────────────────────────────────────────────
    # Deduplication & Caching
    # ──────────────────────────────────────────────────────────────────────────
    DEDUP_SECONDS: int = Field(default=300, ge=0, description="Deduplication window in seconds")
    DEDUP_REDIS_URL: str = Field(default="", description="Optional Redis URL for distributed dedup")
    DEDUP_REDIS_KEY_PREFIX: str = Field(default="scopereview:dedup", description="Redis key prefix")

    @field_validator("AZURE_ENDPOINT_CR", "AZURE_ENDPOINT_REQ")
    @classmethod
    def validate_https_endpoints(cls, v: str, info) -> str:
        """Enforce HTTPS for Azure endpoints (unless in dev mode or empty)."""
        field_name = info.field_name
        environment = os.getenv("ENVIRONMENT", "production")

        # Allow empty endpoints in dev mode for local testing
        if not v:
            if environment == "production":
                raise ValueError(
                    f"{field_name} is required in production mode. "
                    f"Set ENVIRONMENT=dev for local development."
                )
            return v

        # Enforce HTTPS for non-empty endpoints (security best practice)
        if not v.startswith("https://"):
            if environment == "production":
                raise ValueError(
                    f"{field_name} must use HTTPS in production. "
                    f"For local HTTP mocks, set ENVIRONMENT=dev."
                )

        return v

    @field_validator("AZURE_API_KEY_CR", "AZURE_API_KEY_REQ", "ADO_PAT")
    @classmethod
    def validate_credentials(cls, v: str, info) -> str:
        """Ensure critical credentials are set in production mode."""
        field_name = info.field_name
        environment = os.getenv("ENVIRONMENT", "production")

        if not v and environment == "production":
            raise ValueError(
                f"{field_name} is required in production mode. "
                f"Set ENVIRONMENT=dev to skip this validation for local testing."
            )

        return v

    @property
    def ado_auth_header(self) -> str:
        """Base64 ADO authentication header (legacy helper; prefer AzureDevOpsClient)."""
        if not self.ADO_PAT:
            return ""
        return f"Basic {base64.b64encode(f':{self.ADO_PAT}'.encode()).decode()}"

    @property
    def is_production(self) -> bool:
        """Check if running in production mode."""
        return self.ENVIRONMENT.lower() == "production"

    @property
    def is_dev(self) -> bool:
        """Check if running in development mode."""
        return self.ENVIRONMENT.lower() == "dev"


def load_settings() -> Settings:
    """Load and validate settings with clear error messages on failure.

    Returns:
        Settings: Validated configuration instance.

    Raises:
        SystemExit: If configuration validation fails in production mode.
    """
    try:
        settings = Settings()
        return settings
    except ValidationError as e:
        error_msg = "❌ Configuration validation failed:\n\n"
        for error in e.errors():
            field = ".".join(str(loc) for loc in error["loc"])
            msg = error["msg"]
            error_msg += f"  • {field}: {msg}\n"

        error_msg += (
            "\n💡 Tip: Set ENVIRONMENT=dev in .env for local development to bypass production validations.\n"
        )

        # In production mode, fail fast
        if os.getenv("ENVIRONMENT", "production").lower() == "production":
            print(error_msg)
            raise SystemExit(1)
        else:
            # In dev mode, warn but allow startup
            print(f"⚠️  Warning: {error_msg}")
            raise


# Singleton instance
settings = load_settings()


def validate_settings(*, require_https_endpoints: bool = True) -> None:
    """
    Legacy validation function for backward compatibility.

    The validation is now handled by Pydantic validators in the Settings class.
    This function is kept for compatibility with existing code but is now a no-op.
    """
    pass  # Validation now happens in Settings class via Pydantic validators
