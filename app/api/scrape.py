import uuid

from fastapi import APIRouter, Depends, HTTPException
from rq import Queue

from app.core.auth import verify_api_key
from app.core.redis import redis_conn
from app.schemas.scrape import JobResponse, ScrapeRequest
from app.services import job_store
from app.services.youtube_scraper import scrape_search

router = APIRouter(
    prefix="/search",
    tags=["search"],
    dependencies=[Depends(verify_api_key)],
)

queue = Queue("youtube_fetch_jobs", connection=redis_conn)


@router.post("/scrape", response_model=JobResponse, status_code=202)
def enqueue_scrape(request: ScrapeRequest):
    job_id = str(uuid.uuid4())
    job_store.create(job_id)

    queue.enqueue(
        "app.services.jobs.process_scrape_job",
        job_id,
        request.query,
        request.max_results,
        request.format,
        job_timeout=120,
    )

    return JobResponse(job_id=job_id)


@router.post("/scrape-sync")
def scrape_sync(request: ScrapeRequest):
    """Scrape SYNCHRONE : scrape_search() en direct, résultat immédiat (ni RQ ni poll).

    Pour les appelants haut-débit (crawl niche-finder) → supprime le matraquage de polls sur le
    fetcher API. L'endpoint async /scrape (RQ) reste intact pour les autres consommateurs (yt-scorer).
    Renvoie {estimated_results, videos} ; 502 si le scrape échoue (transitoire → l'appelant re-tente).
    """
    result = scrape_search(request.query, request.max_results, request.format)
    if result is None:
        raise HTTPException(status_code=502, detail=f"YouTube scrape failed for query: {request.query}")
    return result
