"""Shared offline/serving temporal decisions and patient-level study metrics."""
import math
import numpy as np

IGNORE = -100


def softmax(logits):
    value = np.asarray(logits, dtype=np.float64)
    value = np.exp(value - value.max(-1, keepdims=True))
    return value / value.sum(-1, keepdims=True)


def segments_from_mask(rows, mask):
    intervals = []
    for row, keep in zip(rows, mask):
        if not keep:
            continue
        start, stop = float(row['start_sec']), float(row['end_sec'])
        if intervals and abs(intervals[-1][1] - start) < 1e-5:
            intervals[-1] = (intervals[-1][0], stop)
        else:
            intervals.append((start, stop))
    return intervals


def temporal_decisions(rows, effective_prob, quality_prob, parameters, legacy_quality=None):
    """A single implementation is used for validation and new-model deployment."""
    raw = np.asarray(effective_prob) >= parameters['threshold']
    intervals = segments_from_mask(rows, raw)
    merged = []
    for start, stop in intervals:
        if merged and start - merged[-1][1] <= parameters['merge_gap_sec'] + 1e-6:
            merged[-1] = (merged[-1][0], stop)
        else:
            merged.append((start, stop))
    merged = [(s, e) for s, e in merged if e-s >= parameters['min_segment_sec']-1e-6]
    times = np.asarray([r['start_sec'] for r in rows])
    effective = np.zeros(len(rows), dtype=bool)
    for start, stop in merged:
        effective[(times >= start) & (times < stop)] = True
    quality_prob = np.asarray(quality_prob)
    window = parameters.get('quality_smooth', 1)
    if window == 3:
        quality_prob = np.median(np.stack([np.r_[quality_prob[0], quality_prob[:-1]],
            quality_prob, np.r_[quality_prob[1:], quality_prob[-1]]]), axis=0)
    quality = quality_prob >= parameters.get('quality_threshold', .5)
    readable = effective & quality
    if legacy_quality is not None:
        # The deployed v1 engine assigns one modal raw four-grade quality to
        # an entire effective segment. Reproduce that actual extraction result.
        readable[:] = False
        for start, stop in merged:
            selected = (times >= start) & (times < stop)
            counts = np.bincount(np.asarray(legacy_quality)[selected], minlength=4)
            if counts.argmax() >= 2:
                readable[selected] = True
    return {'effective': effective, 'quality': quality.astype(int),
            'readable_effective': readable, 'effective_intervals': merged}


def binary_metrics(labels, probability, predictions, weights=None):
    from sklearn.metrics import roc_auc_score, average_precision_score
    labels, probability, predictions = map(np.asarray, (labels, probability, predictions))
    mask = labels != IGNORE
    labels, probability, predictions = labels[mask], probability[mask], predictions[mask]
    weight = np.ones(len(labels)) if weights is None else np.asarray(weights)[mask]
    tp = float(weight[(labels == 1) & (predictions == 1)].sum())
    fp = float(weight[(labels == 0) & (predictions == 1)].sum())
    fn = float(weight[(labels == 1) & (predictions == 0)].sum())
    tn = float(weight[(labels == 0) & (predictions == 0)].sum())
    precision = tp / (tp+fp) if tp+fp else 0.
    recall = tp / (tp+fn) if tp+fn else 0.
    result = {'n': int(mask.sum()), 'positive': int((labels == 1).sum()),
        'negative': int((labels == 0).sum()), 'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn,
        'precision': precision, 'recall': recall,
        'f1': 2*precision*recall/(precision+recall) if precision+recall else 0.,
        'accuracy': (tp+tn)/(tp+tn+fp+fn) if tp+tn+fp+fn else None,
        'roc_auc': None, 'average_precision': None}
    if len(set(labels)) == 2:
        result['roc_auc'] = float(roc_auc_score(labels, probability, sample_weight=weight))
        result['average_precision'] = float(average_precision_score(labels, probability, sample_weight=weight))
    return result


def segment_counts(truth, predicted, threshold=.5):
    from scipy.optimize import linear_sum_assignment
    iou = np.zeros((len(truth), len(predicted)))
    for i, (a, b) in enumerate(truth):
        for j, (c, d) in enumerate(predicted):
            intersection = max(0., min(b, d)-max(a, c))
            union = b-a+d-c-intersection
            iou[i, j] = intersection/union if union else 0.
    if not iou.size:
        return (0, len(predicted), len(truth), [], [])
    # Maximize the number of valid one-to-one matches before IoU tie-breaking.
    valid = iou >= threshold
    score = valid.astype(float) * (1 + iou/(min(iou.shape)+1))
    matched = zip(*linear_sum_assignment(score, maximize=True))
    accepted = [(i, j) for i, j in matched if valid[i, j]]
    errors = [abs(truth[i][k]-predicted[j][k]) for i, j in accepted for k in (0, 1)]
    return (len(accepted), len(predicted)-len(accepted), len(truth)-len(accepted),
            [float(iou[i, j]) for i, j in accepted], errors)


