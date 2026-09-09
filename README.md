# WordPress ingestion and clean-data pipeline

Fetches WordPress REST content, PMPro membership levels, and MEC calendar
occurrences, and performs bounded HTML crawls for sitemap-only content. Each
scheduled run preserves the source responses, normalizes and validates complete
documents, and writes clean JSONL files to local storage or Amazon S3. It does not
chunk, embed, or index documents; those remain the responsibility of the downstream
RAG pipeline.

Info Hub and Anthropedia are deliberately not fetched from `epkb-admin` or their
unavailable custom REST routes. They are discovered through both WordPress search
and the native sitemap, then fetched from their normal HTML pages.

The membership site's public help-centre link is also followed as an explicit
source. It resolves to `https://atlas.culturalinfusion.com/faq/`; the complete
server-rendered FAQ page (both tab sets, questions, and answers) is refreshed on
every run and written as `faq.jsonl`. Its crawler is unauthenticated and isolated
from the membership crawler so WordPress credentials are never sent to that host.

The crawler reads `/wp-sitemap.xml`, including the valid XML currently returned by
the site with an incorrect HTTP 404 status. Known content types remain REST-first.
Sitemap-only AI profiles, AI questions, portfolio items, taxonomy archives, and any
new sitemap types added later are acquired from normal HTML pages. Search results
and sitemap entries provide URLs only; titles and content come from those pages.

## Data layers

Every run uses the same unique `run_id` across these layers:

```text
raw/{run_id}/             Immutable source responses and ingestion manifest
raw/{run_id}/run-report.log  Operational log and timing summary
raw/latest.json           Last baseline eligible for incremental reuse
cleaned/{run_id}/         Full normalized documents as per-source JSONL
cleaned/latest.json       Atomic pointer to the latest completed clean run
quarantine/{run_id}/      Rejected records with validation errors
```

New run folders use a readable, UTC timestamp such as
`2026-09-09_14-28-32_131118_UTC`. Source filenames use words separated by
hyphens, for example `info-hub.jsonl`, `cign-events.jsonl`,
`website-sitemap.jsonl`, `mec-event-definitions.jsonl`, and
`membership-plans.jsonl`. The cleaner can
still read existing raw runs whose filenames contain underscores.

Raw objects are never changed by the cleaning stage. You can rerun cleaning for
an existing raw run after changing normalization rules. The latest pointer includes
the run status, and the manifest exposes `quality_gate_passed`; downstream systems
can reject `complete_with_errors` runs when they require a complete source set.
Incremental runs still write complete raw and cleaned snapshots, not deltas.

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
S3—`RAW_S3_BUCKET_NAME`, `CLEANED_S3_BUCKET_NAME`, and `AWS_DEFAULT_REGION`.
For backward compatibility, `S3_BUCKET_NAME` can be used for both data layers.
The file is intentionally ignored by Git.

Set `WORDPRESS_PAGE_ALLOWLIST` only when you want to restrict normal WordPress
pages to specific IDs, for example `WORDPRESS_PAGE_ALLOWLIST=123,456`. An empty
value fetches every published page. HTML crawls are bounded by
`HTML_CRAWL_MAX_PAGES` (default `5000`) and `HTML_CRAWL_MAX_SITEMAPS` (default `100`).
Redirects are bounded by
`HTML_CRAWL_MAX_REDIRECTS` (default `5`). Requests run asynchronously with bounded
`HTML_CRAWL_CONCURRENCY` (default `6`), and individual HTML responses are capped by
`HTML_CRAWL_MAX_RESPONSE_BYTES` (default `5242880`). HTTP timeouts and retries use
the existing `HTTP_TIMEOUT_SECONDS`, `HTTP_MAX_RETRIES`, and
`HTTP_RETRY_BACKOFF_SECONDS` settings.

`HTML_CRAWL_RESPECT_ROBOTS=true` is the safe default. The live site currently
declares `Crawl-delay: 30`, so crawler requests are started 30 seconds
apart even though parsing and network I/O remain asynchronous. Set
`HTML_CRAWL_MIN_REQUEST_INTERVAL_SECONDS` (default `1`) to enforce a fallback
minimum when robots rules are unavailable. When a valid robots file has no crawl
delay, a token bucket permits up to `HTML_CRAWL_BURST_SIZE` concurrent starts
(default: the configured concurrency) while preserving that average rate. A
published crawl delay—or a robots fetch failure—uses strict spacing and disables
bursts. Disable robots handling only when the site owner has explicitly authorized
a different crawl rate.

`MAX_CALENDAR_EVENTS=all` (or a blank value) fetches every MEC event and its dates.
Set it to a small positive integer for a bounded trial run. MEC IDs come from the
complete WordPress search inventory, while event content and dates come from the
native MEC detail API using bounded asynchronous requests. If a known API becomes
unavailable, its matching sitemap HTML becomes the fallback.

`INCREMENTAL_INGESTION=true` is the default. After the first complete baseline,
REST sources use WordPress `modified_after` with a five-minute safety overlap and
merge changed records into the previous immutable snapshot. HTML sources reuse a
previous document when the current sitemap `lastmod` is identical. Fetched HTML
records retain ETag and Last-Modified validators; later incremental runs use
conditional requests and copy HTTP 304 records into the new self-contained raw
snapshot. Configure the overlap with `INCREMENTAL_LOOKBACK_SECONDS` (default `300`).
A full refresh runs every `FULL_RECONCILIATION_INTERVAL_DAYS` (default `7`) to detect
REST deletions and rebuild the baseline. Disable incremental mode to force every
run to be full.

