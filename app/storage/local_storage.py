import json
import os
from pathlib import Path


class LocalStorage:

    def __init__(self, base_dir: str = "data/raw"):
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
            f"{source_name}.json",
            data
        )

    def save_manifest(self, run_id: str, manifest: dict):
        return self._save_json(run_id, "manifest.json", manifest)