def evaluate(predictions, parameters, finding_names=(), legacy=False, compute_segments=True):
    """Predictions are per-video records; ignored/external gaps are never negatives."""
    from sklearn.metrics import f1_score
    true, scores, decisions, weights = [], [], [], []
    q_true, q_pred, s_true, s_pred = [], [], [], []
    findings = {name: ([], [], []) for name in finding_names}
    segment_totals = {'effective': [0, 0, 0, [], []], 'readable_effective': [0, 0, 0, [], []]}
    case_scores = []
    for item in predictions:
        eff = softmax(item['effective_logits'])[:, 1]
        q = softmax(item['quality_logits'])
        q_probability = q[:, 2:4].sum(-1) if legacy else q[:, 1]
        output = temporal_decisions(item['rows'], eff, q_probability, parameters,
            q.argmax(-1) if legacy else None)
        y_eff, y_q, y_side = map(np.asarray, (item['effective'], item['quality'], item['side']))
        y_read = np.where(y_eff == 0, 0, np.where((y_eff == 1) & (y_q != IGNORE), y_q, IGNORE))
        true.append(np.stack([y_eff, y_read], axis=1))
        scores.append(np.stack([eff, eff*q_probability], axis=1))
        decisions.append(np.stack([output['effective'], output['readable_effective']], axis=1))
        weights.extend(r['end_sec']-r['start_sec'] for r in item['rows'])
        q_true.extend(y_q);q_pred.extend((q.argmax(-1) >= 2).astype(int) if legacy else output['quality'])
        s_true.extend(y_side);s_pred.extend(np.asarray(item['side_logits']).argmax(-1))
        case_scores.append(binary_metrics(y_eff, eff, output['effective'])['f1'])
        if compute_segments and item.get('cohort') != 'external':
            for j, key in enumerate(segment_totals):
                target = y_eff == 1 if j == 0 else y_read == 1
                counts = segment_counts(segments_from_mask(item['rows'], target),
                    segments_from_mask(item['rows'], decisions[-1][:, j]))
                for k in range(3):segment_totals[key][k] += counts[k]
                for k in (3, 4):segment_totals[key][k].extend(counts[k])
        if finding_names and 'finding_logits' in item:
            probability = 1/(1+np.exp(-np.clip(item['finding_logits'], -50, 50)))
            for j, name in enumerate(finding_names):
                yt, yp, yd = findings[name]
                yt.extend(np.asarray(item['finding'])[:, j]);yp.extend(probability[:, j]);yd.extend(probability[:, j] >= .5)
    if not true:
        return {'videos': 0, 'groups': 0}
    true, scores, decisions = map(np.concatenate, (true, scores, decisions))
    report = {'videos': len(predictions), 'groups': len({p['group_key'] for p in predictions}),
              'case_macro_effective_f1': float(np.mean(case_scores))}
    for i, key in enumerate(('effective', 'readable_effective')):
        report[key] = binary_metrics(true[:, i], scores[:, i], decisions[:, i])
        report[key+'_duration'] = binary_metrics(true[:, i], scores[:, i], decisions[:, i], weights)
        tp, fp, fn, ious, errors = segment_totals[key]
        p, r = tp/(tp+fp) if tp+fp else 0., tp/(tp+fn) if tp+fn else 0.
        report[key+'_segments'] = {'tp': tp, 'fp': fp, 'fn': fn,
            'precision': p, 'recall': r, 'f1': 2*p*r/(p+r) if p+r else 0.,
            'matched_mean_iou': float(np.mean(ious)) if ious else None,
            'matched_boundary_mae_sec': float(np.mean(errors)) if errors else None}
    for name, yt, yp, classes in [('quality', q_true, q_pred, [0, 1]), ('side', s_true, s_pred, [1, 2])]:
        yt, yp = np.asarray(yt), np.asarray(yp);mask = yt != IGNORE
        report[name] = {'n': int(mask.sum()), 'accuracy': float((yt[mask] == yp[mask]).mean()) if mask.any() else None,
            'macro_f1': float(f1_score(yt[mask], yp[mask], labels=classes, average='macro', zero_division=0)) if mask.any() else None}
    report['findings'] = {name: binary_metrics(*values) for name, values in findings.items() if values[0]}
    return report


def select_parameters(predictions, legacy=False):
    """Thresholds/postprocessing are selected on validation videos only."""
    if not predictions or any(item.get('split') != 'val' for item in predictions):
        raise ValueError('Temporal parameters may only be tuned on validation groups')
    best = None
    for threshold in (.1, .15, .2, .3, .4, .5, .6, .7, .8, .9):
        for gap in (0., 1.):
            for quality_threshold in ((.5,) if legacy else (.3, .4, .5, .6, .7)):
                for smooth in ((1,) if legacy else (1, 3)):
                    parameters = {'threshold': threshold, 'min_segment_sec': 1., 'merge_gap_sec': gap,
                        'quality_threshold': quality_threshold, 'quality_smooth': smooth}
                    # Optimize clip decisions without expensive segment matching.
                    metrics = evaluate(predictions, parameters, legacy=legacy, compute_segments=False)
                    score = .5*metrics['effective']['f1'] + .3*metrics['readable_effective']['f1'] + .2*(metrics['quality']['macro_f1'] or 0.)
                    if best is None or score > best[0]+1e-9:
                        best = score, parameters
    return {'validation_selection_score': best[0], 'parameters': best[1]}
