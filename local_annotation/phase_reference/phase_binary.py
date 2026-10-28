"""Versioned temporal phase architecture; two quality classes and masked findings.

Model execution belongs on sam-wg-gpu. State keys retain compatibility with the
historical TemporalTransformerSegmenter trunk, effective head and side head.
"""
import math
import torch
from torch import nn

FORMAT = 'nasal-phase.binary-quality.v2'
QUALITY_CLASSES = ('low_quality', 'readable')


class PositionalEncoding(nn.Module):
    def __init__(self, d_model, max_len, dropout):
        super().__init__()
        self.dropout = nn.Dropout(dropout)
        position = torch.arange(max_len, dtype=torch.float32).unsqueeze(1)
        scale = torch.exp(torch.arange(0, d_model, 2) * (-math.log(10000.) / d_model))
        pe = torch.zeros(max_len, d_model)
        pe[:, 0::2], pe[:, 1::2] = torch.sin(position * scale), torch.cos(position * scale)
        self.register_buffer('pe', pe.unsqueeze(0), persistent=False)

    def forward(self, values):
        return self.dropout(values + self.pe[:, :values.shape[1]])


class BinaryPhaseModel(nn.Module):
    def __init__(self, feature_dim=512, d_model=256, nhead=4, num_layers=3,
                 dropout=.15, max_len=256, side_num_classes=3, finding_names=()):
        super().__init__()
        self.side_num_classes, self.quality_num_classes = side_num_classes, 2
        self.finding_names = tuple(finding_names)
        self.input_proj = nn.Sequential(nn.LayerNorm(feature_dim), nn.Linear(feature_dim, d_model),
                                        nn.GELU(), nn.Dropout(dropout))
        self.pos = PositionalEncoding(d_model, max_len, dropout)
        layer = nn.TransformerEncoderLayer(d_model=d_model, nhead=nhead,
            dim_feedforward=4*d_model, dropout=dropout, activation='gelu',
            batch_first=True, norm_first=True)
        self.encoder = nn.TransformerEncoder(layer, num_layers=num_layers)
        self.effective_head = nn.Linear(d_model, 2)
        self.quality_head = nn.Linear(d_model, 2)
        self.side_head = nn.Linear(d_model, side_num_classes)
        self.finding_head = nn.Linear(d_model, len(self.finding_names)) if self.finding_names else None

    def forward(self, features, padding_mask=None):
        hidden = self.encoder(self.pos(self.input_proj(features)), src_key_padding_mask=padding_mask)
        result = {'effective_logits': self.effective_head(hidden),
                  'quality_logits': self.quality_head(hidden), 'side_logits': self.side_head(hidden)}
        if self.finding_head is not None:
            result['finding_logits'] = self.finding_head(hidden)
        return result


def initialize_historical(model, state):
    """Copy the trunk; quality is conditional low/readable, not four grades."""
    remaining = {k: v for k, v in state.items() if not k.startswith('quality_head.')}
    result = model.load_state_dict(remaining, strict=False)
    assert not result.unexpected_keys
    assert set(result.missing_keys) <= {'quality_head.weight', 'quality_head.bias',
                                       'finding_head.weight', 'finding_head.bias'}
    with torch.no_grad():
        for name in ('weight', 'bias'):
            old = state['quality_head.' + name]
            target = getattr(model.quality_head, name)
            # Legacy class 0 means non-effective, excluded by conditional quality.
            target[0].copy_(old[1])
            target[1].copy_(old[2:4].mean(0))


@torch.inference_mode()
def predict_windowed(model, features, max_len=256, device='cuda'):
    """Infer every head, without assuming four quality classes on long videos."""
    if features.ndim != 2 or not 0 < len(features) or max_len < 2:
        raise ValueError('Invalid temporal feature sequence')
    count = torch.zeros((len(features), 1), dtype=torch.float32)
    sums = {}
    starts = [0] if len(features) <= max_len else range(0, len(features), max_len // 2)
    for start in starts:
        stop = min(start + max_len, len(features))
        chunk = features[start:stop].unsqueeze(0).to(device)
        mask = torch.zeros((1, stop-start), dtype=torch.bool, device=device)
        output = model(chunk, mask)
        for key, value in output.items():
            value = value[0].detach().float().cpu()
            if value.ndim != 2 or value.shape[0] != stop-start or not torch.isfinite(value).all():
                raise ValueError('Invalid phase model output')
            if key not in sums:
                sums[key] = torch.zeros((len(features), value.shape[1]), dtype=torch.float32)
            sums[key][start:stop] += value
        count[start:stop] += 1
    return {key: value / count.clamp_min(1) for key, value in sums.items()}
