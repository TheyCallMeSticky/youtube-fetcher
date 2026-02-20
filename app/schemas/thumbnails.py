from pydantic import BaseModel, Field


class ThumbnailFetchRequest(BaseModel):
    query: str
    max_thumbnails: int = Field(default=20, ge=1, le=50)
