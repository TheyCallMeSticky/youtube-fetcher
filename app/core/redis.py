import os

import redis

REDIS_URL = os.getenv("REDIS_URL")
if not REDIS_URL:
    raise ValueError("REDIS_URL environment variable is required")

redis_conn = redis.from_url(REDIS_URL)
