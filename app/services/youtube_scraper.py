"""YouTube search scraper — scrape real YouTube search results via ytInitialData.

Merged from:
- niche-finder/app/services/youtube_scraper.py (base, cleaner code)
- yt-scorer/app/services/scoring_service.py (TubeBuddy format + thumbnail extraction)

Supports two output formats:
- "standard": snake_case keys, views parsed as int (for niche-finder, thumbnails)
- "tubebuddy": PascalCase keys matching TubeBuddy API expectations (for yt-scorer)
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

YOUTUBE_COOKIES = os.getenv("YOUTUBE_COOKIES")
if not YOUTUBE_COOKIES:
    raise ValueError("YOUTUBE_COOKIES is required")

YOUTUBE_USER_AGENT = os.getenv("YOUTUBE_USER_AGENT")
if not YOUTUBE_USER_AGENT:
    raise ValueError("YOUTUBE_USER_AGENT is required")


def _create_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({
        "User-Agent": YOUTUBE_USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
        "Cookie": YOUTUBE_COOKIES,
    })
    return session


_session = _create_session()


def scrape_search(query: str, max_results: int = 20, output_format: str = "standard") -> Optional[Dict]:
    """Scrape YouTube search results page.

    Returns { estimated_results, videos[] } or None on failure.
    output_format: "standard" (snake_case, int views) or "tubebuddy" (PascalCase, text views).
    """
    url = f"https://www.youtube.com/results?search_query={quote(query)}&page=1"

    try:
        response = _session.get(url, timeout=30)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"YouTube scrape request failed: {e}")
        return None

    yt_data = _extract_yt_initial_data(response.text)
    if not yt_data:
        return None

    estimated_results = _find_key_recursive(yt_data, "estimatedResults")
    if not estimated_results:
        logger.warning("estimatedResults not found in ytInitialData")
        return None

    renderers = _extract_video_renderers(yt_data)

    if output_format == "tubebuddy":
        videos = _parse_videos_tubebuddy(renderers, max_results)
    else:
        videos = _parse_videos_standard(renderers, max_results)

    return {
        "estimated_results": int(estimated_results),
        "videos": videos,
    }


def _extract_yt_initial_data(html: str) -> Optional[Dict]:
    marker = "var ytInitialData = "
    start = html.find(marker)
    if start == -1:
        logger.warning("No ytInitialData found in YouTube page")
        return None

    start += len(marker)
    brace_count = 0
    in_string = False
    escape = False
    end = start

    for i in range(start, len(html)):
        char = html[i]
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if char == '"' and not escape:
            in_string = not in_string
            continue
        if not in_string:
            if char == "{":
                brace_count += 1
            elif char == "}":
                brace_count -= 1
                if brace_count == 0:
                    end = i + 1
                    break

    try:
        return json.loads(html[start:end])
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse ytInitialData JSON: {e}")
        return None


def _extract_video_renderers(yt_data: Dict) -> List[Dict]:
    contents = (
        yt_data.get("contents", {})
        .get("twoColumnSearchResultsRenderer", {})
        .get("primaryContents", {})
        .get("sectionListRenderer", {})
        .get("contents", [])
    )

    renderers = []
    for section in contents:
        for item in section.get("itemSectionRenderer", {}).get("contents", []):
            if "videoRenderer" in item:
                renderers.append(item["videoRenderer"])

    return renderers


def _parse_videos_standard(renderers: List[Dict], max_results: int) -> List[Dict]:
    videos = []
    for renderer in renderers[:max_results]:
        video = _parse_video_standard(renderer)
        if video:
            videos.append(video)
    return videos


def _parse_video_standard(renderer: Dict) -> Optional[Dict]:
    video_id = renderer.get("videoId")
    if not video_id:
        return None

    title = renderer.get("title", {}).get("runs", [{}])[0].get("text", "")
    channel_name = renderer.get("ownerText", {}).get("runs", [{}])[0].get("text", "")
    channel_id = (
        renderer.get("ownerText", {})
        .get("runs", [{}])[0]
        .get("navigationEndpoint", {})
        .get("browseEndpoint", {})
        .get("browseId", "")
    )

    view_count_text = _get_view_count_text(renderer)
    published_time = renderer.get("publishedTimeText", {}).get("simpleText", "")
    description_snippet = _get_description_snippet(renderer)
    thumbnail = _get_best_thumbnail(renderer)

    return {
        "video_id": video_id,
        "title": title,
        "channel_name": channel_name,
        "channel_id": channel_id,
        "views": parse_view_count(view_count_text),
        "published_time": published_time,
        "description_snippet": description_snippet,
        "thumbnail": thumbnail,
    }


def _parse_videos_tubebuddy(renderers: List[Dict], max_results: int) -> List[Dict]:
    videos = []
    for renderer in renderers[:max_results]:
        video = _parse_video_tubebuddy(renderer)
        if video:
            videos.append(video)
    return videos


def _parse_video_tubebuddy(renderer: Dict) -> Optional[Dict]:
    video_id = renderer.get("videoId")
    if not video_id:
        return None

    title = renderer.get("title", {}).get("runs", [{}])[0].get("text", "")
    channel_name = renderer.get("ownerText", {}).get("runs", [{}])[0].get("text", "")
    channel_id = (
        renderer.get("ownerText", {})
        .get("runs", [{}])[0]
        .get("navigationEndpoint", {})
        .get("browseEndpoint", {})
        .get("browseId", "")
    )

    view_count_text = _get_view_count_text(renderer)
    published_time = renderer.get("publishedTimeText", {}).get("simpleText", "")
    description = _get_description_snippet(renderer)
    thumbnail = _get_best_thumbnail(renderer)

    return {
        "Type": "video",
        "Id": video_id,
        "URL": f"https://www.youtube.com/watch?v={video_id}",
        "ChannelId": channel_id,
        "ChannelName": channel_name,
        "ChannelUrl": f"https://www.youtube.com/channel/{channel_id}",
        "Desc": description,
        "PublishedTime": published_time,
        "Thumbnail": thumbnail,
        "Title": title,
        "ViewCount": view_count_text,
    }


def _get_view_count_text(renderer: Dict) -> str:
    text = renderer.get("viewCountText", {}).get("simpleText", "")
    if not text:
        text = renderer.get("shortViewCountText", {}).get("simpleText", "")
    return text


def _get_description_snippet(renderer: Dict) -> str:
    runs = (
        renderer.get("detailedMetadataSnippets", [{}])[0]
        .get("snippetText", {})
        .get("runs", [])
    )
    return "".join(r.get("text", "") for r in runs)


def _get_best_thumbnail(renderer: Dict) -> str:
    thumbnails = renderer.get("thumbnail", {}).get("thumbnails", [])
    return thumbnails[-1].get("url", "") if thumbnails else ""


def _find_key_recursive(data: Any, key: str) -> Any:
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for value in data.values():
            result = _find_key_recursive(value, key)
            if result is not None:
                return result
    elif isinstance(data, list):
        for item in data:
            result = _find_key_recursive(item, key)
            if result is not None:
                return result
    return None


def parse_view_count(text: str) -> int:
    """Parse YouTube view count text to int. Ex: '1,234 views' -> 1234, '1.2M views' -> 1200000."""
    if not text:
        return 0
    text = text.lower().replace(",", "").replace(" views", "").replace(" view", "").strip()
    if not text or text == "no":
        return 0

    multipliers = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}
    for suffix, multiplier in multipliers.items():
        if text.endswith(suffix):
            numbers = re.sub(r"[^\d.]", "", text[:-1])
            if numbers:
                return int(float(numbers) * multiplier)
            return 0

    numbers = re.sub(r"[^\d]", "", text)
    return int(numbers) if numbers else 0
