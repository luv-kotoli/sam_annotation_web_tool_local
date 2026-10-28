#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np
import pandas as pd
import torch
from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nasal_qc.config import load_config
from nasal_qc.features import build_resnet18_feature_extractor, normalize_frames
from nasal_qc.labeling import QUALITY_NAMES, SIDE_NAMES, postprocess_binary
from nasal_qc.models import TemporalTransformerSegmenter
from nasal_qc.video import apply_frame_transform, brightness_weight, build_frame_transform, frame_brightness_stats, get_video_meta, sample_times


VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".m4v"}


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/default.yaml")
    ap.add_argument("--input-dir", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--checkpoint", default=None)
    ap.add_argument("--gpus", default="", help="Comma-separated CUDA device indices after CUDA_VISIBLE_DEVICES remapping.")
    ap.add_argument("--batch-size", type=int, default=None)
    ap.add_argument("--recursive", action="store_true", default=True)
    ap.add_argument("--no-recursive", dest="recursive", action="store_false")
    ap.add_argument("--preview-frames", action="store_true", default=True)
    ap.add_argument("--no-preview-frames", dest="preview_frames", action="store_false")
    ap.add_argument("--max-preview-per-video", type=int, default=6)
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    videos = discover_videos(Path(args.input_dir), args.recursive)
    if not videos:
        raise RuntimeError(f"No videos found under {args.input_dir}")

    gpus = parse_gpus(args.gpus)
    if not torch.cuda.is_available():
        gpus = []
    if not gpus:
        run_worker(0, None, videos, cfg, args, out_dir)
        worker_clip_files = [out_dir / "worker_0_clip_predictions.csv"]
        worker_seg_files = [out_dir / "worker_0_predicted_segments.csv"]
        worker_manifest_files = [out_dir / "worker_0_manifest.csv"]
    else:
        worker_clip_files = []
        worker_seg_files = []
        worker_manifest_files = []
        ctx = torch.multiprocessing.get_context("spawn")
        procs = []
        shards = shard_list(videos, len(gpus))
        for rank, (gpu, shard) in enumerate(zip(gpus, shards)):
            p = ctx.Process(target=run_worker, args=(rank, gpu, shard, cfg, args, out_dir))
            p.start()
            procs.append(p)
            worker_clip_files.append(out_dir / f"worker_{rank}_clip_predictions.csv")
            worker_seg_files.append(out_dir / f"worker_{rank}_predicted_segments.csv")
            worker_manifest_files.append(out_dir / f"worker_{rank}_manifest.csv")
        failed = []
        for p in procs:
            p.join()
            if p.exitcode != 0:
                failed.append(p.exitcode)
        if failed:
            raise RuntimeError(f"Some workers failed: {failed}")

    merge_csv(worker_clip_files, out_dir / "all_clip_predictions.csv")
    merge_csv(worker_seg_files, out_dir / "all_predicted_segments.csv")
    merge_csv(worker_manifest_files, out_dir / "video_manifest.csv")
    write_summary(out_dir, len(videos), cfg, args)
    write_ffmpeg_commands(out_dir / "all_predicted_segments.csv", out_dir / "cut_effective_segments.sh")
    cleanup_worker_files(worker_clip_files + worker_seg_files + worker_manifest_files)
    print(f"output_dir={out_dir}")
    print(f"videos={len(videos)}")


def discover_videos(input_dir: Path, recursive: bool) -> list[Path]:
    pattern = "**/*" if recursive else "*"
    return sorted([p for p in input_dir.glob(pattern) if p.is_file() and p.suffix.lower() in VIDEO_EXTS])


def parse_gpus(text: str) -> list[int]:
    if not text.strip():
        return []
    return [int(x) for x in text.split(",") if x.strip()]


def shard_list(items: list[Path], n: int) -> list[list[Path]]:
    shards = [[] for _ in range(n)]
    for i, item in enumerate(items):
        shards[i % n].append(item)
    return shards


@torch.inference_mode()
def run_worker(rank: int, gpu: int | None, videos: list[Path], cfg: dict, args: argparse.Namespace, out_dir: Path) -> None:
    if gpu is None:
        device = torch.device("cpu")
    else:
        torch.cuda.set_device(gpu)
        device = torch.device(f"cuda:{gpu}")

    clip_dir = out_dir / "per_video" / "clip_predictions"
    seg_dir = out_dir / "per_video" / "predicted_segments"
    preview_dir = out_dir / "preview_frames"
    clip_dir.mkdir(parents=True, exist_ok=True)
    seg_dir.mkdir(parents=True, exist_ok=True)
    if args.preview_frames:
        preview_dir.mkdir(parents=True, exist_ok=True)

    if args.checkpoint:
        ckpt_path = Path(args.checkpoint)
    else:
        latest = Path(cfg["project"]["output_dir"]) / "latest_run.txt"
        ckpt_path = Path(latest.read_text().strip()) / "checkpoints" / "best.pt"
    cnn, _ = build_resnet18_feature_extractor(bool(cfg["features"].get("pretrained", True)))
    cnn.to(device).eval()
    model = TemporalTransformerSegmenter(**cfg["model"]).to(device).eval()
    ckpt = torch.load(ckpt_path, map_location=device, weights_only=False)
    model.load_state_dict(ckpt["model"])

    all_clip_rows = []
    all_seg_rows = []
    manifest_rows = []
    iterator = tqdm(videos, desc=f"worker{rank}", position=rank, leave=True)
    for video_path in iterator:
        t0 = time.time()
        safe = safe_name(video_path.stem)
        try:
            result = infer_one_video(video_path, cfg, cnn, model, device, args.batch_size)
            clip_df = result["clip_df"]
            seg_df = result["seg_df"]
            clip_df.insert(0, "video_path", str(video_path))
            clip_df.insert(0, "video_name", video_path.name)
            seg_df.insert(0, "video_path", str(video_path))
            seg_df.insert(0, "video_name", video_path.name)
            clip_df.to_csv(clip_dir / f"{safe}_clip_predictions.csv", index=False)
            seg_df.to_csv(seg_dir / f"{safe}_predicted_segments.csv", index=False)
            if args.preview_frames and not seg_df.empty:
                write_segment_previews(video_path, seg_df, preview_dir, safe, args.max_preview_per_video)
            all_clip_rows.append(clip_df)
            all_seg_rows.append(seg_df)
            manifest_rows.append({
                "video_name": video_path.name,
                "video_path": str(video_path),
                "status": "ok",
                "duration_sec": result["meta"]["duration_sec"],
                "fps": result["meta"]["fps"],
                "frame_count": result["meta"]["frame_count"],
                "num_clips": len(clip_df),
                "num_predicted_segments": len(seg_df),
                "predicted_effective_sec": float(seg_df["pred_duration_sec"].sum()) if not seg_df.empty else 0.0,
                "retention_ratio": (float(seg_df["pred_duration_sec"].sum()) / result["meta"]["duration_sec"]) if result["meta"]["duration_sec"] > 0 and not seg_df.empty else 0.0,
                "worker_rank": rank,
                "device": str(device),
                "elapsed_sec": round(time.time() - t0, 3),
            })
        except Exception as exc:
            manifest_rows.append({
                "video_name": video_path.name,
                "video_path": str(video_path),
                "status": f"error: {exc}",
                "duration_sec": math.nan,
                "fps": math.nan,
                "frame_count": math.nan,
                "num_clips": 0,
                "num_predicted_segments": 0,
                "predicted_effective_sec": 0.0,
                "retention_ratio": 0.0,
                "worker_rank": rank,
                "device": str(device),
                "elapsed_sec": round(time.time() - t0, 3),
            })
    pd.concat(all_clip_rows, ignore_index=True).to_csv(out_dir / f"worker_{rank}_clip_predictions.csv", index=False) if all_clip_rows else pd.DataFrame().to_csv(out_dir / f"worker_{rank}_clip_predictions.csv", index=False)
    pd.concat(all_seg_rows, ignore_index=True).to_csv(out_dir / f"worker_{rank}_predicted_segments.csv", index=False) if all_seg_rows else pd.DataFrame().to_csv(out_dir / f"worker_{rank}_predicted_segments.csv", index=False)
    pd.DataFrame(manifest_rows).to_csv(out_dir / f"worker_{rank}_manifest.csv", index=False)


@torch.inference_mode()
def infer_one_video(
    video_path: Path,
    cfg: dict,
    cnn: torch.nn.Module,
    model: TemporalTransformerSegmenter,
    device: torch.device,
    batch_size: int | None,
) -> dict:
    meta = get_video_meta(video_path)
    clip_sec = float(cfg["data"]["clip_sec"])
    stride = float(cfg["data"]["clip_stride_sec"])
    frames_per_clip = int(cfg["features"]["frames_per_clip"])
    image_size = int(cfg["features"]["image_size"])
    batch_size = int(batch_size or cfg["features"]["batch_size"])
    n = max(1, int(np.ceil(max(0.0, meta["duration_sec"] - clip_sec) / stride)) + 1)
    clip_df = pd.DataFrame(
        {
            "clip_index": np.arange(n, dtype=np.int64),
            "start_sec": [round(i * stride, 3) for i in range(n)],
        }
    )
    clip_df["end_sec"] = np.minimum(clip_df["start_sec"] + clip_sec, float(meta["duration_sec"])).round(3)
    features, brightness_df = extract_features_sequential(
        video_path,
        clip_df,
        meta["fps"],
        frames_per_clip,
        image_size,
        batch_size,
        cnn,
        device,
        cfg["features"].get("preprocess"),
        cfg["features"].get("brightness_prior", {}),
    )
    logits_eff, logits_quality, logits_side = predict_windowed(model, features, int(cfg["model"].get("max_len", 256)), device)
    probs_raw = torch.softmax(logits_eff, dim=-1)[:, 1].cpu().numpy()
    quality_pred = torch.argmax(logits_quality, dim=-1).cpu().numpy()
    if logits_side is not None:
        side_prob_all = torch.softmax(logits_side, dim=-1).cpu().numpy()
        side_pred = side_prob_all.argmax(axis=-1)
        none_class = int(cfg["eval"].get("side_gate", {}).get("none_class", 0))
        nasal_side_prob = 1.0 - side_prob_all[:, none_class]
        if bool(cfg["eval"].get("side_gate", {}).get("enabled", False)):
            probs = probs_raw * np.power(nasal_side_prob, float(cfg["eval"].get("side_gate", {}).get("power", 1.0)))
        else:
            probs = probs_raw
    else:
        side_pred = np.full_like(quality_pred, -100)
        nasal_side_prob = np.ones_like(probs_raw)
        probs = probs_raw
    brightness_values = brightness_df["brightness_weight"].to_numpy(dtype=np.float32) if not brightness_df.empty else np.ones_like(probs_raw)
    if bool(cfg["eval"].get("brightness_gate", {}).get("enabled", False)):
        gate_power = float(cfg["eval"].get("brightness_gate", {}).get("power", 1.0))
        min_scale = float(cfg["eval"].get("brightness_gate", {}).get("min_prob_scale", 0.0))
        probs = probs * np.maximum(min_scale, np.power(np.clip(brightness_values, 0.0, 1.0), gate_power))
    pred, segments = postprocess_binary(
        clip_df["start_sec"].to_numpy(dtype=np.float32),
        probs,
        float(cfg["eval"]["threshold"]),
        float(cfg["eval"]["min_segment_sec"]),
        float(cfg["eval"]["merge_gap_sec"]),
    )
    segments = clip_segments_to_duration(segments, float(meta["duration_sec"]), float(cfg["eval"]["min_segment_sec"]))
    clip_df["effective_prob_raw"] = probs_raw
    clip_df["nasal_side_prob"] = nasal_side_prob
    clip_df["brightness_weight"] = brightness_values
    if not brightness_df.empty:
        clip_df["mean_luma"] = brightness_df["mean_luma"].to_numpy(dtype=np.float32)
        clip_df["nonblack_ratio"] = brightness_df["nonblack_ratio"].to_numpy(dtype=np.float32)
    clip_df["effective_prob"] = probs
    clip_df["effective_pred"] = pred
    clip_df["quality_pred"] = quality_pred
    clip_df["quality_text"] = [QUALITY_NAMES.get(int(q), str(q)) for q in quality_pred]
    clip_df["side_pred"] = side_pred
    clip_df["side_text"] = [SIDE_NAMES.get(int(s), str(s)) if int(s) != -100 else "ignore" for s in side_pred]

    seg_rows = []
    for i, (s, e) in enumerate(segments):
        in_seg = (clip_df["start_sec"] >= s) & (clip_df["start_sec"] < e)
        mean_prob = float(clip_df.loc[in_seg, "effective_prob"].mean()) if in_seg.any() else math.nan
        quality_mode = int(clip_df.loc[in_seg, "quality_pred"].mode().iloc[0]) if in_seg.any() else -1
        side_mode = int(clip_df.loc[in_seg, "side_pred"].mode().iloc[0]) if in_seg.any() else -100
        seg_rows.append({
            "segment_index": i,
            "pred_start_sec": round(float(s), 3),
            "pred_end_sec": round(float(e), 3),
            "pred_duration_sec": round(float(e - s), 3),
            "mean_effective_prob": mean_prob,
            "quality_mode": quality_mode,
            "quality_text": QUALITY_NAMES.get(quality_mode, str(quality_mode)),
            "side_mode": side_mode,
            "side_text": SIDE_NAMES.get(side_mode, str(side_mode)) if side_mode != -100 else "ignore",
            "mean_nasal_side_prob": float(clip_df.loc[in_seg, "nasal_side_prob"].mean()) if in_seg.any() else math.nan,
            "mean_brightness_weight": float(clip_df.loc[in_seg, "brightness_weight"].mean()) if in_seg.any() else math.nan,
            "mean_luma": float(clip_df.loc[in_seg, "mean_luma"].mean()) if in_seg.any() and "mean_luma" in clip_df else math.nan,
            "mean_nonblack_ratio": float(clip_df.loc[in_seg, "nonblack_ratio"].mean()) if in_seg.any() and "nonblack_ratio" in clip_df else math.nan,
        })
    return {"meta": meta, "clip_df": clip_df, "seg_df": pd.DataFrame(seg_rows)}


def clip_segments_to_duration(
    segments: list[tuple[float, float]],
    duration_sec: float,
    min_segment_sec: float,
) -> list[tuple[float, float]]:
    clipped = []
    for start, end in segments:
        s = max(0.0, float(start))
        e = min(float(end), float(duration_sec))
        if e - s >= float(min_segment_sec):
            clipped.append((s, e))
    return clipped


@torch.inference_mode()
def extract_features_sequential(
    video_path: Path,
    clip_df: pd.DataFrame,
    fps: float,
    frames_per_clip: int,
    image_size: int,
    batch_size: int,
    cnn: torch.nn.Module,
    device: torch.device,
    preprocess_cfg: dict | None = None,
    brightness_cfg: dict | None = None,
) -> tuple[torch.Tensor, pd.DataFrame]:
    if fps <= 0:
        fps = 25.0
    rows = list(clip_df.itertuples(index=False))
    targets: list[tuple[int, int, int]] = []
    for clip_pos, row in enumerate(rows):
        times = sample_times(float(row.start_sec), float(row.end_sec), frames_per_clip)
        for frame_pos, t in enumerate(times):
            targets.append((max(0, int(round(t * fps))), clip_pos, frame_pos))
    targets.sort(key=lambda x: x[0])
    out = [[None for _ in range(frames_per_clip)] for _ in rows]
    luma = [[None for _ in range(frames_per_clip)] for _ in rows]
    nonblack = [[None for _ in range(frames_per_clip)] for _ in rows]
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
    transform = build_frame_transform(video_path, image_size, preprocess_cfg)
    target_i = 0
    frame_i = 0
    last = np.zeros((image_size, image_size, 3), dtype=np.uint8)
    while target_i < len(targets):
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        frame_rgb = apply_frame_transform(frame_rgb, transform)
        frame_luma, frame_nonblack = frame_brightness_stats(
            frame_rgb,
            int((preprocess_cfg or {}).get("black_threshold", 18)),
        )
        last = frame_rgb
        last_luma = frame_luma
        last_nonblack = frame_nonblack
        while target_i < len(targets) and targets[target_i][0] <= frame_i:
            _, clip_pos, frame_pos = targets[target_i]
            out[clip_pos][frame_pos] = frame_rgb
            luma[clip_pos][frame_pos] = frame_luma
            nonblack[clip_pos][frame_pos] = frame_nonblack
            target_i += 1
        frame_i += 1
    cap.release()
    while target_i < len(targets):
        _, clip_pos, frame_pos = targets[target_i]
        out[clip_pos][frame_pos] = last
        luma[clip_pos][frame_pos] = last_luma if "last_luma" in locals() else 0.0
        nonblack[clip_pos][frame_pos] = last_nonblack if "last_nonblack" in locals() else 0.0
        target_i += 1

    features = []
    pending = []
    stats = []
    brightness_cfg = brightness_cfg or {}
    for frames, luma_vals, nonblack_vals in zip(out, luma, nonblack):
        filled = [f if f is not None else last for f in frames]
        lv = [float(v if v is not None else 0.0) for v in luma_vals]
        nb = [float(v if v is not None else 0.0) for v in nonblack_vals]
        frame_weights = [brightness_weight(a, b, brightness_cfg) for a, b in zip(lv, nb)]
        stats.append(
            {
                "mean_luma": float(np.mean(lv)) if lv else 0.0,
                "nonblack_ratio": float(np.mean(nb)) if nb else 0.0,
                "brightness_weight": brightness_weight(float(np.mean(lv)) if lv else 0.0, float(np.mean(nb)) if nb else 0.0, brightness_cfg),
                "frame_brightness_weight": frame_weights,
            }
        )
        arr = np.stack(filled, axis=0)
        x = torch.from_numpy(arr).permute(0, 3, 1, 2).float().div_(255.0)
        pending.append(x)
        if len(pending) >= batch_size:
            features.append(run_cnn_batch(cnn, pending, device, stats[-len(pending):], frames_per_clip, brightness_cfg))
            pending = []
    if pending:
        features.append(run_cnn_batch(cnn, pending, device, stats[-len(pending):], frames_per_clip, brightness_cfg))
    stat_df = pd.DataFrame([{k: v for k, v in s.items() if k != "frame_brightness_weight"} for s in stats])
    return (torch.cat(features, dim=0).cpu() if features else torch.empty((0, int(512))), stat_df)


@torch.inference_mode()
def run_cnn_batch(
    cnn: torch.nn.Module,
    clips: list[torch.Tensor],
    device: torch.device,
    clip_stats: list[dict] | None = None,
    frames_per_clip: int = 8,
    brightness_cfg: dict | None = None,
) -> torch.Tensor:
    x = torch.stack(clips, dim=0)
    b, f, c, h, w = x.shape
    x = x.view(b * f, c, h, w).to(device, non_blocking=True)
    x = normalize_frames(x)
    y = cnn(x).view(b, f, -1)
    use_weighted = bool((brightness_cfg or {}).get("enabled", False)) and bool(
        (brightness_cfg or {}).get("use_frame_weighted_features", False)
    )
    if use_weighted and clip_stats:
        frame_w = torch.tensor(
            [s.get("frame_brightness_weight", [1.0] * frames_per_clip) for s in clip_stats],
            dtype=torch.float32,
            device=y.device,
        ).clamp_min(1e-4)
        y = (y * frame_w.unsqueeze(-1)).sum(dim=1) / frame_w.sum(dim=1, keepdim=True).clamp_min(1e-4)
    else:
        y = y.mean(dim=1)
    return y.detach().cpu()


@torch.inference_mode()
def predict_windowed(model: TemporalTransformerSegmenter, features: torch.Tensor, max_len: int, device: torch.device) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor | None]:
    n = features.shape[0]
    if n <= max_len:
        f = features.unsqueeze(0).to(device)
        padding = torch.zeros((1, n), dtype=torch.bool, device=device)
        out = model(f, padding)
        side = out.get("side_logits")
        return out["effective_logits"][0].detach().cpu(), out["quality_logits"][0].detach().cpu(), side[0].detach().cpu() if side is not None else None

    stride = max_len // 2
    eff_sum = torch.zeros((n, 2), dtype=torch.float32)
    qual_sum = torch.zeros((n, 4), dtype=torch.float32)
    side_classes = int(getattr(model, "side_num_classes", 0))
    side_sum = torch.zeros((n, side_classes), dtype=torch.float32) if side_classes > 0 else None
    count = torch.zeros((n, 1), dtype=torch.float32)
    starts = list(range(0, n, stride))
    if starts[-1] + max_len < n:
        starts.append(n - max_len)
    for s in starts:
        e = min(n, s + max_len)
        f = features[s:e].unsqueeze(0).to(device)
        padding = torch.zeros((1, e - s), dtype=torch.bool, device=device)
        out = model(f, padding)
        eff = out["effective_logits"][0].detach().cpu()
        qual = out["quality_logits"][0].detach().cpu()
        eff_sum[s:e] += eff
        qual_sum[s:e] += qual
        if side_sum is not None and "side_logits" in out:
            side_sum[s:e] += out["side_logits"][0].detach().cpu()
        count[s:e] += 1
    return eff_sum / count.clamp_min(1), qual_sum / count.clamp_min(1), side_sum / count.clamp_min(1) if side_sum is not None else None


