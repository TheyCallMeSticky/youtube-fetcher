"""Synchronous endpoints for YouTube Data API v3 (video descriptions, channel stats).

Unlike scrape/thumbnail endpoints, these do NOT go through RQ — API v3 calls
are fast (<1s) and don't need background processing.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import verify_api_key
from app.schemas.youtube_api import (
    ChannelSubscribersRequest,
    ChannelSubscribersResponse,
    VideoDescriptionsRequest,
    VideoDescriptionsResponse,
)
from app.services.youtube_api import YouTubeDataAPI

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/youtube",
    tags=["youtube-api"],
    dependencies=[Depends(verify_api_key)],
)

_youtube_api = YouTubeDataAPI()


@router.post("/videos", response_model=VideoDescriptionsResponse)
def get_video_descriptions(request: VideoDescriptionsRequest):
    """Fetch full video descriptions from YouTube Data API v3."""
    try:
        descriptions = _youtube_api.get_video_descriptions(request.video_ids)
        return VideoDescriptionsResponse(descriptions=descriptions)
    except Exception as e:
        if "YOUTUBE_QUOTA_EXCEEDED" in str(e):
            raise HTTPException(status_code=429, detail="YouTube API quota exceeded")
        raise HTTPException(status_code=502, detail=f"YouTube API error: {e}")


@router.post("/channels", response_model=ChannelSubscribersResponse)
def get_channel_subscribers(request: ChannelSubscribersRequest):
    """Fetch channel subscriber counts from YouTube Data API v3."""
    try:
        subscribers = _youtube_api.get_channel_subscribers(request.channel_ids)
        return ChannelSubscribersResponse(subscribers=subscribers)
    except Exception as e:
        if "YOUTUBE_QUOTA_EXCEEDED" in str(e):
            raise HTTPException(status_code=429, detail="YouTube API quota exceeded")
        raise HTTPException(status_code=502, detail=f"YouTube API error: {e}")
