import logging
import time
from datetime import datetime, timezone

from app.jobs.run_cleaning import run as run_cleaning
from app.jobs.run_ingestion import build_storage, run as run_ingestion
from app.jobs.run_report import RunReport, format_duration


LOGGER = logging.getLogger(__name__)


def run(storage=None):
    started_at = datetime.now(timezone.utc)
    started_clock = time.monotonic()
    run_id = started_at.strftime("%Y-%m-%d_%H-%M-%S_%f_UTC")
    report = RunReport(run_id, started_at)
    active_storage = storage
    raw_manifest = None
    cleaned_manifest = None
    ingestion_seconds = None
    cleaning_seconds = None
    pipeline_status = "failed"
    failure_in_progress = False

    try:
        with report:
            try:
                LOGGER.info("Starting pipeline run %s", run_id)
                active_storage = active_storage or build_storage()

                stage_started = time.monotonic()
                raw_manifest = run_ingestion(
                    storage=active_storage,
                    started_at=started_at,
                    run_id=run_id,
                )
                ingestion_seconds = time.monotonic() - stage_started
                LOGGER.info(
                    "Ingestion stage finished in %s with status %s",
                    format_duration(ingestion_seconds),
                    raw_manifest["status"],
                )
                if raw_manifest["status"] not in {
                    "complete",
                    "complete_with_errors",
                }:
                    raise RuntimeError(
                        f"Raw ingestion failed: {raw_manifest['status']}"
                    )

                if raw_manifest["run_id"] != run_id:
                    raise RuntimeError(
                        "Ingestion returned a different run id than the "
                        "pipeline assigned"
                    )
                stage_started = time.monotonic()
                cleaned_manifest = run_cleaning(
                    run_id,
                    storage=active_storage,
                )
                cleaning_seconds = time.monotonic() - stage_started
                if cleaned_manifest["run_id"] != run_id:
                    raise RuntimeError(
                        "Cleaning returned a different run id than ingestion"
                    )
                pipeline_status = (
                    "complete_with_errors"
                    if "complete_with_errors" in {
                        raw_manifest["status"],
                        cleaned_manifest["status"],
                    }
                    else cleaned_manifest["status"]
                )
                LOGGER.info(
                    "Cleaning stage finished in %s with status %s",
                    format_duration(cleaning_seconds),
                    pipeline_status,
                )
                LOGGER.info("Pipeline run %s finished", run_id)
            except BaseException:
                failure_in_progress = True
                LOGGER.exception("Pipeline run %s failed", run_id)
                raise
    finally:
        completed_at = datetime.now(timezone.utc)
        total_seconds = time.monotonic() - started_clock
        summary = {
            "run_id": run_id,
            "status": pipeline_status,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "total_duration_seconds": round(total_seconds, 3),
            "total_duration": format_duration(total_seconds),
            "ingestion_duration_seconds": (
                round(ingestion_seconds, 3)
                if ingestion_seconds is not None else None
            ),
            "cleaning_duration_seconds": (
                round(cleaning_seconds, 3)
                if cleaning_seconds is not None else None
            ),
            "raw_status": (
                raw_manifest.get("status") if raw_manifest else None
            ),
            "cleaned_status": (
                cleaned_manifest.get("status") if cleaned_manifest else None
            ),
            "raw_source_counts": (
                raw_manifest.get("source_counts", {}) if raw_manifest else {}
            ),
            "cleaned_source_counts": (
                cleaned_manifest.get("source_counts", {})
                if cleaned_manifest else {}
            ),
            "source_failures": (
                raw_manifest.get("source_failures", []) if raw_manifest else []
            ),
            "quality_gate": (
                raw_manifest.get("quality_gate") if raw_manifest else None
            ),
            "quarantine_counts": (
                cleaned_manifest.get("quarantine_counts", {})
                if cleaned_manifest else {}
            ),
        }
        report.append_summary(summary)
        print(
            f"Pipeline run {run_id} finished with status {pipeline_status}. "
            f"Total run time: {format_duration(total_seconds)}",
            flush=True,
        )

        try:
            save_report = getattr(active_storage, "save_run_report", None)
            if save_report is not None:
                report_path = save_report(run_id, report.read())
                print(f"Run report: {report_path}", flush=True)
        except Exception:
            logging.exception("Could not save pipeline run report")
            if not failure_in_progress:
                raise
        finally:
            report.close()

    return cleaned_manifest


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run()
