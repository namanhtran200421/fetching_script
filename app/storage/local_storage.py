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

    def _load_json(self, run_id: str, file_name: str):
        file_path = self.base_dir / run_id / file_name
        if not file_path.exists():
            raise FileNotFoundError(f"Raw object not found: {file_path}")

        with file_path.open(encoding="utf-8") as file:
            return json.load(file)

    def load_manifest(self, run_id: str):
        return self._load_json(run_id, "manifest.json")

    def load_source(self, run_id: str, source_name: str):
        return self._load_json(run_id, f"{source_name}.json")

    def _layer_storage(self, layer: str):
        return LocalStorage(self.base_dir.parent / layer)

    def save_curated_source(self, run_id: str, source_name: str, data):
        return self._layer_storage("curated")._save_json(
            run_id,
            f"{source_name}.json",
            data,
        )

    def save_quarantine(self, run_id: str, source_name: str, data):
        return self._layer_storage("quarantine")._save_json(
            run_id,
            f"{source_name}.json",
            data,
        )

    def save_curated_manifest(self, run_id: str, manifest: dict):
        return self._layer_storage("curated")._save_json(
            run_id,
            "manifest.json",
            manifest,
        )

    def _save_jsonl(self, layer: str, run_id: str, file_name: str, rows):
        run_dir = self.base_dir.parent / layer / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        file_path = run_dir / file_name
        temporary_path = file_path.with_suffix(file_path.suffix + ".tmp")

        with temporary_path.open("w", encoding="utf-8") as file:
            for row in rows:
                file.write(json.dumps(row, ensure_ascii=False))
                file.write("\n")
            file.flush()
            os.fsync(file.fileno())

        temporary_path.replace(file_path)
        return file_path

    def save_rag_chunks(self, run_id: str, source_name: str, chunks):
        return self._save_jsonl(
            "rag",
            run_id,
            f"{source_name}.jsonl",
            chunks,
        )

    def save_rag_manifest(self, run_id: str, manifest: dict):
        return self._layer_storage("rag")._save_json(
            run_id,
            "manifest.json",
            manifest,
        )
