from typing import Any

SECRET_FRAGMENTS = (
    "token",
    "password",
    "pass",
    "secret",
    "authorization",
    "cookie",
    "did",
    "user",
    "url",
)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if any(part in key.lower() for part in SECRET_FRAGMENTS)
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value
