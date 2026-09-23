import io
import unittest
import zlib

from pypdf import PdfWriter

from app.cleaning.normalizers import normalize_media_document
from app.cleaning.validators import validate_document
from app.clients.media_client import MediaClient
from app.ingestion.pdf_text import PdfTextUnavailable, extract_pdf_text
from app.ingestion.source_registry import SOURCES


def build_pdf(page_texts):
    """Build a minimal multi-page PDF carrying a real text layer."""
    objects = []
    # 1: catalog, 2: page tree, 3: font, then one page and one content
    # stream per page. The font must not share an id with a stream.
    font_id = 3
    page_ids = [4 + index * 2 for index in range(len(page_texts))]

    objects.append((1, "<< /Type /Catalog /Pages 2 0 R >>", None))
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects.append((
        2,
        f"<< /Type /Pages /Count {len(page_texts)} /Kids [{kids}] >>",
        None,
    ))
    objects.append((
        font_id,
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        None,
    ))
    for index, text in enumerate(page_texts):
        page_id = page_ids[index]
        stream_id = page_id + 1
        escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1")
        objects.append((
            page_id,
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {stream_id} 0 R >>",
            None,
        ))
        objects.append((stream_id, f"<< /Length {len(stream)} >>", stream))

    objects.sort()
    output = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for object_id, body, stream in objects:
        offsets[object_id] = len(output)
        output += f"{object_id} 0 obj\n{body}\n".encode("latin-1")
        if stream is not None:
            output += b"stream\n" + stream + b"\nendstream\n"
        output += b"endobj\n"

    start_xref = len(output)
    highest = max(offsets) + 1
    output += f"xref\n0 {highest}\n".encode("latin-1")
    output += b"0000000000 65535 f \n"
    for object_id in range(1, highest):
        offset = offsets.get(object_id, 0)
        output += f"{offset:010d} 00000 n \n".encode("latin-1")
    output += (
        f"trailer\n<< /Size {highest} /Root 1 0 R >>\n"
        f"startxref\n{start_xref}\n%%EOF\n"
    ).encode("latin-1")
    return bytes(output)


class PdfTextTests(unittest.TestCase):
    def test_extracts_text_from_every_page(self):
        sentence = "Cultural diversity reporting for the 2026 benchmark study. "
        data = build_pdf([sentence * 4, sentence * 4])

        text, page_count, warnings = extract_pdf_text(data)

        self.assertEqual(page_count, 2)
        self.assertIn("Cultural diversity reporting", text)
        self.assertEqual(warnings, [])

    def test_page_limit_truncates_and_warns(self):
        sentence = "Diversity Atlas methodology and benchmark appendix notes. "
        data = build_pdf([sentence * 5, sentence * 5, sentence * 5])

        text, page_count, warnings = extract_pdf_text(data, max_pages=1)

        self.assertEqual(page_count, 3)
        self.assertTrue(text)
        self.assertEqual(warnings, ["truncated to the first 1 of 3 pages"])

    def test_image_only_pdf_is_rejected_rather_than_emptied(self):
        # A scanned page has no text layer. Producing an empty document would
        # silently poison the corpus, so this must fail loudly instead.
        data = build_pdf(["x"])

        with self.assertRaises(PdfTextUnavailable) as raised:
            extract_pdf_text(data)

        self.assertIn("OCR", str(raised.exception))

    def test_password_protected_pdf_is_rejected(self):
        sentence = "Confidential benchmark results for member organisations. "
        writer = PdfWriter(clone_from=io.BytesIO(build_pdf([sentence * 5])))
        writer.encrypt("a-real-password")
        buffer = io.BytesIO()
        writer.write(buffer)

        with self.assertRaises(PdfTextUnavailable) as raised:
            extract_pdf_text(buffer.getvalue())

        self.assertIn("password", str(raised.exception))

    def test_unparsable_bytes_are_rejected(self):
        with self.assertRaises(PdfTextUnavailable):
            extract_pdf_text(zlib.compress(b"not a pdf"))

    def test_empty_content_is_rejected(self):
        with self.assertRaises(PdfTextUnavailable):
            extract_pdf_text(b"")


class FakeResponse:
    def __init__(self, content):
        self.content = content


class FakeAsyncHttpClient:
    def __init__(self, bodies, **kwargs):
        self.bodies = bodies
        self.requested = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return None

    async def get(self, url, **kwargs):
        self.requested.append((url, kwargs))
        return FakeResponse(self.bodies[url])


class ListingClient:
    def __init__(self, items):
        self.items = items
        self.calls = []

    def get_all(self, **kwargs):
        self.calls.append(kwargs)
        return list(self.items)