MEC event details, occurrence schedules, and membership plans are still refreshed
in full because their current APIs do not expose a trustworthy change cursor.
WordPress collection and search pages after page one are fetched asynchronously
with the same bounded `HTML_CRAWL_CONCURRENCY` setting.

HTML pages are parsed once with `lxml`, and parsing/extraction runs in worker
threads so CPU work does not block network progress. Persistent crawl workers pull
from a shared bounded frontier, so a slow response does not hold up the next batch.

Run either stage independently when needed:

```bash
# Fetch API responses only
.venv/bin/python -m app.jobs.run_ingestion

# Re-clean an existing successful raw run without fetching again
.venv/bin/python -m app.jobs.run_cleaning --run-id RUN_ID
```

## Cleaned output

Each `cleaned/{run_id}/{source}.jsonl` file contains one complete normalized
document per line. JSONL remains the dataset and metadata envelope; Markdown is
the canonical representation consumed by the downstream RAG pipeline:

```json
{
  "document_id": "research:123",
  "title": "Document title",
  "content_format": "text/markdown",
  "content_html": "<h2>Eligibility</h2><p>Students can apply...</p><ul><li>Must be enrolled</li></ul>",
  "content_text": "Eligibility\nStudents can apply...\nMust be enrolled",
  "content_markdown": "# Document title\n\n## Eligibility\n\nStudents can apply...\n\n- Must be enrolled",
  "source": "research",
  "source_id": 123,
  "content_type": "research",
  "access_class": "member_restricted",
  "url": "https://example.com/document",
  "modified_at": "2026-09-01T00:00:00",
  "run_id": "2026-09-09_14-28-32_131118_UTC",
  "metadata": {
    "canonical_url": "https://example.com/document",
    "acquisition_method": "html",
    "retrieved_at": "2026-09-02T00:00:00+00:00",
    "content_hash": "..."
  }
}
```

Cleaning removes navigation, forms, scripts, decorative SVGs, page-builder noise,
and unsafe links while preserving headings, paragraphs, ordered and unordered
lists, links, block quotes, code blocks, tables, captions, and meaningful image
alternative text. Collapsed accordion panels remain because their hidden UI state
does not make their answers irrelevant. Relative links are resolved against the
source page, so links to supporting documents such as PDFs remain usable.

The three representations intentionally serve different purposes:

- `content_html` preserves the selected source HTML for reference and future
  reprocessing.
- `content_text` is a flattened fallback useful for inspection and debugging.
- `content_markdown` is the canonical structured text for RAG.

The downstream RAG pipeline can read `cleaned/latest.json`, load the referenced
manifest, use `content_markdown`, and choose its own chunking, embedding, and
indexing strategy. This ingestion project does not chunk documents or produce
Bedrock `.md` files.

New runs produce `research.jsonl`, `info-hub.jsonl`, `anthropedia.jsonl`,
`cign-events.jsonl`, `pages.jsonl`, `faq.jsonl`, `website-sitemap.jsonl`,
`mec-event-definitions.jsonl`, `membership-plans.jsonl`, and
`calendar-occurrences.jsonl`. `website-sitemap.jsonl` holds sitemap-only and newly
discovered content types without duplicating the known REST-backed sitemap groups.

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
   needed. Replace `RAW_BUCKET_NAME` and `CLEANED_BUCKET_NAME` in
   [deploy/iam-policy.json](deploy/iam-policy.json) and attach the policy to the
   instance role. The minimum permissions are:

   - `s3:ListBucket` on both buckets
   - `s3:GetObject` on `arn:aws:s3:::RAW_BUCKET_NAME/raw/*`
   - `s3:PutObject` on the raw bucket's `raw/*`
   - `s3:PutObject` on the cleaned bucket's `cleaned/*` and `quarantine/*`
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
an inbound application port for this job. The service allows up to 18 hours for a
worst-case robots-throttled HTML fallback; the normal REST/MEC-first path is much
faster.

The crawler refreshes the sitemap index and generic WordPress search inventory on
every run, then fetches changed approved HTML concurrently while reusing documents
whose sitemap modification value is unchanged. A protected landing page can be
skipped safely when sitemap or search discovery succeeds;
access-denied pages are never stored as content. Linked external hosts, redirects,
oversized responses, malformed sitemap XML, and executable page noise are rejected.

## Verification

Run the offline test suite without contacting WordPress or AWS:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

A combined run writes raw and cleaned manifests with status `complete` or
`complete_with_errors`. Individual source failures are recorded and do not discard
successful sources. Fatal storage or configuration errors return a non-zero exit
code and attempt to write a stage-specific failure manifest.
Each raw manifest also reports per-HTML-source `discovered`, `fetched`, `accepted`,
`skipped`, and `failed` counts. Alert on `complete_with_errors`, any nonzero HTML
`failed` count, or a failed `quality_gate`. The gate is enforced in code for every
configured content source, membership plans, and calendar occurrences; a successful
HTTP response with zero records is therefore still a degraded run.

GitHub Actions runs the offline suite on Python 3.10 and 3.12, performs critical
Ruff lint checks and MyPy checks, compiles the package, and audits pinned runtime
dependencies with `pip-audit`.

The combined pipeline prints its total wall-clock time when it finishes, including
failed runs. It also writes `raw/{run_id}/run-report.log` locally or to the raw S3
bucket. The report contains timestamped application logs, stage durations, final
statuses, source counts, source failures, quality-gate results, and quarantine
counts. Operational reports stay in the raw bucket so they are not mistaken for
cleaned RAG documents.
