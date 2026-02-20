import logging
import os

from fastapi import FastAPI

LOG_LEVEL_STR = os.getenv("LOG_LEVEL")
if not LOG_LEVEL_STR:
    raise ValueError("LOG_LEVEL is required")

LOG_LEVEL = getattr(logging, LOG_LEVEL_STR.upper(), None)
if not isinstance(LOG_LEVEL, int):
    raise ValueError(f"Invalid LOG_LEVEL: {LOG_LEVEL_STR}")

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

from app.api.jobs import router as jobs_router
from app.api.scrape import router as scrape_router
from app.api.thumbnails import router as thumbnails_router
from app.api.youtube_api import router as youtube_api_router

app = FastAPI(
    title="YouTube Fetcher API",
    description="Scraping YouTube search results, thumbnail downloading, and YouTube Data API v3",
    version="1.1.0",
)

app.include_router(scrape_router)
app.include_router(thumbnails_router)
app.include_router(jobs_router)
app.include_router(youtube_api_router)


@app.get("/health")
def health_check():
    return {"status": "ok"}