class MediaClientTests(unittest.TestCase):
    base_url = "https://atlas.example.test"

    def build_client(self, items, bodies, **kwargs):
        clients = []

        def factory(**factory_kwargs):
            client = FakeAsyncHttpClient(bodies, **factory_kwargs)
            clients.append(client)
            return client

        client = MediaClient(
            base_url=self.base_url,
            wordpress_client=ListingClient(items),
            async_http_client_factory=factory,
            **kwargs,
        )
        return client, clients

    def test_listing_filters_by_mime_type(self):
        client, _ = self.build_client([{"id": 1}], {})

        client.list_documents(fields="id,source_url")

        # Attachments are status "inherit", not "publish".
        self.assertEqual(
            client.wordpress_client.calls[0]["extra_params"],
            {"mime_type": "application/pdf", "status": "inherit"},
        )

    def test_reads_and_extracts_an_approved_attachment(self):
        sentence = "Workplace inclusion benchmark findings and methodology. "
        url = f"{self.base_url}/report.pdf"
        attachment = {
            "id": 5,
            "mime_type": "application/pdf",
            "source_url": url,
        }
        client, _ = self.build_client(
            [attachment], {url: build_pdf([sentence * 5])}
        )

        result = client.read_documents([attachment])

        self.assertEqual(result["failures"], [])
        document = result["documents"][0]
        self.assertIn("Workplace inclusion benchmark", document["content_text"])
        self.assertEqual(document["page_count"], 1)
        self.assertEqual(document["acquisition_method"], "media_files")

    def test_attachment_on_another_host_is_refused(self):
        attachment = {
            "id": 6,
            "mime_type": "application/pdf",
            "source_url": "https://elsewhere.example.test/report.pdf",
        }
        client, http_clients = self.build_client([attachment], {})

        result = client.read_documents([attachment])

        self.assertEqual(result["documents"], [])
        self.assertIn("outside the approved host", result["failures"][0]["error"])
        # The file was never requested.
        self.assertEqual(http_clients[0].requested, [])

    def test_oversized_attachment_is_refused_before_download(self):
        attachment = {
            "id": 7,
            "mime_type": "application/pdf",
            "source_url": f"{self.base_url}/huge.pdf",
            "media_details": {"filesize": 50 * 1024 * 1024},
        }
        client, http_clients = self.build_client(
            [attachment], {}, max_response_bytes=1024 * 1024
        )

        result = client.read_documents([attachment])

        self.assertIn("over the", result["failures"][0]["error"])
        self.assertEqual(http_clients[0].requested, [])

    def test_oversized_body_is_refused_when_the_declared_size_was_wrong(self):
        url = f"{self.base_url}/understated.pdf"
        attachment = {
            "id": 8,
            "mime_type": "application/pdf",
            "source_url": url,
            "media_details": {"filesize": 10},
        }
        client, _ = self.build_client(
            [attachment],
            {url: b"x" * 5000},
            max_response_bytes=1024,
        )

        result = client.read_documents([attachment])

        self.assertEqual(result["documents"], [])
        self.assertIn("body is 5000 bytes", result["failures"][0]["error"])

    def test_no_attachments_needs_no_event_loop(self):
        client, _ = self.build_client([], {})

        self.assertEqual(
            client.read_documents([]), {"documents": [], "failures": []}
        )


class MediaNormalizerTests(unittest.TestCase):
    config = SOURCES["atlas_media_documents"]

    def test_normalized_document_passes_validation(self):
        document = normalize_media_document(
            {
                "id": 62528,
                "title": {"rendered": "Detecting Bias in AI"},
                "mime_type": "application/pdf",
                "source_url": (
                    "https://atlas.example.test/uploads/detecting-bias.pdf"
                ),
                "link": "https://atlas.example.test/detecting-bias/",
                "caption": {"rendered": "<p>Published research paper</p>"},
                "alt_text": "",
                "date_gmt": "2026-03-01T00:00:00",
                "modified_gmt": "2026-03-02T00:00:00",
                "content_text": "Abstract\n\nThis paper examines bias.",
                "page_count": 28,
                "byte_size": 1005837,
                "extraction_warnings": [],
            },
            "atlas_media_documents",
            self.config,
            "run-1",
        )

        self.assertEqual(validate_document(document), [])
        self.assertEqual(document["document_id"], "atlas_media_documents:62528")
        self.assertEqual(document["access_class"], "public")
        self.assertEqual(document["content_type"], "public_document")
        self.assertEqual(
            document["url"],
            "https://atlas.example.test/uploads/detecting-bias.pdf",
        )
        self.assertEqual(document["content_html"], "")
        self.assertTrue(
            document["content_markdown"].startswith("# Detecting Bias in AI")
        )
        self.assertIn("This paper examines bias.", document["content_markdown"])
        self.assertEqual(document["metadata"]["page_count"], 28)
        self.assertEqual(document["metadata"]["file_name"], "detecting-bias.pdf")
        self.assertEqual(
            document["metadata"]["caption"], "Published research paper"
        )

    def test_document_without_extracted_text_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_media_document(
                {
                    "id": 1,
                    "title": {"rendered": "Empty"},
                    "source_url": "https://atlas.example.test/empty.pdf",
                    "content_text": "",
                },
                "atlas_media_documents",
                self.config,
                "run-1",
            )


if __name__ == "__main__":
    unittest.main()
