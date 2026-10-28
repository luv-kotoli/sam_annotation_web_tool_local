from dataclasses import dataclass
from pathlib import Path


class InputError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    model_project: Path
    checkpoint: Path
    backbone: Path
    max_duration_sec: float = 14400
    max_pixels: int = 3840 * 2160
    cnn_batch_clips: int = 2
