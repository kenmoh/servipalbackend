
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime

class AuditLogBase(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    actor_id: str
    actor_name: str
    actor_role: str
    action: str
    resource_type: str
    resource_id: Optional[str] = None
    resource_summary: Optional[str] = None
    changes: Optional[Dict[str, List[Any]]] = None  # e.g., {"field": [old, new]}
    ip_address: Optional[str] = None
    extra_metadata: Optional[Dict[str, Any]] = None


class AuditLogResponse(AuditLogBase):
    id: str
