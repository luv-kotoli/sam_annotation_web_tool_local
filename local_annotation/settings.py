from dataclasses import dataclass
from pathlib import Path
import os

PROJECT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    max_upload_bytes: int = 20 * 1024**3

    @classmethod
    def from_env(cls):
        data_dir = Path(os.environ.get("LOCAL_ANNOTATION_DATA_DIR", str(PROJECT / "data"))).expanduser().resolve()
        return cls(data_dir=data_dir)

    @property
    def database(self):
        return self.data_dir / "annotations.sqlite3"

    @property
    def videos(self):
        return self.data_dir / "videos"

    @property
    def temporary(self):
        return self.data_dir / "temporary"

    @property
    def models(self):
        return self.data_dir / "models"

    @property
    def frames(self):
        return self.data_dir / "frames"

    @property
    def exports(self):
        return self.data_dir / "exports"

    def prepare(self):
        for path in (self.data_dir, self.videos, self.temporary, self.models, self.frames, self.exports):
            path.mkdir(parents=True, exist_ok=True)
