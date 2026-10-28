from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass

import cv2
import numpy as np


def get_video_meta(path: str | Path) -> dict:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    duration = frame_count / fps if fps > 0 else 0.0
    cap.release()
    return {
        "fps": fps,
        "frame_count": frame_count,
        "width": width,
        "height": height,
        "duration_sec": duration,
    }


@dataclass(frozen=True)
class FrameTransform:
    image_size: int
    mode: str
    crop_box: tuple[int, int, int, int] | None = None
    source_type: str = "raw"
    nonblack_ratio: float = 0.0
    timestamp_bright_ratio: float = 0.0


def read_frames_at_times(
    path: str | Path,
    times_sec: list[float],
    image_size: int,
    preprocess_cfg: dict | None = None,
) -> np.ndarray:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {path}")

    transform = build_frame_transform(path, image_size, preprocess_cfg)
    frames = []
    last_frame = None
    for t in times_sec:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, float(t)) * 1000.0)
        ok, frame = cap.read()
        if not ok or frame is None:
            if last_frame is None:
                frame = np.zeros((image_size, image_size, 3), dtype=np.uint8)
            else:
                frame = last_frame.copy()
        else:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frame = apply_frame_transform(frame, transform)
            last_frame = frame
        frames.append(frame)
    cap.release()
    return np.stack(frames, axis=0)


