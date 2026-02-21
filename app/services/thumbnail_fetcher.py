"""Download YouTube thumbnails concurrently and encode as base64.

Uses the HTML scraper results (videoRenderer contains thumbnail URLs)
instead of the YouTube Data API v3.
"""

import asyncio
import base64
import logging
from typing import Dict, List, Optional, Tuple

import httpx

from app.services.youtube_scraper import scrape_search

logger = logging.getLogger(__name__)

CONCURRENT_DOWNLOADS = 10
DOWNLOAD_TIMEOUT = 30.0


async def _download_thumbnail(
    client: httpx.AsyncClient, url: str
) -> Optional[Tuple[bytes, str]]:
    try:
        response = await client.get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        media_type = _detect_media_type(response.content, content_type)
        return response.content, media_type
    except httpx.HTTPError as e:
        logger.warning(f"Failed to download thumbnail {url}: {e}")
        return None


def _detect_media_type(data: bytes, content_type: str) -> str:
    """Detect actual media type from file magic bytes, fallback to content-type header."""
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"

    if "webp" in content_type:
        return "image/webp"
    if "png" in content_type:
        return "image/png"

    return "image/jpeg"


async def _download_all(thumbnail_urls: List[str], max_thumbnails: int) -> List[Dict]:
    transport = httpx.AsyncHTTPTransport(retries=3)
    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, transport=transport) as client:
        semaphore = asyncio.Semaphore(CONCURRENT_DOWNLOADS)

        async def download_with_limit(url: str) -> Tuple[str, Optional[Tuple[bytes, str]]]:
            async with semaphore:
                result = await _download_thumbnail(client, url)
                return url, result

        tasks = [download_with_limit(url) for url in thumbnail_urls]
        results = await asyncio.gather(*tasks)

    thumbnails = []
    for url, result in results:
        if result and len(thumbnails) < max_thumbnails:
            data, media_type = result
            thumbnails.append({
                "url": url,
                "base64": base64.standard_b64encode(data).decode("utf-8"),
                "media_type": media_type,
            })

    return thumbnails


def fetch_thumbnails(query: str, max_thumbnails: int = 20) -> Optional[Dict]:
    """Scrape YouTube search, extract thumbnail URLs, download and encode as base64."""
    scrape_result = scrape_search(query, max_results=max_thumbnails + 5)
    if not scrape_result:
        logger.error(f"Scrape failed for thumbnail query: {query}")
        return None

    thumbnail_urls = [
        video["thumbnail"]
        for video in scrape_result["videos"]
        if video.get("thumbnail")
    ]

    if not thumbnail_urls:
        logger.warning(f"No thumbnail URLs found for query: {query}")
        return None

    thumbnails = asyncio.run(_download_all(thumbnail_urls, max_thumbnails))

    if not thumbnails:
        logger.error(f"Failed to download any thumbnails for query: {query}")
        return None

    return {
        "query": query,
        "thumbnails": thumbnails,
        "count": len(thumbnails),
    }