def write_segment_previews(video_path: Path, seg_df: pd.DataFrame, preview_dir: Path, safe: str, max_count: int) -> None:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return
    for row in seg_df.head(max_count).itertuples(index=False):
        mid = (float(row.pred_start_sec) + float(row.pred_end_sec)) / 2.0
        cap.set(cv2.CAP_PROP_POS_MSEC, mid * 1000.0)
        ok, frame = cap.read()
        if ok and frame is not None:
            out_file = preview_dir / f"{safe}_seg{int(row.segment_index):03d}_{mid:.1f}s.jpg"
            cv2.imwrite(str(out_file), frame)
    cap.release()


def merge_csv(files: Iterable[Path], out_file: Path) -> None:
    dfs = []
    for f in files:
        if f.exists() and f.stat().st_size > 0:
            try:
                df = pd.read_csv(f)
                if not df.empty:
                    dfs.append(df)
            except pd.errors.EmptyDataError:
                pass
    pd.concat(dfs, ignore_index=True).to_csv(out_file, index=False) if dfs else pd.DataFrame().to_csv(out_file, index=False)


def cleanup_worker_files(files: Iterable[Path]) -> None:
    for f in files:
        try:
            f.unlink()
        except FileNotFoundError:
            pass


def write_summary(out_dir: Path, num_videos: int, cfg: dict, args: argparse.Namespace) -> None:
    manifest = pd.read_csv(out_dir / "video_manifest.csv")
    segments = pd.read_csv(out_dir / "all_predicted_segments.csv")
    clips = pd.read_csv(out_dir / "all_clip_predictions.csv")
    summary = {
        "num_input_videos": int(num_videos),
        "num_ok_videos": int(manifest["status"].eq("ok").sum()) if not manifest.empty else 0,
        "num_error_videos": int((~manifest["status"].eq("ok")).sum()) if not manifest.empty else 0,
        "num_clips": int(len(clips)),
        "num_predicted_segments": int(len(segments)),
        "total_video_sec": float(manifest["duration_sec"].sum(skipna=True)) if not manifest.empty else 0.0,
        "total_predicted_effective_sec": float(segments["pred_duration_sec"].sum()) if not segments.empty else 0.0,
        "overall_retention_ratio": (float(segments["pred_duration_sec"].sum()) / float(manifest["duration_sec"].sum(skipna=True))) if not manifest.empty and float(manifest["duration_sec"].sum(skipna=True)) > 0 and not segments.empty else 0.0,
        "threshold": float(cfg["eval"]["threshold"]),
        "min_segment_sec": float(cfg["eval"]["min_segment_sec"]),
        "merge_gap_sec": float(cfg["eval"]["merge_gap_sec"]),
        "input_dir": str(args.input_dir),
        "output_dir": str(out_dir),
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES", ""),
        "gpus_arg": args.gpus,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


def write_ffmpeg_commands(segments_csv: Path, out_file: Path) -> None:
    try:
        segments = pd.read_csv(segments_csv)
    except Exception:
        segments = pd.DataFrame()
    lines = ["#!/usr/bin/env bash", "set -euo pipefail", "cd \"$(dirname \"$0\")\"", "mkdir -p effective_video_clips"]
    for row in segments.itertuples(index=False):
        src = str(row.video_path)
        base = safe_name(Path(src).stem)
        seg_i = int(row.segment_index)
        dst = f"effective_video_clips/{base}_seg{seg_i:03d}_{float(row.pred_start_sec):.1f}-{float(row.pred_end_sec):.1f}.mp4"
        lines.append(f"ffmpeg -hide_banner -loglevel error -y -ss {float(row.pred_start_sec):.3f} -to {float(row.pred_end_sec):.3f} -i {sh_quote(src)} -c copy {sh_quote(dst)}")
    out_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out_file.chmod(0o755)


def sh_quote(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


def safe_name(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-._" else "_" for c in name)


if __name__ == "__main__":
    main()
