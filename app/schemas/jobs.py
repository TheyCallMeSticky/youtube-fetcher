from typing import Any, Optional

from pydantic import BaseModel


class JobStatus(BaseModel):
    status: str
    progress: int = 0
    result: Optional[Any] = None
    error: Optional[str] = None
