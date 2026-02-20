import uuid

from fastapi import APIRouter, Depends
from rq import Queue

from app.core.auth import verify_api_key
from app.core.redis import redis_conn
from app.schemas.scrape import JobResponse
from app.schemas.thumbnails import ThumbnailFetchRequest
from app.services import job_store

router = APIRouter(
    prefix="/thumbnails",
    tags=["thumbnails"],
    dependencies=[Depends(verify_api_key)],
)

queue = Queue("youtube_fetch_jobs", connection=redis_conn)


@router.post("/fetch", response_model=JobResponse, status_code=202)
def enqueue_thumbnail_fetch(request: ThumbnailFetchRequest):
    job_id = str(uuid.uuid4())
    job_store.create(job_id, "thumbnail")

    queue.enqueue(
        "app.services.jobs.process_thumbnail_job",
        job_id,
        request.query,
        request.max_thumbnails,
        job_timeout=300,
    )

    return JobResponse(job_id=job_id)
