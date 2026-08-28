"""Endpoints synchrones pour descriptions de vidéos + abonnés de chaînes.

Stratégie hybride : API Data v3 en premier (rapide, batchée). Quand le quota est épuisé
(YOUTUBE_QUOTA_EXCEEDED), on BASCULE sur le scraping (mêmes données : descriptions complètes,
abonnés arrondis à 3 chiffres sig. comme l'API) → jamais de blocage. Le scraping ne coûte
qu'en requêtes (pas de batch), donc réservé au fallback.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import verify_api_key
from app.core.redis import redis_conn
from app.schemas.youtube_api import (
    ChannelSubscribersRequest,
    ChannelSubscribersResponse,
    VideoDescriptionsRequest,
    VideoDescriptionsResponse,
)
from app.services.youtube_api import YouTubeDataAPI
from app.services.youtube_scraper import (
    scrape_channel_subscribers,
    scrape_video_description,
    scrape_video_details,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/youtube",
    tags=["youtube-api"],
    dependencies=[Depends(verify_api_key)],
)

_youtube_api = YouTubeDataAPI()

_SCRAPE_WORKERS = 6                      # parallélisme du fallback (1 page par item, sinon lent)
_SUBS_CACHE_TTL = 7 * 24 * 3600         # 7 j — abonnés stables + grosse réutilisation des chaînes


def _is_quota_exceeded(err: Exception) -> bool:
    return "YOUTUBE_QUOTA_EXCEEDED" in str(err)


def _safe_map(fn, items: list) -> list:
    """pool.map robuste : un item qui LÈVE (vidéo/chaîne morte, browse en erreur) → None au lieu
    de crasher toute la batch (sinon 1 mauvais id fait échouer les 50). Le None = 'pas de donnée'
    (l'appelant l'ignore / marque l'entité comme traitée sans data)."""
    def one(x):
        try:
            return fn(x)
        except Exception:
            return None
    with ThreadPoolExecutor(max_workers=_SCRAPE_WORKERS) as pool:
        return list(pool.map(one, items))


def _map_avec_erreurs(fn, items: list) -> tuple[dict, list]:
    """Comme _safe_map, mais SÉPARE « pas de donnée » de « ça a raté ».

    La distinction est vitale pour l'appelant qui tient une file de travail : une vidéo réellement
    sans description est traitée une fois pour toutes, une vidéo qui a pris un 403 doit être
    retentée. Confondre les deux revient à marquer un échec comme un succès définitif — mesuré :
    une vidéo sur deux perdue silencieusement sous forte concurrence, liens de vente compris."""
    resultats, erreurs = {}, []

    def un(x):
        try:
            return x, fn(x), None
        except Exception as erreur:
            return x, None, erreur

    with ThreadPoolExecutor(max_workers=_SCRAPE_WORKERS) as pool:
        for item, valeur, erreur in pool.map(un, items):
            if erreur is not None:
                erreurs.append(item)
            elif valeur is not None:
                resultats[item] = valeur
    return resultats, erreurs


def _scrape_descriptions(video_ids: list[str]) -> dict:
    """Fallback : descriptions complètes via InnerTube (youtubei/v1/player, parallélisé)."""
    results = _safe_map(scrape_video_description, video_ids)
    return {vid: {"description": d} for vid, d in zip(video_ids, results) if d is not None}


def _scrape_subscribers(channel_ids: list[str]) -> tuple[dict, list]:
    """Abonnés via scraping des pages chaîne, avec cache Redis par chaîne (les gros producteurs
    reviennent sur des milliers de niches → quasi que du cache). Rend aussi ce qui a échoué."""
    out: dict = {}
    missing: list[str] = []
    for cid in channel_ids:
        cached = redis_conn.get(f"subs:{cid}")
        if cached is not None:
            out[cid] = int(cached)
        else:
            missing.append(cid)

    scraped, erreurs = _map_avec_erreurs(scrape_channel_subscribers, missing)
    for cid, subs in scraped.items():
        redis_conn.set(f"subs:{cid}", subs, ex=_SUBS_CACHE_TTL)
        out[cid] = subs
    return out, erreurs


@router.post("/videos", response_model=VideoDescriptionsResponse)
def get_video_descriptions(request: VideoDescriptionsRequest):
    """Descriptions complètes : API d'abord, scraping si quota épuisé."""
    try:
        descriptions = _youtube_api.get_video_descriptions(request.video_ids)
    except Exception as e:
        if not _is_quota_exceeded(e):
            raise HTTPException(status_code=502, detail=f"YouTube API error: {e}")
        logger.warning(f"Quota API épuisé → scraping de {len(request.video_ids)} descriptions")
        descriptions = _scrape_descriptions(request.video_ids)
    return VideoDescriptionsResponse(descriptions=descriptions)


@router.post("/channels", response_model=ChannelSubscribersResponse)
def get_channel_subscribers(request: ChannelSubscribersRequest):
    """Abonnés : API d'abord, scraping (+ cache) si quota épuisé."""
    try:
        subscribers = _youtube_api.get_channel_subscribers(request.channel_ids)
    except Exception as e:
        if not _is_quota_exceeded(e):
            raise HTTPException(status_code=502, detail=f"YouTube API error: {e}")
        logger.warning(f"Quota API épuisé → scraping de {len(request.channel_ids)} chaînes")
        subscribers, _ = _scrape_subscribers(request.channel_ids)
    return ChannelSubscribersResponse(subscribers=subscribers)


# ───── Endpoints SCRAPE-DIRECT (pas d'API) : pour l'enrichissement Phase 2 en volume ─────

@router.post("/videos/details")
def scrape_videos_details(request: VideoDescriptionsRequest):
    """Descriptions + date absolue + vues par SCRAPE direct (InnerTube, sans quota).

    Renvoie {details: {video_id: {...}}, errors: [video_id]}. `errors` liste ce qui a ÉCHOUÉ ;
    une vidéo absente des deux est vivante mais sans description. L'appelant retente les
    premières et classe les secondes."""
    details, erreurs = _map_avec_erreurs(scrape_video_details, request.video_ids)
    return {"details": details, "errors": erreurs}


@router.post("/channels/subs")
def scrape_channels_subs(request: ChannelSubscribersRequest):
    """Abonnés par SCRAPE direct (InnerTube + cache Redis, sans quota).

    Renvoie {subscribers: {channel_id: n}, errors: [channel_id]} — même contrat que
    /videos/details : ce qui a échoué est nommé, pas confondu avec un compte caché."""
    subscribers, erreurs = _scrape_subscribers(request.channel_ids)
    return {"subscribers": subscribers, "errors": erreurs}
