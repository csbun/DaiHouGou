from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from daihougou.redaction import redact


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
