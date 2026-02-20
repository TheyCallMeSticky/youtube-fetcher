import uuid

from fastapi import APIRouter, Depends
from rq import Queue

from app.core.auth import verify_api_key
from app.core.redis import redis_conn
from app.schemas.scrape import JobResponse, ScrapeRequest
from app.services import job_store

router = APIRouter(
    prefix="/search",
    tags=["search"],
    dependencies=[Depends(verify_api_key)],
)

queue = Queue("youtube_fetch_jobs", connection=redis_conn)


@router.post("/scrape", response_model=JobResponse, status_code=202)
def enqueue_scrape(request: ScrapeRequest):
    job_id = str(uuid.uuid4())
    job_store.create(job_id, "scrape")

    queue.enqueue(
        "app.services.jobs.process_scrape_job",
        job_id,
        request.query,
        request.max_results,
        request.format,
        job_timeout=120,
    )

    return JobResponse(job_id=job_id)
