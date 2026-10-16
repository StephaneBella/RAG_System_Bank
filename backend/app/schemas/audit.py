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

    model_config = ConfigDict(from_attributes=True)
