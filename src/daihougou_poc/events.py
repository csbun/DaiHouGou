from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

SECRET_FRAGMENTS = ("token", "password", "pass", "secret", "authorization", "cookie")


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


@dataclass(frozen=True)
class ProbeEvent:
    timestamp: str
    correlation_id: str
    component: str
    operation: str
    success: bool
    campaign_id: str = ""
    inventory_sha256: str = ""
    latency_ms: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, component: str, operation: str, success: bool, **kwargs: Any) -> "ProbeEvent":
        return cls(
            timestamp=datetime.now(UTC).isoformat(),
            correlation_id=kwargs.pop("correlation_id", str(uuid4())),
            component=component,
            operation=operation,
            success=success,
            campaign_id=kwargs.pop("campaign_id", ""),
            inventory_sha256=kwargs.pop("inventory_sha256", ""),
            latency_ms=kwargs.pop("latency_ms", None),
            details=redact(kwargs.pop("details", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
