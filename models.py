from pydantic import BaseModel


class AuditEntry(BaseModel):
    """Một bản ghi audit trail cho mỗi quyết định của agent / human reviewer."""

    timestamp: str
    agent_id: str
    action: str
    confidence: float
    reviewer_id: str
    decision: str
