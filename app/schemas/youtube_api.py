"""Schemas for YouTube Data API v3 endpoints."""

from typing import Dict, List

from pydantic import BaseModel, Field


class VideoDescriptionsRequest(BaseModel):
    video_ids: List[str] = Field(..., min_length=1, max_length=50)


class VideoDescriptionsResponse(BaseModel):
    descriptions: Dict[str, Dict]


class ChannelSubscribersRequest(BaseModel):
    channel_ids: List[str] = Field(..., min_length=1, max_length=50)


class ChannelSubscribersResponse(BaseModel):
    subscribers: Dict[str, int]
