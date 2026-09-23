"""Fetch published document attachments and read their text.

Attachments are listed through the ordinary WordPress media collection, then
downloaded with bounded concurrency. Only the extracted text is kept: the raw
layer stores a readable document rather than tens of megabytes of base64, and a
later cleaning run can be repeated without downloading the files again.
"""

import asyncio
import logging
from urllib.parse import urlsplit

from app.clients.http_client import AsyncRetryingHttpClient
from app.ingestion.pdf_text import PdfTextUnavailable, extract_pdf_text


LOGGER = logging.getLogger(__name__)
PDF_MIME_TYPE = "application/pdf"


class MediaClient:
    """List and read the document attachments published on one origin."""

    def __init__(
        self,
        base_url: str,
        wordpress_client,
        mime_types=(PDF_MIME_TYPE,),
        max_response_bytes=25 * 1024 * 1024,
        max_pdf_pages=500,
        async_concurrency=6,
        timeout=50,
        max_retries=3,
        backoff_seconds=1,
        async_http_client_factory=AsyncRetryingHttpClient,
        user_agent=(
            "CulturalInfusionAtlasIngestion/1.0 "
            "(+https://culturalinfusion.com)"
        ),
    ):
        self.base_url = base_url.rstrip("/")
        # The listing client carries this origin's credential policy. A public
        # origin is given an unauthenticated client, so nothing here can send
        # membership credentials to it.
        self.wordpress_client = wordpress_client
        self.mime_types = tuple(mime_types)
        self.max_response_bytes = max_response_bytes
        self.max_pdf_pages = max_pdf_pages
        self.async_concurrency = async_concurrency
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.async_http_client_factory = async_http_client_factory
        self.headers = {"User-Agent": user_agent, "Accept": "*/*"}

    def list_documents(self, fields=None, modified_after=None):
        """Return the attachment metadata for every configured media type."""
        attachments = []
        seen = set()
        for mime_type in self.mime_types:
            for item in self.wordpress_client.get_all(
                endpoint="media",
                fields=fields,
                modified_after=modified_after,
                # Attachments carry the post status "inherit"; the
                # collection default of "publish" is rejected outright.
                extra_params={
                    "mime_type": mime_type,
                    "status": "inherit",
                },
            ):
                if not isinstance(item, dict):
                    continue
                identity = item.get("id") or item.get("source_url")
                if identity in seen:
                    continue
                seen.add(identity)
                attachments.append(item)
        return attachments

    def _declared_size(self, attachment):
        details = attachment.get("media_details")
        if not isinstance(details, dict):
            return None
        size = details.get("filesize")
        return size if isinstance(size, int) else None

    def _approved_url(self, attachment):
        """Reject any attachment served from a different origin."""
        source_url = attachment.get("source_url")
        if not isinstance(source_url, str) or not source_url.strip():
            raise ValueError("attachment is missing its source_url")
        parsed = urlsplit(source_url)
        approved = urlsplit(self.base_url)
        if parsed.scheme.lower() not in {"http", "https"}:
            raise ValueError("attachment URL is not HTTP(S)")
        if (parsed.hostname or "").lower() != (approved.hostname or "").lower():
            raise ValueError("attachment URL is outside the approved host")
        return source_url

    def _extract(self, attachment, content):
        mime_type = (attachment.get("mime_type") or "").lower()
        if mime_type != PDF_MIME_TYPE:
            raise ValueError(f"unsupported attachment type: {mime_type!r}")
        return extract_pdf_text(content, max_pages=self.max_pdf_pages)

    async def _read_documents_async(self, attachments):
        semaphore = asyncio.Semaphore(self.async_concurrency)
        async with self.async_http_client_factory(
            timeout=self.timeout,
            max_retries=self.max_retries,
            backoff_seconds=self.backoff_seconds,
            max_connections=self.async_concurrency,
        ) as http_client:

            async def read_one(attachment):
                attachment_id = attachment.get("id")
                try:
                    source_url = self._approved_url(attachment)
                    declared_size = self._declared_size(attachment)
                    if (
                        declared_size is not None
                        and declared_size > self.max_response_bytes
                    ):
                        raise ValueError(
                            f"attachment is {declared_size} bytes, over the "
                            f"{self.max_response_bytes} byte limit"
                        )
                    async with semaphore:
                        response = await http_client.get(
                            source_url,
                            headers=self.headers,
                            follow_redirects=True,
                        )
                    content = response.content
                    # The declared size can be stale or absent, so the real
                    # body is checked again before it reaches the parser.
                    if len(content) > self.max_response_bytes:
                        raise ValueError(
                            f"attachment body is {len(content)} bytes, over "
                            f"the {self.max_response_bytes} byte limit"
                        )
                    text, page_count, warnings = await asyncio.to_thread(
                        self._extract, attachment, content
                    )
                    return {
                        **attachment,
                        "content_text": text,
                        "page_count": page_count,
                        "byte_size": len(content),
                        "extraction_warnings": warnings,
                        "acquisition_method": "media_files",
                    }, None
                except (PdfTextUnavailable, ValueError, OSError) as error:
                    return None, {
                        "attachment_id": attachment_id,
                        "error": f"{type(error).__name__}: {error}",
                    }
                except Exception as error:
                    return None, {
                        "attachment_id": attachment_id,
                        "error": f"{type(error).__name__}: {error}",
                    }

            records = []
            failures = []
            batch_size = max(10, self.async_concurrency * 5)
            for offset in range(0, len(attachments), batch_size):
                batch = attachments[offset:offset + batch_size]
                for record, failure in await asyncio.gather(*(
                    read_one(item) for item in batch
                )):
                    if record is not None:
                        records.append(record)
                    if failure is not None:
                        failures.append(failure)
                LOGGER.info(
                    "Read media documents %d/%d",
                    min(offset + len(batch), len(attachments)),
                    len(attachments),
                )
        return {"documents": records, "failures": failures}

    def read_documents(self, attachments):
        if not attachments:
            return {"documents": [], "failures": []}
        return asyncio.run(self._read_documents_async(attachments))
