import logging

from app.jobs.run_cleaning import run as run_cleaning
from app.jobs.run_ingestion import run as run_ingestion


def run():
    raw_manifest = run_ingestion()
    if raw_manifest["status"] not in {"complete", "complete_with_errors"}:
        raise RuntimeError(f"Raw ingestion failed: {raw_manifest['status']}")

    return run_cleaning(raw_manifest["run_id"])


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run()
