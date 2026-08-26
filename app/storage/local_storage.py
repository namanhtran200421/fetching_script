import json
from pathlib import Path


class LocalStorage:

    def __init__(
        self,
        base_dir: str = "data/raw"
    ):
        self.base_dir = Path(base_dir)

    def save_source(
        self,
        run_id: str,
        source_name: str,
        items: list
    ):
        source_dir = self.base_dir / run_id

        source_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        file_path = (
            source_dir
            / f"{source_name}.json"
        )

        with open(
            file_path,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                items,
                file,
                ensure_ascii=False,
                indent=2
            )

        return file_path


    def save_manifest(
        self,
        run_id: str,
        manifest: dict
    ):
        run_dir = self.base_dir / run_id

        run_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        file_path = run_dir / "manifest.json"

        with open(
            file_path,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                manifest,
                file,
                ensure_ascii=False,
                indent=2
            )

        return file_path