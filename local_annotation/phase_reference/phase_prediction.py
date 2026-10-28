"""Explicit quality-schema compatibility for gateway-facing phase results."""
import math
import re


def clinical_quality(value, model):
    if type(value) is int:
        raw = value
    elif isinstance(value, str) and value in {'0', '1', '2', '3'}:
        raw = int(value)
    else:
        raise ValueError('Invalid phase quality class')
    schema = model.get('output_schema')
    if schema is None:
        if model.get('quality_class_count', 4) != 4:
            raise ValueError('Binary quality outputs require an explicit schema')
        schema = 'legacy-quality-0-3'
    mapping = {'legacy-quality-0-3': {0: '1', 1: '1', 2: '2', 3: '2'},
               'clinical-low-readable-v2': {1: '1', 2: '2'},
               'binary-quality-index-v2': {0: '1', 1: '2'}}
    if schema not in mapping or raw not in mapping[schema]:
        raise ValueError('Unsupported phase quality schema or class')
    return mapping[schema][raw]


def visible_side(segment):
    value = segment.get('side_pred', 0)
    if type(value) is not int or value not in (0, 1, 2):
        raise ValueError('Invalid phase laterality class')
    return {0: 'unknown', 1: 'left', 2: 'right'}[value]


def finding_scores(segment):
    values = segment.get('finding_scores', {})
    if not isinstance(values, dict) or len(values) > 64:
        raise ValueError('Invalid auxiliary phase scores')
    for key, value in values.items():
        if (not isinstance(key, str) or not re.fullmatch('[a-z][a-z0-9_]{0,60}', key)
            or type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1):
            raise ValueError('Invalid auxiliary phase score')
    return values
