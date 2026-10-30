"""Translate recorded model preprocessing into original-pixel player geometry.

This adapter never estimates an FOV: only the actual boxes returned by inference
can become overlays. The reference preprocessing itself remains unchanged.
"""
import math


def crop_record(result, video, job_id):
    preprocess = result.get('preprocess')
    if not isinstance(preprocess, dict) or not preprocess.get('intervals'):
        return None
    metadata = result.get('video', {})
    width, height = video['width'], video['height']
    if metadata.get('width') != width or metadata.get('height') != height:
        raise ValueError('裁剪记录尺寸与源视频不符')
    intervals, previous = [], 0.0
    for item in preprocess['intervals']:
        start, end = float(item['start_sec']), float(item['end_sec'])
        box = item['box']
        if (len(box) != 4 or any(type(value) not in (float, int) or not math.isfinite(value) for value in box)
                or not 0 <= box[0] < box[2] <= width or not 0 <= box[1] < box[3] <= height
                or not math.isfinite(start) or not math.isfinite(end) or start < previous
                or not 0 <= start < end or end > video['duration_seconds'] + .1):
            raise ValueError('模型裁剪记录包含无效的时段或像素范围')
        end = min(end, video['duration_seconds'])
        if end <= start:
            raise ValueError('模型裁剪记录超出源视频')
        x0, y0, x1, y1 = box
        intervals.append({'start_sec': start, 'end_sec': end,
                          'points': [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
                          'fallback': item.get('ellipse') is None})
        previous = end
    return {'schema': 'sam-workflow.video-crop.v1', 'job_id': job_id,
            'video_sha256': video['sha256'], 'video': {'width': width, 'height': height},
            'source': preprocess.get('source_type'), 'strategy': preprocess.get('strategy'),
            'model': result.get('model', {}), 'intervals': intervals}
