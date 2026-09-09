import json
import logging
import tempfile
import time
from datetime import datetime, timezone


LOG_FORMAT = "%(asctime)sZ %(levelname)s %(name)s: %(message)s"


class _UtcFormatter(logging.Formatter):
    @staticmethod
    def converter(timestamp=None):
        return time.gmtime(timestamp)


class _ApplicationLogFilter(logging.Filter):
    def filter(self, record):
        return record.name == "root" or record.name.startswith("app.")


def format_duration(seconds):
    seconds = max(0.0, float(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, remaining_seconds = divmod(remainder, 60)
    return f"{int(hours):02d}:{int(minutes):02d}:{remaining_seconds:06.3f}"


class RunReport:
    """Capture application logs into a bounded-memory, uploadable run report."""

    def __init__(self, run_id, started_at=None):
        self.run_id = run_id
        self.started_at = started_at or datetime.now(timezone.utc)
        self.stream = tempfile.SpooledTemporaryFile(
            max_size=2 * 1024 * 1024,
            mode="w+",
            encoding="utf-8",
            newline="\n",
        )
        self.handler = logging.StreamHandler(self.stream)
        self.handler.setLevel(logging.INFO)
        self.handler.addFilter(_ApplicationLogFilter())
        self.handler.setFormatter(_UtcFormatter(LOG_FORMAT))
        self.root_logger = logging.getLogger()
        self.previous_root_level = self.root_logger.level

    def __enter__(self):
        self.stream.write(
            "Cultural Infusion ingestion pipeline run report\n"
            f"run_id: {self.run_id}\n"
            f"started_at: {self.started_at.isoformat()}\n\n"
        )
        if self.root_logger.getEffectiveLevel() > logging.INFO:
            self.root_logger.setLevel(logging.INFO)
        self.root_logger.addHandler(self.handler)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.root_logger.removeHandler(self.handler)
        self.root_logger.setLevel(self.previous_root_level)
        self.handler.flush()

    def append_summary(self, summary):
        self.stream.seek(0, 2)
        self.stream.write("\nRUN SUMMARY\n")
        self.stream.write(json.dumps(summary, ensure_ascii=False, indent=2))
        self.stream.write("\n")
        self.stream.flush()

    def read(self):
        self.stream.flush()
        self.stream.seek(0)
        return self.stream.read()

    def close(self):
        self.handler.close()
        self.stream.close()
