"""Local video probing. Never invokes a shell or downloads media."""
import json
import math
from pathlib import Path
import shutil
import subprocess

from fastapi import HTTPException

EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}


def probe(path: Path):
    if not shutil.which("ffprobe"):
        raise HTTPException(503, "未找到 ffprobe，请先安装 FFmpeg 后上传视频。")
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
            capture_output=True, timeout=45, check=True,
        )
        info = json.loads(result.stdout)
        stream = next(s for s in info["streams"] if s.get("codec_type") == "video")
        duration = float(stream.get("duration") or info["format"]["duration"])
        width, height = int(stream["width"]), int(stream["height"])
        if not math.isfinite(duration) or duration <= 0 or min(width, height) <= 0:
            raise ValueError("invalid video metadata")
    except (subprocess.SubprocessError, ValueError, KeyError, StopIteration, TypeError):
        raise HTTPException(422, "文件不是可读取的视频，或缺少有效的视频时长。") from None
    return {"duration_seconds": duration, "width": width, "height": height, "codec": stream.get("codec_name", "")}