def build_frame_transform(path: str | Path, image_size: int, preprocess_cfg: dict | None = None) -> FrameTransform:
    cfg = preprocess_cfg or {}
    mode = str(cfg.get("mode", "raw"))
    if mode != "center_norm":
        return FrameTransform(image_size=image_size, mode="raw")

    black_threshold = int(cfg.get("black_threshold", 18))
    sample_frames = int(cfg.get("sample_frames", 12))
    probe_max_sec = float(cfg.get("probe_max_sec", 20.0))
    padding_ratio = float(cfg.get("padding_ratio", 0.06))
    min_component_area_ratio = float(cfg.get("min_component_area_ratio", 0.01))
    small_width_threshold = float(cfg.get("small_source_bbox_width_threshold", 0.72))
    timestamp_bright_threshold = int(cfg.get("timestamp_bright_threshold", 120))
    ts_h_ratio = float(cfg.get("timestamp_region_height_ratio", 0.22))
    ts_w_ratio = float(cfg.get("timestamp_region_width_ratio", 0.35))

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return FrameTransform(image_size=image_size, mode="center_norm", source_type="open_failed")
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if frame_count <= 0 or width <= 0 or height <= 0:
        cap.release()
        return FrameTransform(image_size=image_size, mode="center_norm", source_type="invalid_meta")

    max_probe_frame = min(max(0, frame_count - 1), max(0, int(round(max(1.0, fps) * probe_max_sec))))
    indices = np.linspace(0, max_probe_frame, max(1, sample_frames)).astype(np.int64).tolist()
    index_set = set(int(i) for i in indices)
    accum = np.zeros((height, width), dtype=np.uint8)
    timestamp_vals: list[float] = []
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    target_i = 0
    frame_i = 0
    max_index = max(index_set) if index_set else 0
    while target_i < len(indices) and frame_i <= max_index:
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        if frame_i < int(indices[target_i]):
            frame_i += 1
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        mask = (gray > black_threshold).astype(np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
        accum = np.maximum(accum, mask)
        th = max(1, int(round(height * ts_h_ratio)))
        tw = max(1, int(round(width * ts_w_ratio)))
        timestamp_vals.append(float((gray[:th, :tw] > timestamp_bright_threshold).mean()))
        while target_i < len(indices) and int(indices[target_i]) <= frame_i:
            target_i += 1
        frame_i += 1
    cap.release()

    if accum.sum() == 0:
        return FrameTransform(image_size=image_size, mode="center_norm", source_type="blank")

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(accum, connectivity=8)
    min_area = max(16, int(round(width * height * min_component_area_ratio)))
    best_label = -1
    best_area = 0
    for label in range(1, num_labels):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area >= min_area and area > best_area:
            best_area = area
            best_label = label
    if best_label < 0:
        best_label = int(np.argmax(stats[1:, cv2.CC_STAT_AREA]) + 1) if num_labels > 1 else -1
    if best_label < 0:
        return FrameTransform(image_size=image_size, mode="center_norm", source_type="no_component")

    x = int(stats[best_label, cv2.CC_STAT_LEFT])
    y = int(stats[best_label, cv2.CC_STAT_TOP])
    w = int(stats[best_label, cv2.CC_STAT_WIDTH])
    h = int(stats[best_label, cv2.CC_STAT_HEIGHT])
    pad = int(round(max(w, h) * padding_ratio))
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(width, x + w + pad)
    y1 = min(height, y + h + pad)
    bw_ratio = (x1 - x0) / max(1, width)
    ts_ratio = float(np.median(timestamp_vals)) if timestamp_vals else 0.0
    source_type = "small_fov_timestamp" if bw_ratio < small_width_threshold and ts_ratio > 0.002 else "large_fov"
    if bw_ratio < small_width_threshold and ts_ratio <= 0.002:
        source_type = "small_fov"
    if bw_ratio >= 0.95:
        source_type = "full_or_large_fov"
    return FrameTransform(
        image_size=image_size,
        mode="center_norm",
        crop_box=(x0, y0, x1, y1),
        source_type=source_type,
        nonblack_ratio=float(best_area / max(1, width * height)),
        timestamp_bright_ratio=ts_ratio,
    )


def apply_frame_transform(frame: np.ndarray, transform: FrameTransform) -> np.ndarray:
    if transform.mode == "center_norm" and transform.crop_box is not None:
        x0, y0, x1, y1 = transform.crop_box
        frame = frame[y0:y1, x0:x1]
        return resize_pad_square(frame, transform.image_size)
    return resize_square(frame, transform.image_size)


def frame_brightness_stats(frame: np.ndarray, black_threshold: int = 18) -> tuple[float, float]:
    """Return mean luma in [0, 1] and non-black pixel ratio for an RGB frame."""
    if frame.size == 0:
        return 0.0, 0.0
    gray = (
        0.299 * frame[:, :, 0].astype(np.float32)
        + 0.587 * frame[:, :, 1].astype(np.float32)
        + 0.114 * frame[:, :, 2].astype(np.float32)
    )
    return float(gray.mean() / 255.0), float((gray > float(black_threshold)).mean())


def brightness_weight(
    mean_luma: float,
    nonblack_ratio: float,
    cfg: dict | None = None,
) -> float:
    cfg = cfg or {}
    dark_luma = float(cfg.get("dark_luma", 0.055))
    bright_luma = float(cfg.get("bright_luma", 0.16))
    min_weight = float(cfg.get("min_weight", 0.15))
    nonblack_floor = float(cfg.get("nonblack_floor", 0.18))
    if bright_luma <= dark_luma:
        luma_score = 1.0
    else:
        luma_score = (float(mean_luma) - dark_luma) / (bright_luma - dark_luma)
    luma_score = float(np.clip(luma_score, 0.0, 1.0))
    if nonblack_floor <= 0:
        nonblack_score = 1.0
    else:
        nonblack_score = float(np.clip(float(nonblack_ratio) / nonblack_floor, 0.0, 1.0))
    return float(min_weight + (1.0 - min_weight) * luma_score * nonblack_score)


def resize_pad_square(frame: np.ndarray, image_size: int) -> np.ndarray:
    h, w = frame.shape[:2]
    if h == 0 or w == 0:
        return np.zeros((image_size, image_size, 3), dtype=np.uint8)
    side = max(h, w)
    pad = np.zeros((side, side, 3), dtype=np.uint8)
    y0 = (side - h) // 2
    x0 = (side - w) // 2
    pad[y0 : y0 + h, x0 : x0 + w] = frame
    return cv2.resize(pad, (image_size, image_size), interpolation=cv2.INTER_AREA)


def resize_square(frame: np.ndarray, image_size: int) -> np.ndarray:
    h, w = frame.shape[:2]
    if h == 0 or w == 0:
        return np.zeros((image_size, image_size, 3), dtype=np.uint8)
    scale = image_size / min(h, w)
    new_w = int(round(w * scale))
    new_h = int(round(h * scale))
    resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
    y0 = max(0, (new_h - image_size) // 2)
    x0 = max(0, (new_w - image_size) // 2)
    cropped = resized[y0 : y0 + image_size, x0 : x0 + image_size]
    if cropped.shape[0] != image_size or cropped.shape[1] != image_size:
        pad = np.zeros((image_size, image_size, 3), dtype=np.uint8)
        pad[: cropped.shape[0], : cropped.shape[1]] = cropped
        cropped = pad
    return cropped


def sample_times(start: float, end: float, frames_per_clip: int) -> list[float]:
    if frames_per_clip <= 1:
        return [(start + end) / 2.0]
    eps = min(0.05, max(0.0, (end - start) / 20.0))
    return np.linspace(start + eps, max(start + eps, end - eps), frames_per_clip).tolist()
