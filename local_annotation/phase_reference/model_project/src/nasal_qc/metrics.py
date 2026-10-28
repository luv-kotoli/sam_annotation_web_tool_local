from __future__ import annotations

import math

import numpy as np
from sklearn.metrics import accuracy_score, f1_score, precision_recall_fscore_support

from .labeling import IGNORE, interval_overlap


def classification_metrics(y_true: np.ndarray, y_pred: np.ndarray, prefix: str) -> dict[str, float]:
    mask = y_true != IGNORE
    if mask.sum() == 0:
        return {f"{prefix}/n": 0.0}
    yt = y_true[mask]
    yp = y_pred[mask]
    p, r, f1, _ = precision_recall_fscore_support(
        yt, yp, average="binary" if len(np.unique(yt)) <= 2 else "macro", zero_division=0
    )
    return {
        f"{prefix}/n": float(mask.sum()),
        f"{prefix}/accuracy": float(accuracy_score(yt, yp)),
        f"{prefix}/precision": float(p),
        f"{prefix}/recall": float(r),
        f"{prefix}/f1": float(f1),
    }


def binary_effective_metrics(y_true: np.ndarray, y_prob: np.ndarray, threshold: float, prefix: str) -> dict[str, float]:
    y_pred = (y_prob >= threshold).astype(np.int64)
    out = classification_metrics(y_true, y_pred, prefix)
    mask = y_true != IGNORE
    if mask.sum() > 0:
        p, r, f1, _ = precision_recall_fscore_support(y_true[mask], y_pred[mask], average="binary", zero_division=0)
        out.update({f"{prefix}/effective_precision": float(p), f"{prefix}/effective_recall": float(r), f"{prefix}/effective_f1": float(f1)})
    return out


def interval_iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = interval_overlap(a[0], a[1], b[0], b[1])
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union > 0 else 0.0


def segment_metrics(
    true_segments: list[tuple[float, float]],
    pred_segments: list[tuple[float, float]],
    iou_threshold: float = 0.5,
) -> dict[str, float]:
    if not true_segments and not pred_segments:
        return {
            "segment/precision": 1.0,
            "segment/recall": 1.0,
            "segment/f1": 1.0,
            "segment/mean_iou": 1.0,
            "segment/boundary_mae_sec": 0.0,
        }
    if not true_segments:
        return {
            "segment/precision": 0.0,
            "segment/recall": 1.0,
            "segment/f1": 0.0,
            "segment/mean_iou": 0.0,
            "segment/boundary_mae_sec": math.nan,
        }
    if not pred_segments:
        return {
            "segment/precision": 1.0,
            "segment/recall": 0.0,
            "segment/f1": 0.0,
            "segment/mean_iou": 0.0,
            "segment/boundary_mae_sec": math.nan,
        }

    ious = np.zeros((len(true_segments), len(pred_segments)), dtype=np.float32)
    for i, gt in enumerate(true_segments):
        for j, pr in enumerate(pred_segments):
            ious[i, j] = interval_iou(gt, pr)

    matched_true = set()
    matched_pred = set()
    boundary_errors = []
    iou_values = []
    order = np.dstack(np.unravel_index(np.argsort(-ious.ravel()), ious.shape))[0]
    for i, j in order:
        if i in matched_true or j in matched_pred:
            continue
        if ious[i, j] < iou_threshold:
            break
        matched_true.add(int(i))
        matched_pred.add(int(j))
        gt = true_segments[int(i)]
        pr = pred_segments[int(j)]
        boundary_errors.extend([abs(gt[0] - pr[0]), abs(gt[1] - pr[1])])
        iou_values.append(float(ious[i, j]))

    tp = len(matched_true)
    fp = len(pred_segments) - tp
    fn = len(true_segments) - tp
    precision = tp / (tp + fp) if tp + fp > 0 else 0.0
    recall = tp / (tp + fn) if tp + fn > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall > 0 else 0.0
    return {
        "segment/precision": float(precision),
        "segment/recall": float(recall),
        "segment/f1": float(f1),
        "segment/mean_iou": float(np.mean(iou_values)) if iou_values else 0.0,
        "segment/boundary_mae_sec": float(np.mean(boundary_errors)) if boundary_errors else math.nan,
    }

