"""Redis-backed job status store for tracking RQ job progress.

Each job is stored as a Redis hash: yt_job:{job_id}
Fields: status, progress, result (JSON), error, job_type
TTL: 1 hour after completion.
"""

import json
import logging
from typing import Any, Optional

from app.core.redis import redis_conn

logger = logging.getLogger(__name__)

JOB_KEY_PREFIX = "yt_job:"
JOB_TTL_SECONDS = 3600


def _key(job_id: str) -> str:
    return f"{JOB_KEY_PREFIX}{job_id}"


def create(job_id: str, job_type: str) -> None:
    redis_conn.hset(_key(job_id), mapping={
        "status": "queued",
        "progress": 0,
        "job_type": job_type,
    })


def update_progress(job_id: str, progress: int) -> None:
    redis_conn.hset(_key(job_id), mapping={
        "status": "running",
        "progress": progress,
    })


def complete(job_id: str, result: Any) -> None:
    key = _key(job_id)
    redis_conn.hset(key, mapping={
        "status": "done",
        "progress": 100,
        "result": json.dumps(result),
    })
    redis_conn.expire(key, JOB_TTL_SECONDS)


def fail(job_id: str, error: str) -> None:
    key = _key(job_id)
    redis_conn.hset(key, mapping={
        "status": "failed",
        "progress": 0,
        "error": error,
    })
    redis_conn.expire(key, JOB_TTL_SECONDS)


def get_status(job_id: str) -> Optional[dict]:
    key = _key(job_id)
    data = redis_conn.hgetall(key)
    if not data:
        return None

    status = {
        "status": data.get(b"status", b"unknown").decode(),
        "progress": int(data.get(b"progress", b"0")),
    }

    result_raw = data.get(b"result")
    if result_raw:
        status["result"] = json.loads(result_raw)

    error_raw = data.get(b"error")
    if error_raw:
        status["error"] = error_raw.decode()

    return status
