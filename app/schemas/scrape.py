from typing import Literal, Optional

from pydantic import BaseModel, Field


class ScrapeRequest(BaseModel):
    query: str
    max_results: int = Field(default=20, ge=1, le=50)
    format: Literal["standard", "tubebuddy"] = "standard"


class JobResponse(BaseModel):
    job_id: str
