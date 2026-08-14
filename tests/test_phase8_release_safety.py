import logging

import pytest
from pydantic import ValidationError

from services.api.app.config import Settings
from services.api.app.observability import JsonFormatter, redact


def test_production_rejects_development_credentials_and_http_cors() -> None:
    with pytest.raises(ValidationError, match="unsafe production configuration"):
        Settings(app_env="production", _env_file=None)


def test_production_accepts_hardened_connection_settings() -> None:
    settings = Settings(
        app_env="production",
        log_level="INFO",
        api_cors_origins=["https://routebook.example.com"],
        database_url="postgresql+psycopg://app:secret@postgres:5432/routebook",
        langgraph_database_url="postgresql://graph:secret@postgres:5432/routebook",
        _env_file=None,
    )

    assert settings.app_env == "production"


def test_redaction_covers_nested_secrets_and_query_credentials() -> None:
    value = {
        "authorization": "Bearer secret",
        "nested": [{"password": "secret"}],
        "url": "https://provider.example/path?key=secret&city=320100",
    }

    assert redact(value) == {
        "authorization": "[REDACTED]",
        "nested": [{"password": "[REDACTED]"}],
        "url": "https://provider.example/path?key=[REDACTED]&city=320100",
    }


def test_json_formatter_never_serializes_query_secret() -> None:
    record = logging.LogRecord(
        "routebook.test",
        logging.INFO,
        __file__,
        1,
        "provider failed: https://example.test?q=1&token=top-secret",
        (),
        None,
    )

    rendered = JsonFormatter().format(record)

    assert "top-secret" not in rendered
    assert "token=[REDACTED]" in rendered
