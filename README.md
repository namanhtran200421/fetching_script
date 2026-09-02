# WordPress ingestion and RAG preparation pipeline

Fetches configured WordPress content, PMPro membership levels, and MEC calendar
occurrences. Each scheduled run preserves the API responses, cleans and validates
documents, and creates retrieval-ready JSONL chunks in local storage or Amazon S3.

## Data layers

Every run uses the same unique `run_id` across these layers:

```text
raw/{run_id}/             Immutable API responses and ingestion manifest
curated/{run_id}/         Normalized documents and cleaning manifest
quarantine/{run_id}/      Rejected records with validation errors
rag/{run_id}/             Per-source JSONL and combined chunks.jsonl
```

Raw objects are never changed by the cleaning stage. You can rerun cleaning for
an existing raw run after changing normalization or chunking rules.

## Local run

Requires Python 3.10 or newer with `venv` support. Python 3.12 is recommended for
the EC2 deployment.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
chmod 600 .env
# Create and fill in the ignored .env, then run ingestion followed by cleaning:
.venv/bin/python -m app.jobs.run_pipeline
```

The `.env` file must provide `WORDPRESS_BASE_URL`, `WORDPRESS_USERNAME`,
`WORDPRESS_APP_PASSWORD`, `MEC_API_TOKEN`, `STORAGE_MODE`, and—when using
S3—`S3_BUCKET_NAME` and `AWS_DEFAULT_REGION`. It can also override
`MAX_CALENDAR_EVENTS`, the HTTP retry settings, `RAG_CHUNK_SIZE_CHARS`, and
`RAG_CHUNK_OVERLAP_CHARS`. The file is intentionally ignored by Git.

`MAX_CALENDAR_EVENTS=all` (or a blank value) resolves all MEC event definitions.
Set it to a small positive integer for a trial run.

Run either stage independently when needed:

```bash
# Fetch API responses only
.venv/bin/python -m app.jobs.run_ingestion

# Re-clean an existing successful raw run without fetching again
.venv/bin/python -m app.jobs.run_cleaning --run-id RUN_ID
```

## RAG output

`rag/{run_id}/chunks.jsonl` is the combined embedding/import file. Each line is
one JSON object with retrieval text and filterable metadata:

```json
{
  "chunk_id": "stable-content-hash",
  "document_id": "research:123",
  "chunk_index": 0,
  "text": "Document title\n\nParagraph-aware chunk text...",
  "title": "Document title",
  "source": "research",
  "source_id": 123,
  "content_type": "research",
  "access_class": "member_restricted",
  "url": "https://example.com/document",
  "modified_at": "2026-09-01T00:00:00",
  "run_id": "20260902_...",
  "metadata": {}
}
```

Use `text` as the embedding input. Store the other properties as vector-store
metadata so retrieval can enforce `access_class` and filter by source or content
type. Chunk IDs remain stable when the document content and chunk position are
unchanged. Configure chunking with `RAG_CHUNK_SIZE_CHARS` and
`RAG_CHUNK_OVERLAP_CHARS`.

## EC2 deployment

1. Put the repository at `/opt/fetching-script`, owned by the account that will
   run it. The supplied unit uses `ec2-user`; change `User` and `Group` to
   `ubuntu` when using an Ubuntu AMI.
2. Create the virtual environment on EC2. Do not copy the development machine's
   `.venv` directory because virtual environments are platform-specific.

   ```bash
   cd /opt/fetching-script
   python3.12 -m venv .venv
   .venv/bin/python -m pip install --upgrade pip
   .venv/bin/python -m pip install -r requirements.txt
   ```

3. Transfer the local `.env` to the instance through your secure deployment
   process, install it as `/etc/fetching-script.env`, and restrict it. Do not
   commit or copy it into a public artifact:

   ```bash
   sudo install -m 600 /secure/path/to/uploaded.env /etc/fetching-script.env
   ```

4. For S3 storage, attach an EC2 instance role. Static AWS access keys are not
   needed. Replace `BUCKET_NAME` in [deploy/iam-policy.json](deploy/iam-policy.json)
   and attach the policy to the instance role. The minimum permissions are:

   - `s3:ListBucket` on `arn:aws:s3:::BUCKET_NAME`
   - `s3:GetObject` on `arn:aws:s3:::BUCKET_NAME/raw/*`
   - `s3:PutObject` on `raw/*`, `curated/*`, `quarantine/*`, and `rag/*`
5. Install and test the service:

   ```bash
   sudo cp deploy/fetching-script.service /etc/systemd/system/
   sudo cp deploy/fetching-script.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl start fetching-script.service
   sudo journalctl -u fetching-script.service -n 100 --no-pager
   ```

6. Once the manual run succeeds, enable the daily 02:00 UTC timer:

   ```bash
   sudo systemctl enable --now fetching-script.timer
   systemctl list-timers fetching-script.timer
   ```

The instance needs outbound HTTPS access to WordPress and S3. It does not need
an inbound application port for this job.

## Verification

Run the offline test suite without contacting WordPress or AWS:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

A successful combined run returns exit code zero and writes raw, curated, and RAG
manifests with status `complete` or `complete_with_errors`. A fatal error returns
a non-zero exit code and attempts to write a stage-specific failure manifest.
