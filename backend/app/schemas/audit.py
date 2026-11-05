from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AuditLogResponse(BaseModel):
    id: int
    user_id: int | None
    user_email: str | None
    action: str
    resource_type: str
    resource_id: int | None
    result: str
    ip_address: str | None
    timestamp: datetime
    user_department_id: int | None = None
    user_department_name: str | None = None
    resource_department_id: int | None = None
    resource_department_name: str | None = None
    duration_ms: int | None = None
    details: dict[str, object] | None = None

    model_config = ConfigDict(from_attributes=True)
