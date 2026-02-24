# YouTube Fetcher

FastAPI microservice that provides centralized YouTube data access for the TypeFlick ecosystem — scraping search results, downloading thumbnails, and proxying YouTube Data API v3 with multi-key rotation.

## Responsibility

- Scrape YouTube search results (bypasses official API quotas)
- Download and base64-encode YouTube thumbnails for AI analysis
- Proxy YouTube Data API v3 with automatic key rotation on quota exhaustion
- Process long-running jobs asynchronously via RQ

## Tech Stack

| Component | Technology |
|-----------|-----------|
| **Framework** | FastAPI + Uvicorn |
| **Job Queue** | Redis Queue (RQ) |
| **HTTP Client** | httpx (async), requests (sync) |
| **Runtime** | Python 3.11-slim |

## Project Structure

```
youtube-fetcher/
├── app/
│   ├── main.py                    # FastAPI app + route registration
│   ├── api/
│   │   ├── scrape.py              # POST /search/scrape (async job)
│   │   ├── thumbnails.py          # POST /thumbnails/fetch (async job)
│   │   ├── youtube_api.py         # POST /youtube/videos, /youtube/channels (sync)
│   │   └── jobs.py                # GET /jobs/{job_id} (poll results)
│   ├── schemas/
│   │   ├── scrape.py              # ScrapeRequest, JobResponse
│   │   ├── thumbnails.py          # ThumbnailFetchRequest
│   │   ├── youtube_api.py         # VideoDescriptions/ChannelSubscribers request/response
│   │   └── jobs.py                # JobStatus model
│   ├── services/
│   │   ├── jobs.py                # RQ job entry points
│   │   ├── job_store.py           # Redis hash-based job status store
│   │   ├── youtube_scraper.py     # YouTube HTML scraper (ytInitialData)
│   │   ├── thumbnail_fetcher.py   # Async concurrent thumbnail downloader
│   │   └── youtube_api.py         # YouTube Data API v3 client (multi-key)
│   └── core/
│       ├── auth.py                # X-API-Key header validation
│       └── redis.py               # Redis connection singleton
├── cache/
│   ├── youtube/                   # API v3 response cache (JSON files by MD5)
│   └── debug-thumbnails/          # Downloaded thumbnails for inspection
├── worker.py                      # RQ worker entry point
├── Dockerfile                     # Dev image (1 worker)
├── Dockerfile.prod                # Prod image (2 workers, non-root)
├── docker-entrypoint.sh           # Fix cache permissions, drop to appuser
└── requirements.txt
```

## API Endpoints

All endpoints require `X-API-Key` header authentication (except `/health`).

### Asynchronous (Job-Based)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/search/scrape` | POST | Scrape YouTube search results (enqueue job) |
| `/thumbnails/fetch` | POST | Fetch + download YouTube thumbnails (enqueue job) |
| `/jobs/{job_id}` | GET | Poll async job status and results |

**POST /search/scrape**
```json
{
  "query": "Drake type beat",
  "max_results": 20,
  "format": "standard"  // or "tubebuddy"
}
```

**POST /thumbnails/fetch**
```json
{
  "query": "Drake type beat",
  "max_thumbnails": 20
}
```

**GET /jobs/{job_id}** response:
```json
{
  "status": "queued | running | done | failed",
  "progress": 75,
  "result": { ... },
  "error": null
}
```

### Synchronous (YouTube Data API v3)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/youtube/videos` | POST | Fetch video descriptions (batch, max 50) |
| `/youtube/channels` | POST | Fetch channel subscriber counts (batch, max 50) |
| `/health` | GET | Health check (no auth) |

## Key Services

### YouTube Scraper
- Parses `ytInitialData` from YouTube search HTML
- Supports two output formats: `standard` (snake_case, numeric views) and `tubebuddy` (PascalCase, compatible with yt-scorer)
- Uses cookies and user-agent for authentication
- Retries 3x with exponential backoff on server errors

### Thumbnail Fetcher
- Downloads up to 10 thumbnails concurrently (asyncio Semaphore)
- Auto-detects media type from file magic bytes (WebP, PNG, JPEG)
- Base64-encodes for direct API response
- Saves to disk for debugging

### YouTube Data API v3 Client
- Supports up to 12 API keys (`YOUTUBE_API_KEY_1` through `YOUTUBE_API_KEY_12`)
- Auto-rotates to next key on 403 (quota exhaustion)
- File-based response caching by MD5 hash
- MOCK mode for development without API keys

## Redis Keys

| Key Pattern | Type | TTL | Content |
|-------------|------|-----|---------|
| `yt_job:{job_id}` | Hash | 1 hour | `status`, `progress`, `result` (JSON), `error`, `job_type` |

## Development

```bash
# Via Docker
docker compose up youtube-fetcher youtube-fetcher-worker typeflick-redis

# Local
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# In another terminal:
python worker.py
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `REDIS_URL` | Yes | Redis connection string |
| `YOUTUBE_FETCHER_API_KEY` | Yes | API key for client authentication |
| `YOUTUBE_COOKIES` | Yes | YouTube session cookies (for scraping) |
| `YOUTUBE_USER_AGENT` | Yes | Browser user-agent for scraping |
| `YOUTUBE_MODE` | Yes | `LIVE` or `MOCK` (for API v3) |
| `YOUTUBE_CACHE_DIR` | No | Cache directory path (default: `/app/cache/youtube`) |
| `YOUTUBE_API_KEY_1`..`_12` | For LIVE mode | YouTube Data API v3 keys |
| `LOG_LEVEL` | No | Logging level (default: `INFO`) |

## Consumers

| Service | Endpoints Used | Purpose |
|---------|---------------|---------|
| **TypeFlick-core** | `/thumbnails/fetch`, `/jobs/{id}` | Reference thumbnails for AI analysis |
| **yt-scorer** | `/search/scrape` (tubebuddy format) | YouTube data for TubeBuddy scoring |
| **niche-finder** | `/search/scrape`, `/youtube/videos`, `/youtube/channels` | Niche analysis data |
