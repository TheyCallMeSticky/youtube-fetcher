import logging
import os

import redis
from rq import Queue, Worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL")
if not REDIS_URL:
    raise ValueError("REDIS_URL environment variable is required")

redis_conn = redis.from_url(REDIS_URL)
queue = Queue("youtube_fetch_jobs", connection=redis_conn)

if __name__ == "__main__":
    logger.info("Starting youtube-fetcher worker...")
    logger.info(f"Redis: {REDIS_URL}")
    worker = Worker(queues=[queue], connection=redis_conn)
    worker.work()
