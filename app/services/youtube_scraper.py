"""YouTube data fetcher — via l'API interne InnerTube (youtubei/v1/*), pas de scraping de page.

InnerTube est l'API JSON qu'utilise le client web youtube.com lui-même : mêmes données que
ytInitialData mais renvoyées EN JSON (pas de page HTML ~500 KB à parser) → bien moins de CPU
par requête, limites bien plus hautes, plus sûr côté bans. Les renderers sont identiques à ceux
de ytInitialData → tout le parsing ci-dessous est réutilisé tel quel.

Supports two output formats:
- "standard": snake_case keys, views parsed as int (for niche-finder, thumbnails)
- "tubebuddy": PascalCase keys matching TubeBuddy API expectations (for yt-scorer)
"""

import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.core.redis import redis_conn

logger = logging.getLogger(__name__)

YOUTUBE_COOKIES = os.getenv("YOUTUBE_COOKIES")
if not YOUTUBE_COOKIES:
    raise ValueError("YOUTUBE_COOKIES is required")

YOUTUBE_USER_AGENT = os.getenv("YOUTUBE_USER_AGENT")
if not YOUTUBE_USER_AGENT:
    raise ValueError("YOUTUBE_USER_AGENT is required")

# Proxy résidentiel rotatif (optionnel). Stratégie coût : IP LOCALE par défaut (gratuite) ;
# sur un 429 en direct → bascule proxy pour PROXY_WINDOW_SECONDS (état partagé entre workers
# via Redis), puis retour auto à l'IP locale. Le proxy ne sert donc que pendant les throttles.
PROXY_URL = os.getenv("YOUTUBE_FETCHER_PROXY_URL")
PROXY_FLAG_KEY = "scrape:use_proxy"
PROXY_WINDOW_SECONDS = 7200  # 2h de proxy après un 429, puis on retente l'IP locale

# InnerTube : clé et version du client web YouTube. La clé est publique et stable depuis des
# années ; la version est très tolérante (une valeur « périmée » passe encore) → surchargeable
# par env si YouTube resserre un jour, sinon les défauts suffisent.
INNERTUBE_KEY = os.getenv("YOUTUBE_INNERTUBE_KEY", "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8")
INNERTUBE_CLIENT_VERSION = os.getenv("YOUTUBE_INNERTUBE_CLIENT_VERSION", "2.20240401.00.00")
INNERTUBE_CONTEXT = {
    "client": {"clientName": "WEB", "clientVersion": INNERTUBE_CLIENT_VERSION, "hl": "en", "gl": "US"}
}


def _create_session(proxy: Optional[str] = None) -> requests.Session:
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({
        "User-Agent": YOUTUBE_USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
        "Cookie": YOUTUBE_COOKIES,
    })
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    return session


_session_direct = _create_session()
_session_proxy = _create_session(PROXY_URL) if PROXY_URL else None


def _proxy_window_active() -> bool:
    """Sommes-nous dans une fenêtre proxy (429 récent, TTL non expiré) ? Partagé via Redis."""
    return _session_proxy is not None and bool(redis_conn.exists(PROXY_FLAG_KEY))


def _open_proxy_window() -> None:
    """Bascule TOUS les workers sur le proxy pour PROXY_WINDOW_SECONDS."""
    redis_conn.set(PROXY_FLAG_KEY, b"1", ex=PROXY_WINDOW_SECONDS)
    logger.warning(f"429 sur IP locale → bascule PROXY pour {PROXY_WINDOW_SECONDS // 3600}h")


def _innertube_post(endpoint: str, payload: Dict) -> requests.Response:
    """POST vers InnerTube (youtubei/v1/{endpoint}) avec la stratégie proxy : IP locale par
    défaut, bascule proxy 2h sur un 429 en direct (état partagé entre workers via Redis).
    Mêmes sessions (UA + proxy) que l'ancien GET — on ne change que le transport."""
    url = f"https://www.youtube.com/youtubei/v1/{endpoint}?key={INNERTUBE_KEY}&prettyPrint=false"
    body = {"context": INNERTUBE_CONTEXT, **payload}
    if _proxy_window_active():
        return _session_proxy.post(url, json=body, timeout=30)
    response = _session_direct.post(url, json=body, timeout=30)
    if response.status_code == 429 and _session_proxy is not None:
        _open_proxy_window()
        return _session_proxy.post(url, json=body, timeout=30)
    return response


