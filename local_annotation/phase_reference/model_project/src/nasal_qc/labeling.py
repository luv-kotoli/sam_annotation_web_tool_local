from __future__ import annotations

from collections import Counter

import numpy as np
import pandas as pd


EFFECTIVE = "effective_observation"
INSERTION = "insertion"
IGNORE = -100


QUALITY_NAMES = {
    0: "non_effective",
    1: "low_quality",
    2: "readable",
    3: "high_quality",
}

SIDE_NAMES = {
    0: "none_or_unknown",
    1: "left",
    2: "right",
}
SIDE_TO_ID = {
    "left": 1,
    "right": 2,
}


def interval_overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def assign_clip_label(
    clip_start: float,
    clip_end: float,
    segments: pd.DataFrame,
    overlap_threshold: float,
    include_unannotated_as_negative: bool = False,
) -> tuple[int, int, str, float]:
    effective, quality, phase, overlap, _ = assign_clip_annotation(
        clip_start,
        clip_end,
        segments,
        overlap_threshold,
        include_unannotated_as_negative,
    )
    return effective, quality, phase, overlap


def assign_clip_annotation(
    clip_start: float,
    clip_end: float,
    segments: pd.DataFrame,
    overlap_threshold: float,
    include_unannotated_as_negative: bool = False,
) -> tuple[int, int, str, float, int]:
    clip_len = max(1e-6, clip_end - clip_start)
    best = None
    best_ratio = 0.0
    for row in segments.itertuples(index=False):
        ov = interval_overlap(clip_start, clip_end, float(row.start_sec), float(row.end_sec))
        ratio = ov / clip_len
        if ratio > best_ratio:
            best_ratio = ratio
            best = row

    if best is None or best_ratio < overlap_threshold:
        if include_unannotated_as_negative:
            return 0, 0, "unannotated_negative", best_ratio, 0
        return IGNORE, IGNORE, "ignore_unannotated", best_ratio, IGNORE

    side = SIDE_TO_ID.get(str(getattr(best, "visible_side", "")).strip().lower(), 0)
    phase = str(best.phase_label)
    if phase == EFFECTIVE:
        quality = int(best.quality_level) if not pd.isna(best.quality_level) else 2
        quality = min(3, max(1, quality))
        return 1, quality, phase, best_ratio, side
    if phase == INSERTION:
        return 0, 0, phase, best_ratio, side
    return 0, 0, phase, best_ratio, side


def majority_label(values: list[int], ignore_value: int = IGNORE) -> int:
    kept = [int(v) for v in values if int(v) != ignore_value]
    if not kept:
        return ignore_value
    return Counter(kept).most_common(1)[0][0]


def labels_to_segments(times: np.ndarray, labels: np.ndarray, threshold_label: int = 1) -> list[tuple[float, float]]:
    if len(times) == 0:
        return []
    segments = []
    in_seg = False
    start = 0.0
    step = float(np.median(np.diff(times))) if len(times) > 1 else 1.0
    for t, y in zip(times, labels):
        if int(y) == threshold_label and not in_seg:
            start = float(t)
            in_seg = True
        elif int(y) != threshold_label and in_seg:
            segments.append((start, float(t)))
            in_seg = False
    if in_seg:
        segments.append((start, float(times[-1] + step)))
    return segments


def postprocess_binary(
    times: np.ndarray,
    probs: np.ndarray,
    threshold: float,
    min_segment_sec: float,
    merge_gap_sec: float,
) -> tuple[np.ndarray, list[tuple[float, float]]]:
    raw = (probs >= threshold).astype(np.int64)
    segments = labels_to_segments(times, raw, 1)
    if merge_gap_sec > 0 and segments:
        merged = [segments[0]]
        for s, e in segments[1:]:
            ps, pe = merged[-1]
            if s - pe <= merge_gap_sec:
                merged[-1] = (ps, e)
            else:
                merged.append((s, e))
        segments = merged
    segments = [(s, e) for s, e in segments if e - s >= min_segment_sec]
    clean = np.zeros_like(raw)
    for s, e in segments:
        clean[(times >= s) & (times < e)] = 1
    return clean, segments
