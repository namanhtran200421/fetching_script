import argparse
import logging

from app.cleaning.pipeline import CleaningPipeline
from app.config import (
    LOCAL_DATA_DIR,
    RAG_CHUNK_OVERLAP_CHARS,
    RAG_CHUNK_SIZE_CHARS,
    S3_BUCKET_NAME,
    STORAGE_MODE,
    validate_cleaning_config,
)
from app.storage.local_storage import LocalStorage
from app.storage.s3_storage import S3Storage


def build_storage():
    if STORAGE_MODE == "local":
        return LocalStorage(LOCAL_DATA_DIR)

    if STORAGE_MODE == "s3":
        storage = S3Storage(S3_BUCKET_NAME)
        storage.validate_connection()
        return storage

    raise ValueError("STORAGE_MODE must be 'local' or 's3'")


def run(run_id: str, storage=None):
    validate_cleaning_config()
    storage = storage or build_storage()
    pipeline = CleaningPipeline(
        storage,
        chunk_size=RAG_CHUNK_SIZE_CHARS,
        chunk_overlap=RAG_CHUNK_OVERLAP_CHARS,
    )
    return pipeline.clean_run(run_id)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Clean a completed raw ingestion run and create RAG chunks"
    )
    parser.add_argument("--run-id", required=True)
    arguments = parser.parse_args()
    run(arguments.run_id)