def scrape_search(query: str, max_results: int = 20, output_format: str = "standard") -> Optional[Dict]:
    """Recherche YouTube via InnerTube (youtubei/v1/search).

    Returns { estimated_results, videos[] } or None on failure.
    output_format: "standard" (snake_case, int views) or "tubebuddy" (PascalCase, text views).
    """
    try:
        response = _innertube_post("search", {"query": query})
        response.raise_for_status()
        yt_data = response.json()
    except (requests.RequestException, ValueError) as e:
        logger.error(f"InnerTube search request failed: {e}")
        return None

    estimated_results = _find_key_recursive(yt_data, "estimatedResults")
    if not estimated_results:
        logger.warning("estimatedResults not found in InnerTube response")
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


def _dig_attributed_description(node: Any) -> Optional[str]:
    """Description complète depuis attributedDescription.content (endpoint next → panneau
    description de la watch page). Prend la PLUS LONGUE si plusieurs (la vraie description vs des
    micro-textes attribués). None si aucune (vidéo indispo / description vide)."""
    best: Optional[str] = None
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            attr = cur.get("attributedDescription")
            if isinstance(attr, dict):
                content = attr.get("content")
                if content and (best is None or len(content) > len(best)):
                    best = content
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return best


_DATE_PREFIXES = ("Premiered ", "Premieres ", "Streamed live on ", "Started streaming on ")


def _parse_watch_date(date_text: Optional[str]) -> Optional[str]:
    """dateText de la watch page ('Aug 19, 2020', 'Premiered Jan 5, 2023') → date ISO. None si
    non-parsable (locale inattendue / date relative)."""
    if not date_text:
        return None
    txt = date_text.strip()
    for prefix in _DATE_PREFIXES:
        if txt.startswith(prefix):
            txt = txt[len(prefix):]
            break
    try:
        return datetime.strptime(txt, "%b %d, %Y").date().isoformat()
    except ValueError:
        return None


def scrape_video_description(video_id: str) -> Optional[str]:
    """Description COMPLÈTE d'une vidéo via InnerTube (youtubei/v1/next → attributedDescription).

    On passe par 'next' et PAS 'player' : YouTube gate 'player' derrière un login
    (playabilityStatus=LOGIN_REQUIRED) mais laisse 'next' (panneau description) ouvert. Renvoie la
    description, ou None si la vidéo est indisponible (supprimée/privée / description vide).
    """
    response = _innertube_post("next", {"videoId": video_id})
    response.raise_for_status()
    return _dig_attributed_description(response.json())


def scrape_video_details(video_id: str) -> Optional[dict]:
    """Description COMPLÈTE + date de publication ABSOLUE via InnerTube (youtubei/v1/next). On
    utilise 'next' et PAS 'player' : YouTube exige un login sur 'player' (LOGIN_REQUIRED) mais laisse
    'next' ouvert. La description vient de attributedDescription.content, la date absolue de
    dateText.simpleText ('Aug 19, 2020' — au scrape recherche on n'a que du relatif). Renvoie
    {description, published_date}, ou None si la vidéo est indisponible."""
    response = _innertube_post("next", {"videoId": video_id})
    response.raise_for_status()
    data = response.json()
    description = _dig_attributed_description(data)
    if description is None:
        return None
    date_text = _find_key_recursive(data, "dateText")
    published = _parse_watch_date(date_text.get("simpleText")) if isinstance(date_text, dict) else None
    return {"description": description, "published_date": published}


_SUBSCRIBER_RE = re.compile(r"([\d.,]+\s*[KMB]?)\s+subscribers", re.IGNORECASE)


def scrape_channel_subscribers(channel_id: str) -> Optional[int]:
    """Nb d'abonnés d'une chaîne via InnerTube (youtubei/v1/browse → "X subscribers", arrondi
    3 chiffres sig. comme l'API depuis 2019). Renvoie l'entier, ou None si le compte est
    caché/introuvable."""
    response = _innertube_post("browse", {"browseId": channel_id})
    response.raise_for_status()
    match = _SUBSCRIBER_RE.search(response.text)
    if not match:
        return None
    return parse_view_count(match.group(1))


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
