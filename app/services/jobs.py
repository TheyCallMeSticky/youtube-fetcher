"""RQ job functions executed by the worker.

Each function receives a job_id + parameters, processes the task,
and stores the result in Redis via job_store.
"""

import logging

from app.services import job_store
from app.services.thumbnail_fetcher import fetch_thumbnails
from app.services.youtube_scraper import scrape_search

logger = logging.getLogger(__name__)


def process_scrape_job(job_id: str, query: str, max_results: int, output_format: str) -> None:
    try:
        job_store.update_progress(job_id, 10)

        result = scrape_search(query, max_results, output_format)

        if result is None:
            job_store.fail(job_id, f"YouTube scrape failed for query: {query}")
            return

        job_store.complete(job_id, {
            "success": True,
            "estimated_results": result["estimated_results"],
            "videos": result["videos"],
        })

    except Exception as e:
        logger.exception(f"Scrape job {job_id} failed")
        job_store.fail(job_id, str(e))


def process_thumbnail_job(job_id: str, query: str, max_thumbnails: int) -> None:
    try:
        job_store.update_progress(job_id, 10)

        result = fetch_thumbnails(query, max_thumbnails)

        if result is None:
            job_store.fail(job_id, f"Thumbnail fetch failed for query: {query}")
            return

        job_store.complete(job_id, result)

    except Exception as e:
        logger.exception(f"Thumbnail job {job_id} failed")
        job_store.fail(job_id, str(e))
