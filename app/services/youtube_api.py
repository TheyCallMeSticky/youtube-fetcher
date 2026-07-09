"""YouTube Data API v3 service with multi-key rotation and file cache.

Centralizes all YouTube Data API v3 calls behind youtube-fetcher.
Supports up to 12 API keys with automatic rotation on quota exhaustion.
"""

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://www.googleapis.com/youtube/v3"


class YouTubeDataAPI:
    """YouTube Data API v3 with multi-key rotation and file-based caching."""

    def __init__(self):
        mode = os.getenv("YOUTUBE_MODE")
        if not mode:
            raise ValueError("YOUTUBE_MODE is required (LIVE or MOCK)")
        self.mode = mode.upper()

        cache_dir_env = os.getenv("YOUTUBE_CACHE_DIR")
        if not cache_dir_env:
            raise ValueError("YOUTUBE_CACHE_DIR is required")
        self.cache_dir = Path(cache_dir_env)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        if self.mode != "MOCK":
            self._init_api_keys()

        logger.info(
            f"YouTubeDataAPI initialized (mode={self.mode}, "
            f"keys={len(self.api_keys) if self.mode != 'MOCK' else 0})"
        )

    def _init_api_keys(self):
        self.api_keys: List[str] = []
        for i in range(1, 13):
            key = os.getenv(f"YOUTUBE_API_KEY_{i}")
            if key and key.strip():
                self.api_keys.append(key.strip())

        if not self.api_keys:
            raise ValueError("At least one YOUTUBE_API_KEY_N is required (1-12)")

        self.current_key_index = 0
        self.exhausted_keys: set = set()

    def make_request(self, endpoint: str, params: Dict[str, Any]) -> Optional[Dict]:
        """Make a YouTube Data API request with key rotation and caching."""
        cache_key = self._get_cache_key(endpoint, params)

        if self.mode == "MOCK":
            return self._load_from_cache(cache_key)

        available_keys = [k for k in self.api_keys if k not in self.exhausted_keys]
        if not available_keys:
            raise Exception("YOUTUBE_QUOTA_EXCEEDED")

        if self.api_keys[self.current_key_index] in self.exhausted_keys:
            self._rotate_to_available_key()

        for _ in range(len(available_keys)):
            current_key = self.api_keys[self.current_key_index]
            if current_key in self.exhausted_keys:
                self._rotate_to_available_key()
                continue

            params["key"] = current_key
            response = requests.get(f"{BASE_URL}/{endpoint}", params=params)

            if response.status_code == 200:
                result = response.json()
                self._save_to_cache(cache_key, result)
                return result

            if response.status_code == 403:
                self.exhausted_keys.add(current_key)
                logger.warning(f"YouTube key {self.current_key_index + 1} exhausted")
                self._rotate_to_available_key()
                time.sleep(0.5)
                continue

            logger.error(f"YouTube API error {response.status_code}: {response.text}")
            return None

        raise Exception("YOUTUBE_QUOTA_EXCEEDED")

    def get_video_descriptions(self, video_ids: List[str]) -> Dict[str, Dict]:
        """Fetch full descriptions for a batch of video IDs (max 50)."""
        if not video_ids:
            return {}

        result = self.make_request(
            "videos", {"part": "snippet", "id": ",".join(video_ids[:50])}
        )
        if not result or "items" not in result:
            return {}

        descriptions: Dict[str, Dict] = {}
        for item in result["items"]:
            snippet = item.get("snippet")
            if snippet and "description" in snippet:
                descriptions[item["id"]] = {"description": snippet["description"]}
        return descriptions

    def get_channel_subscribers(self, channel_ids: List[str]) -> Dict[str, int]:
        """Fetch subscriber counts for a batch of channel IDs (max 50)."""
        if not channel_ids:
            return {}

        result = self.make_request(
            "channels", {"part": "statistics", "id": ",".join(channel_ids[:50])}
        )
        if not result or "items" not in result:
            return {}

        subs_map: Dict[str, int] = {}
        for item in result["items"]:
            statistics = item.get("statistics")
            if statistics and "subscriberCount" in statistics:
                subs_map[item["id"]] = int(statistics["subscriberCount"])
        return subs_map

    def _rotate_to_available_key(self):
        for i in range(len(self.api_keys)):
            idx = (self.current_key_index + 1 + i) % len(self.api_keys)
            if self.api_keys[idx] not in self.exhausted_keys:
                self.current_key_index = idx
                return
        raise Exception("YOUTUBE_QUOTA_EXCEEDED")

    def _get_cache_key(self, endpoint: str, params: Dict) -> str:
        cache_params = {k: v for k, v in params.items() if k != "key"}
        raw = f"{endpoint}_{json.dumps(cache_params, sort_keys=True)}"
        return hashlib.md5(raw.encode()).hexdigest()

    def _save_to_cache(self, cache_key: str, data: Dict):
        cache_file = self.cache_dir / f"{cache_key}.json"
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _load_from_cache(self, cache_key: str) -> Optional[Dict]:
        cache_file = self.cache_dir / f"{cache_key}.json"
        if cache_file.exists():
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return None
