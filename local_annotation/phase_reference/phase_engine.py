"""Adapter around the release bundle, preserving its weights and postprocessing."""

from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import importlib.util
import math
import os
from pathlib import Path
import sys
import time

import cv2
import numpy as np
import torch
import yaml

from .config import InputError, Settings
from .adaptive_fov import AdaptiveFOV


PROFILES = {
    'annotation': 'raw020_no_sidegate.yaml',
    'high_precision': 'brightness_nogate_default.yaml',
}


class Cancelled(Exception):
    pass


@contextmanager
def gpu_section(lock, check):
    check()
    with lock:
        check()
        with torch.inference_mode():
            yield


class LockedModel:
    def __init__(self, model, lock, check):
        self.model, self.lock, self.check = model, lock, check
        self.side_num_classes = model.side_num_classes

    def __call__(self, *args, **kwargs):
        with gpu_section(self.lock, self.check):
            output = self.model(*args, **kwargs)
            # Keep work inside the lock until CUDA completes, including CPU copy.
            return {key: value.detach().cpu() for key, value in output.items()}


class PhaseEngine:
    def __init__(self, settings: Settings, gpu_lock, device='cpu'):
        self.settings, self.gpu_lock = settings, gpu_lock
        self.device = torch.device(device)
        self.fast_features = False
        project = settings.model_project
        checkpoint, backbone = settings.checkpoint, settings.backbone
        for path in (checkpoint, backbone):
            if not path.is_file():
                raise RuntimeError(f'Required phase weight missing: {path.name}')
        sys.path.insert(0, str(project / 'src'))
        spec = importlib.util.spec_from_file_location('local_nasal_release_batch', project / 'scripts/batch_infer_directory.py')
        self.release = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.release)
        self.configs = {key: yaml.safe_load((project / 'configs' / filename).read_text()) for key, filename in PROFILES.items()}
        cfg = self.configs['annotation']
        package = torch.load(checkpoint, map_location='cpu', weights_only=True)
        if not isinstance(package, dict) or not isinstance(package.get('model'), dict):
            raise RuntimeError('阶段权重格式无效：需要包含 model 状态字典')
        from torchvision.models import resnet18
        self.cnn = resnet18(weights=None)
        self.cnn.load_state_dict(torch.load(backbone, map_location='cpu', weights_only=True), strict=True)
        self.cnn.fc = torch.nn.Identity()
        self.cnn.to(self.device).eval()
        self.info = {'loaded': True, 'checkpoint': checkpoint.name,
            'checkpoint_sha256': hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
            'backbone': backbone.name, 'backbone_sha256': hashlib.sha256(backbone.read_bytes()).hexdigest(),
            'device': str(self.device), 'precision': 'float32', 'quality_class_count': 4,
            'output_schema': 'legacy-quality-0-3'}
        self.phase_registry = None
        if package.get('format') == 'nasal-phase.binary-quality.v2':
            from .phase_binary import BinaryPhaseModel
            import inspect
            proof = package.get('feature_provenance', {})
            if (package.get('quality_classes') != ['low_quality', 'readable']
                or package.get('crop_strategy') != 'adaptive-fov-v1'
                or package.get('backbone_sha256') != self.info['backbone_sha256']
                or proof.get('feature_function_sha256') != hashlib.sha256(inspect.getsource(type(self)._features).encode()).hexdigest()
                or proof.get('adaptive_fov_source_sha256') != hashlib.sha256(Path(inspect.getfile(AdaptiveFOV)).read_bytes()).hexdigest()
                or proof.get('features_config') != cfg['features']):
                raise RuntimeError('阶段权重的骨干或预处理指纹与本地参考实现不匹配')
            model_cfg = package.get('model_config', {})
            if {key: value for key,value in model_cfg.items() if key != 'finding_names'} != cfg['model']:
                raise RuntimeError('不支持此阶段模型结构')
            self.model = BinaryPhaseModel(**model_cfg)
            self.info.update(quality_class_count=2, output_schema='clinical-low-readable-v2',
                crop_strategy='adaptive-fov-v1', finding_names=list(model_cfg.get('finding_names', [])))
            self.phase_registry = PinnedModel(self.model, self.info, package['parameters'])
        elif package.get('format') is not None:
            raise RuntimeError('不支持此阶段权重格式')
        else:
            self.model = self.release.TemporalTransformerSegmenter(**cfg['model'])
        self.model.load_state_dict(package['model'], strict=True)
        self.model.to(self.device).eval()

    def request_model_info(self, request):
        if self.phase_registry is None:
            return self.info
        record = self.phase_registry.request_record(request)
        return {**record['metadata'], 'model_id': record['id'], 'checkpoint_sha256': record['sha256']}

    def freeze_request(self, request):
        """Freeze a version before defaults or a later global model switch."""
        if self.phase_registry is not None:
            record = self.phase_registry.request_record(request)
            request['_model_id'], request['_model_sha256'] = record['id'], record['sha256']
            if record['metadata']['crop_strategy'] == 'adaptive-fov-v1':
                request['crop_strategy'] = 'adaptive-fov-v1'
        else:
            request['_model_sha256'] = self.info['checkpoint_sha256']
        return request

    def output_quality_names(self):
        return {1: 'low_quality', 2: 'readable'} if self.info.get('quality_class_count') == 2 else self.release.QUALITY_NAMES

    def parameters(self, profile, overrides):
        cfg = self.configs[profile]
        result = {
            'profile': profile,
            'config': PROFILES[profile],
            'threshold': overrides.get('threshold') if overrides.get('threshold') is not None else cfg['eval']['threshold'],
            'min_segment_sec': overrides.get('min_segment_sec') if overrides.get('min_segment_sec') is not None else cfg['eval']['min_segment_sec'],
            'merge_gap_sec': overrides.get('merge_gap_sec') if overrides.get('merge_gap_sec') is not None else cfg['eval']['merge_gap_sec'],
            'side_gate': cfg['eval']['side_gate'],
            'brightness_gate': cfg['eval']['brightness_gate'],
            'clip_sec': cfg['data']['clip_sec'],
            'clip_stride_sec': cfg['data']['clip_stride_sec'],
            'frames_per_clip': cfg['features']['frames_per_clip'],
            'temporal_window_clips': cfg['model']['max_len'],
            'temporal_stride_clips': cfg['model']['max_len'] // 2,
        }
        if self.phase_registry is not None:
            record = self.phase_registry.request_record(overrides)
            if 'parameters' in record['metadata']:
                selected = record['metadata']['parameters']
                for key in ('threshold', 'min_segment_sec', 'merge_gap_sec'):
                    result[key] = overrides.get(key) if overrides.get(key) is not None else selected[key]
                if profile == 'high_precision' and overrides.get('threshold') is None:
                    result['threshold'] = max(.5, result['threshold'])
                result.update(quality_threshold=selected['quality_threshold'], quality_smooth=selected['quality_smooth'])
                if record['metadata']['quality_class_count'] == 2:
                    result['side_gate'] = {**result['side_gate'], 'enabled': False}
                    result['brightness_gate'] = {**result['brightness_gate'], 'enabled': False}
        return result

    def _features(self, path, meta, rows, cfg, check, progress, crop_strategy='legacy'):
        """Stream one decode pass; retain only a bounded batch of resized clips."""
        r = self.release
        fc = cfg['features']
        brightness = fc.get('brightness_prior', {})
        adaptive = AdaptiveFOV(path, meta, fc['image_size'], check) if crop_strategy == 'adaptive-fov-v1' else None
        transform = None if adaptive else r.build_frame_transform(path, fc['image_size'], fc.get('preprocess'))
        check()
        targets = []
        for clip, row in enumerate(rows):
            for sample in r.sample_times(row['start_sec'], row['end_sec'], fc['frames_per_clip']):
                targets.append((max(0, round(sample * meta['fps'])), clip))
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            raise InputError('video could not be opened by the decoder')
        last = None
        frame_index, decoded, target = -1, 0, 0
        features, stats, pending = [], [], []
        try:
            for clip, row in enumerate(rows):
                check()
                frames, luma, nonblack = [], [], []
                for _ in range(fc['frames_per_clip']):
                    index, owner = targets[target]
                    assert owner == clip
                    while frame_index < index:
                        check()
                        ok, frame = cap.read()
                        if not ok or frame is None:
                            # Match the release's last-frame padding for tail
                            # samples rounded just past the final frame.
                            break
                        last = frame
                        frame_index += 1
                        decoded += 1
                        if adaptive:
                            adaptive.observe(frame, frame_index / meta['fps'])
                    if last is None:
                        raise InputError('video contains no decodable frames')
                    rgb = adaptive.image(last) if adaptive else r.apply_frame_transform(cv2.cvtColor(last, cv2.COLOR_BGR2RGB), transform)
                    a, b = r.frame_brightness_stats(rgb, fc['preprocess'].get('black_threshold', 18))
                    frames.append(rgb)
                    luma.append(a)
                    nonblack.append(b)
                    target += 1
                weights = [r.brightness_weight(a, b, brightness) for a, b in zip(luma, nonblack)]
                stats.append({
                    'mean_luma': float(np.mean(luma)),
                    'nonblack_ratio': float(np.mean(nonblack)),
                    'brightness_weight': r.brightness_weight(float(np.mean(luma)), float(np.mean(nonblack)), brightness),
                    'frame_brightness_weight': weights,
                })
                pending.append(torch.from_numpy(np.stack(frames)).permute(0, 3, 1, 2).float().div_(255.0))
                if len(pending) == self.settings.cnn_batch_clips or clip + 1 == len(rows):
                    with gpu_section(self.gpu_lock, check):
                        features.append(r.run_cnn_batch(self.cnn, pending, self.device,
                                                       stats[-len(pending):], fc['frames_per_clip'], brightness))
                    pending.clear()
                progress(0.85 * (clip + 1) / len(rows), 'extracting_features')
        finally:
            cap.release()
        expected_decode = max(1, min(meta['frame_count'], targets[-1][0] + 1))
        if decoded < expected_decode - max(2, math.ceil(meta['fps'])):
            raise InputError('video ended substantially earlier than its metadata; check file integrity')
        return torch.cat(features), stats, adaptive.record() if adaptive else asdict(transform)

    def _features_fast(self, path, meta, rows, cfg, check, progress, crop_strategy='legacy'):
        from .phase_fast_features import features, ReferenceDecodeRequired
        try:
            return features(self, path, meta, rows, cfg, check, progress, crop_strategy)
        except ReferenceDecodeRequired:
            check()
            return self._features(path, meta, rows, cfg, check, progress, crop_strategy)

    def infer(self, path: Path, request: dict, check, progress):
        started = time.monotonic()
        bundle = self.phase_registry.load(self.phase_registry.request_record(request)) if self.phase_registry is not None else None
        model = bundle['model'] if bundle else self.model
        model_info = bundle['info'] if bundle else dict(self.info)
        binary_quality = model_info.get('quality_class_count') == 2
        try:
            meta = self.release.get_video_meta(path)
        except RuntimeError:
            raise InputError('video could not be opened by the decoder') from None
        if not all(math.isfinite(float(meta[key])) and meta[key] > 0 for key in meta):
            raise InputError('video metadata is invalid')
        if meta['duration_sec'] > self.settings.max_duration_sec:
            raise InputError('video exceeds PHASE_MAX_DURATION_SEC')
        if meta['width'] * meta['height'] > self.settings.max_pixels:
            raise InputError('video resolution exceeds PHASE_MAX_PIXELS')
        check()
        cfg = self.configs[request['profile']]
        params = self.parameters(request['profile'], request)
        count = max(1, math.ceil(max(0., meta['duration_sec'] - params['clip_sec']) / params['clip_stride_sec']) + 1)
        rows = [{'clip_index': i, 'start_sec': round(i * params['clip_stride_sec'], 3),
                 'end_sec': round(min(i * params['clip_stride_sec'] + params['clip_sec'], meta['duration_sec']), 3)}
                for i in range(count)]
        crop_strategy = 'adaptive-fov-v1' if binary_quality or model_info.get('crop_strategy') == 'adaptive-fov-v1' else request.get('crop_strategy','legacy')
        extract = self._features_fast if self.fast_features else self._features
        features, stats, transform = extract(path, meta, rows, cfg, check, progress, crop_strategy)
        progress(0.9, 'classifying')
        # Release's overlapping windows avoid the single-video script's 256-clip
        # positional encoding limit on long examinations.
        from .phase_binary import predict_windowed
        from .phase_study_metrics import temporal_decisions
        logits = predict_windowed(LockedModel(model, self.gpu_lock, check), features,
                                  int(cfg['model']['max_len']), self.device)
        eff, quality, side = logits['effective_logits'], logits['quality_logits'], logits['side_logits']
        raw = torch.softmax(eff, -1)[:, 1].numpy()
        quality_prob = torch.softmax(quality, -1).numpy()
        quality = quality.argmax(-1).numpy()
        finding_scores = torch.sigmoid(logits['finding_logits']).numpy() if 'finding_logits' in logits else None
        side_probs = torch.softmax(side, -1).numpy()
        side_ids = side_probs.argmax(-1)
        nasal = 1 - side_probs[:, params['side_gate']['none_class']]
        probs = raw.copy()
        if params['side_gate']['enabled']:
            probs *= nasal ** params['side_gate']['power']
        if params['brightness_gate']['enabled']:
            gate = params['brightness_gate']
            probs *= np.maximum(gate['min_prob_scale'], np.clip([s['brightness_weight'] for s in stats], 0, 1) ** gate['power'])
        times = np.array([r['start_sec'] for r in rows], dtype=np.float32)
        if binary_quality:
            decision = temporal_decisions(rows, probs, quality_prob[:, 1], params)
            pred, segments = decision['effective'], decision['effective_intervals']
            # Classifier indices 0/1 become clinical quality IDs 1/2.
            quality = decision['quality'] + 1
        else:
            pred, segments = self.release.postprocess_binary(times, probs, params['threshold'], params['min_segment_sec'], params['merge_gap_sec'])
            segments = self.release.clip_segments_to_duration(segments, meta['duration_sec'], params['min_segment_sec'])
        # Use final, duration-clipped intervals as the source of truth.
        pred = np.zeros_like(pred)
        for s, e in segments:
            pred[(times >= s) & (times < e)] = 1
        clips = []
        for i, row in enumerate(rows):
            clips.append({**row, **{k: v for k, v in stats[i].items() if k != 'frame_brightness_weight'},
                          'effective_prob_raw': float(raw[i]), 'nasal_side_prob': float(nasal[i]),
                          'effective_prob': float(probs[i]), 'effective_pred': int(pred[i]),
                          'quality_pred': int(quality[i]), 'quality_text': ({1:'low_quality',2:'readable'} if binary_quality else self.release.QUALITY_NAMES)[int(quality[i])],
                          'side_pred': int(side_ids[i]), 'side_text': self.release.SIDE_NAMES[int(side_ids[i])]})
            if finding_scores is not None:
                clips[-1]['finding_scores'] = {name: float(value) for name, value in zip(model_info['finding_names'], finding_scores[i])}
        if binary_quality:
            pieces = []
            for start, stop in segments:
                for i, row in enumerate(rows):
                    if not start <= row['start_sec'] < stop:
                        continue
                    left, right = max(start, row['start_sec']), min(stop, row['end_sec'])
                    identity = (int(quality[i]), int(side_ids[i]))
                    if pieces and abs(pieces[-1][1]-left) < 1e-6 and pieces[-1][2] == identity:
                        pieces[-1] = (pieces[-1][0], right, identity)
                    else:
                        pieces.append((left, right, identity))
            segments = [(s,e) for s,e,identity in pieces]
        output_segments = []
        for i, (s, e) in enumerate(segments):
            selected = [c for c in clips if s <= c['start_sec'] < e]
            # Match pandas mode's tie-break rule: lowest class ID wins.
            def mode(key):
                counts = Counter(c[key] for c in selected)
                return min(counts, key=lambda k: (-counts[k], k))
            q, side_id = mode('quality_pred'), mode('side_pred')
            output_segments.append({
                'segment_index': i, 'label': 'effective_observation',
                'start_sec': float(s), 'end_sec': float(e), 'duration_sec': float(e - s),
                'mean_effective_prob': float(np.mean([c['effective_prob'] for c in selected])),
                'quality_pred': q, 'quality_text': ({1:'low_quality',2:'readable'} if binary_quality else self.release.QUALITY_NAMES)[q],
                'side_pred': side_id, 'side_text': self.release.SIDE_NAMES[side_id],
            })
            if finding_scores is not None:
                output_segments[-1]['finding_scores'] = {name: float(np.mean([c['finding_scores'][name] for c in selected])) for name in model_info['finding_names']}
        check()
        return {
            'schema_version': '2.0' if binary_quality else '1.0', 'video_id': request.get('video_id'), 'video_path': str(path),
            'video': meta, 'model': model_info, 'parameters': params, 'preprocess': transform,
            'segments': output_segments, 'clips': clips,
            'summary': {'clip_count': len(clips), 'segment_count': len(segments),
                        'effective_duration_sec': sum(e - s for s, e in segments),
                        'retention_ratio': sum(e - s for s, e in segments) / meta['duration_sec'],
                        'elapsed_sec': round(time.monotonic() - started, 3)},
            'review_required': True,
        }


class PinnedModel:
    """One explicitly installed local checkpoint; no external model registry."""
    def __init__(self, model, info, parameters):
        self.model, self.info = model, info
        self.record = {'id': 'local-' + info['checkpoint_sha256'][:12], 'sha256': info['checkpoint_sha256'],
            'metadata': {**info, 'parameters': parameters}}

    def request_record(self, request):
        return self.record

    def load(self, record):
        return {'model': self.model, 'info': self.info}
