import json
import os
from pathlib import Path
from typing import Union

from app.storage.naming import source_file_stem
from app.storage.serialization import json_line


class LocalStorage:

    def __init__(self, base_dir: Union[str, Path] = "data/raw"):
        self.base_dir = Path(base_dir)

    def _save_json(self, run_id: str, file_name: str, data):
        # Make one folder for this run.
        run_dir = self.base_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        file_path = run_dir / file_name
        temporary_path = file_path.with_suffix(file_path.suffix + ".tmp")
        with temporary_path.open("w", encoding="utf-8") as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2
            )
            file.flush()
            os.fsync(file.fileno())

        temporary_path.replace(file_path)

        return file_path

    def save_source(self, run_id: str, source_name: str, data):
        return self._save_json(
            run_id,
            f"{source_file_stem(source_name)}.json",
            data
        )

    def save_manifest(self, run_id: str, manifest: dict):
        return self._save_json(run_id, "manifest.json", manifest)

    def save_run_report(self, run_id: str, report: str):
        run_dir = self.base_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        file_path = run_dir / "run-report.log"
        temporary_path = file_path.with_suffix(".log.tmp")
        with temporary_path.open("w", encoding="utf-8", newline="\n") as file:
            file.write(report)
            file.flush()
            os.fsync(file.fileno())
        temporary_path.replace(file_path)
        return file_path

    def publish_raw_latest(self, run_id: str, manifest_path, status=None):
        return self._save_json(
            "",
            "latest.json",
            {
                "run_id": run_id,
                "manifest": str(manifest_path),
                "status": status,
            },
        )

    def _load_json(self, run_id: str, file_name: str):
        file_path = self.base_dir / run_id / file_name
        if not file_path.exists():
            raise FileNotFoundError(f"Raw object not found: {file_path}")

        with file_path.open(encoding="utf-8") as file:
            return json.load(file)

    def load_manifest(self, run_id: str):
        return self._load_json(run_id, "manifest.json")

    def load_raw_latest(self):
        try:
            return self._load_json("", "latest.json")
        except FileNotFoundError:
            return None

    def load_source(self, run_id: str, source_name: str):
        readable_name = f"{source_file_stem(source_name)}.json"
        try:
            return self._load_json(run_id, readable_name)
        except FileNotFoundError:
            # Older runs used internal underscore-separated source keys.
            return self._load_json(run_id, f"{source_name}.json")

    def _layer_storage(self, layer: str):
        return LocalStorage(self.base_dir.parent / layer)

    def save_cleaned_source(self, run_id: str, source_name: str, documents):
        return self._save_jsonl(
            "cleaned",
            run_id,
            f"{source_file_stem(source_name)}.jsonl",
            documents,
        )

    def save_quarantine(self, run_id: str, source_name: str, data):
        return self._layer_storage("quarantine")._save_json(
            run_id,
            f"{source_file_stem(source_name)}.json",
            data,
        )

    def save_cleaned_manifest(self, run_id: str, manifest: dict):
        return self._layer_storage("cleaned")._save_json(
            run_id,
            "manifest.json",
            manifest,
        )

    def publish_cleaned_latest(
        self, run_id: str, manifest_path, status=None
    ):
        pointer = {
            "run_id": run_id,
            "manifest": str(manifest_path),
            "status": status,
        }
        return self._layer_storage("cleaned")._save_json(
            "",
            "latest.json",
            pointer,
        )

    def _save_jsonl(self, layer: str, run_id: str, file_name: str, rows):
        run_dir = self.base_dir.parent / layer / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        file_path = run_dir / file_name
        temporary_path = file_path.with_suffix(file_path.suffix + ".tmp")

        with temporary_path.open("w", encoding="utf-8") as file:
            for row in rows:
                file.write(json_line(row))
                file.write("\n")
            file.flush()
            os.fsync(file.fileno())

        temporary_path.replace(file_path)
        return file_path
