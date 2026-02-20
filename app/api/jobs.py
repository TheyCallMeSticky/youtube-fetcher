from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import verify_api_key
from app.schemas.jobs import JobStatus
from app.services import job_store

router = APIRouter(
    prefix="/jobs",
    tags=["jobs"],
    dependencies=[Depends(verify_api_key)],
)


@router.get("/{job_id}", response_model=JobStatus)
def get_job_status(job_id: str):
    status = job_store.get_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="Job not found")
    return status
