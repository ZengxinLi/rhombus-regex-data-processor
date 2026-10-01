# Regex Data Processor

An end-to-end, asynchronous web application for applying natural-language data-cleaning requests to CSV and Excel files in a user-owned Amazon S3 bucket. The browser is a React/Vite app; Django exposes the API and job records; Celery and Redis run heavy work outside the request path; PySpark executes transformations and persists the full output as Parquet.

## Live demo

The deployed application is available at [https://34-9-23-197.sslip.io](https://34-9-23-197.sslip.io).

## Demo video

[Watch the 36-second step-by-step public-deployment walkthrough](demo/regex-data-processor-demo.mp4). It covers S3 connection, supported-file selection, natural-language email redaction, asynchronous job submission, and the completed result. The credential fields shown in the first frame are intentionally blank.

Reviewers can test it with a bucket they control and a least-privilege IAM key that permits only `s3:ListBucket` and `s3:GetObject` for that bucket. The app uses HTTPS; submitted credentials are encrypted in short-lived Redis storage and are never saved in a job record.

## Start locally

Docker Desktop is the only prerequisite.

```bash
docker compose up --build
```

Open [http://localhost:5173](http://localhost:5173). The production-built Nginx frontend proxies `/api` to Django, so the browser does not need direct access to port 8000.

For production, copy `.env.example` to `.env`, set a long `DJANGO_SECRET_KEY`, add the production host/origin values, and optionally set `OPENAI_API_KEY`. Docker Compose has safe development defaults so it can start without an `.env` file.

```bash
cp .env.example .env
docker compose --profile observability up --build
```

The optional Flower worker dashboard is then at [http://localhost:5555](http://localhost:5555).

## What it does

1. Enter an AWS access key, secret key, optional session token, region, and bucket name.
2. The API validates access and shows only CSV, XLSX, and XLS objects in that bucket.
3. Pick a file, inspect its column header, choose target columns, and describe the desired change. For example: `Find email addresses`, replacement `REDACTED`.
4. The submit endpoint immediately returns a job id. The UI polls job status and supports cancellation.
5. A Celery worker downloads the selected object to a temporary worker directory, loads it into a Spark DataFrame, resolves and validates the rule, repartitions the DataFrame, and executes the Spark transformation.
6. The complete output is written as partitioned Parquet under the shared application data volume. The UI pages through a bounded 500-row preview, so a browser never receives a million-row payload.

The default local resolver supports email, phone, URL, IP-address and number matching, plus whitespace normalization, lowercase, uppercase and trim. Set `OPENAI_API_KEY` to make arbitrary natural-language matching requests use the configured OpenAI model. Every rule resolution is cached in Redis for 24 hours.

## Architecture

```text
React browser
  |  S3 connection / job submit / status polling / result pages
  v
Django API ---- Redis cache (encrypted short-lived S3 connection; LLM rule cache)
  |                    |
  |                    +---- Celery broker + result backend
  v
Job metadata                    v
                         Celery worker --> PySpark DataFrame --> Parquet result volume
                                |
                                +--> user-owned Amazon S3 (source file only)
```

The database stores job metadata only: bucket/key, requested columns, prompt, job state, safe resolved regex, progress and result location. It does **not** store AWS access keys, secret keys, session tokens or plaintext credentials. The browser gets only a cryptographically random connection id. That id maps to a Fernet-encrypted Redis entry with a 30-minute expiry. Credentials are also excluded from error messages and logs.

## API

| Endpoint | Purpose |
| --- | --- |
| `POST /api/s3/connect/` | Validate S3 details and list supported files; returns opaque `connection_id` |
| `POST /api/s3/inspect/` | Read a selected file's header and return columns |
| `POST /api/jobs/` | Queue a Spark processing job; returns `202` immediately |
| `GET /api/jobs/{id}/` | Read current status and progress |
| `POST /api/jobs/{id}/cancel/` | Mark a queued/running job cancelled and revoke its task |
| `GET /api/jobs/{id}/results/?page=1&page_size=25` | Read a paginated, bounded result preview |

## Operations and safety

- Regex is compiled and checked before Spark runs it. Backreferences, lookbehind/recursive constructs, oversized expressions, and common nested quantifiers such as `(.*)*` are rejected to reduce regex denial-of-service risk.
- User-provided replacement values are treated literally; Spark capture substitutions are not permitted accidentally.
- CSV input is read as strings, preserving identifiers with leading zeroes. Excel input is converted to a Spark DataFrame before processing.
- The worker partitions using `ceil(rows / SPARK_ROWS_PER_PARTITION)`, capped by `SPARK_SHUFFLE_PARTITIONS`. Defaults are 250,000 rows/partition and 16 partitions. Set `SPARK_MASTER` to a Spark cluster URL for a distributed deployment.
- A per-worker Celery concurrency of one avoids overcommitting memory to multiple Spark drivers. Scale workers or use a remote Spark cluster after profiling the data shape.
- The local Compose stack uses SQLite for simple development. Use PostgreSQL, authenticated users, TLS, a managed Redis instance, encrypted volumes/object storage and a retention policy before exposing it publicly.

## Test

Run the focused backend checks from the backend directory:

```bash
pytest
```

They cover local rule resolution, unsafe regex rejection, the opaque connection response, the guarantee that a queued job does not persist an S3 secret, and the Spark replacement/whitespace pipeline.

## Production deployment notes

This repository is deliberately ready to containerize. On a Google Compute Engine demo VM, copy `.env.example` to `.env`, replace `PUBLIC_IP` in the allowed-host/origin settings with the VM's external IP, set `FRONTEND_BIND=127.0.0.1:5173`, and set `PUBLIC_HOST` to a DNS name resolving to that IP. Then run `docker compose -f docker-compose.yml -f docker-compose.gcp.yml up -d --build`. Caddy supplies HTTPS and proxies to the Nginx frontend, which in turn proxies `/api` to Django; port 8000 remains bound to the VM loopback interface. For a production deployment, add authentication, point `SPARK_MASTER` at your Spark cluster, use PostgreSQL for job metadata, and mount or replace `app_data` with durable encrypted result storage. Restrict the AWS IAM policy to the exact source bucket and actions required (`s3:ListBucket`, `s3:GetObject`); use a separate result bucket/prefix if outputs need to be retained or downloaded outside the app.
