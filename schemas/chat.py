from pydantic import BaseModel
from typing import Optional

class SendMessageRequest(BaseModel):
    session_id: Optional[int] = None
    message: str
