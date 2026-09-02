import hashlib


def _rag_safe_metadata(metadata):
    safe = {}
    for key, value in metadata.items():
        if isinstance(value, (str, int, float, bool)):
            safe[key] = value
        elif isinstance(value, list) and all(
            isinstance(item, (str, int, float, bool)) for item in value
        ):
            safe[key] = value
    return safe


def _preferred_break(text, start, hard_end):
    minimum = start + int((hard_end - start) * 0.6)
    for marker in ("\n\n", ". ", "\n", " "):
        position = text.rfind(marker, minimum, hard_end)
        if position != -1:
            return position + len(marker)
    return hard_end


def split_text(text, chunk_size, overlap):
    text = text.strip()
    if not text:
        return []

    chunks = []
    start = 0
    while start < len(text):
        hard_end = min(start + chunk_size, len(text))
        end = (
            hard_end
            if hard_end == len(text)
            else _preferred_break(text, start, hard_end)
        )
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        next_start = max(end - overlap, start + 1)
        while next_start < end and not text[next_start].isspace():
            next_start += 1
        start = next_start if next_start < end else end

    return chunks


def document_to_chunks(document, chunk_size, overlap):
    title = document.get("title", "").strip()
    content = document.get("content_text", "").strip()
    retrieval_text = "\n\n".join(part for part in (title, content) if part)
    text_chunks = split_text(retrieval_text, chunk_size, overlap)
    chunks = []

    for index, text in enumerate(text_chunks):
        identity = f"{document['document_id']}:{index}:{text}"
        chunk_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        chunks.append({
            "schema_version": document["schema_version"],
            "chunk_id": chunk_id,
            "document_id": document["document_id"],
            "chunk_index": index,
            "text": text,
            "title": title,
            "source": document["source"],
            "source_id": document["source_id"],
            "content_type": document["content_type"],
            "access_class": document["access_class"],
            "url": document.get("url"),
            "modified_at": document.get("modified_at"),
            "run_id": document["run_id"],
            # Vector stores commonly require flat scalar metadata. Rich nested
            # values remain available in the curated document.
            "metadata": _rag_safe_metadata(document.get("metadata", {})),
        })

    return chunks
