from __future__ import annotations

import math

import torch
from torch import nn


class PositionalEncoding(nn.Module):
    def __init__(self, d_model: int, max_len: int = 512, dropout: float = 0.1) -> None:
        super().__init__()
        self.dropout = nn.Dropout(dropout)
        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float32).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2, dtype=torch.float32) * (-math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        self.register_buffer("pe", pe.unsqueeze(0), persistent=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = x + self.pe[:, : x.size(1)]
        return self.dropout(x)


class TemporalTransformerSegmenter(nn.Module):
    def __init__(
        self,
        feature_dim: int = 512,
        d_model: int = 256,
        nhead: int = 4,
        num_layers: int = 3,
        dropout: float = 0.15,
        max_len: int = 256,
        side_num_classes: int = 0,
    ) -> None:
        super().__init__()
        self.side_num_classes = int(side_num_classes)
        self.input_proj = nn.Sequential(
            nn.LayerNorm(feature_dim),
            nn.Linear(feature_dim, d_model),
            nn.GELU(),
            nn.Dropout(dropout),
        )
        self.pos = PositionalEncoding(d_model=d_model, max_len=max_len, dropout=dropout)
        layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=nhead,
            dim_feedforward=d_model * 4,
            dropout=dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(layer, num_layers=num_layers)
        self.effective_head = nn.Linear(d_model, 2)
        self.quality_head = nn.Linear(d_model, 4)
        self.side_head = nn.Linear(d_model, self.side_num_classes) if self.side_num_classes > 0 else None

    def forward(self, features: torch.Tensor, padding_mask: torch.Tensor | None = None) -> dict[str, torch.Tensor]:
        x = self.input_proj(features)
        x = self.pos(x)
        x = self.encoder(x, src_key_padding_mask=padding_mask)
        out = {
            "effective_logits": self.effective_head(x),
            "quality_logits": self.quality_head(x),
        }
        if self.side_head is not None:
            out["side_logits"] = self.side_head(x)
        return out


def temporal_smoothing_loss(
    logits: torch.Tensor,
    labels: torch.Tensor,
    ignore_index: int = -100,
    weights: torch.Tensor | None = None,
) -> torch.Tensor:
    if logits.size(1) < 2:
        return logits.new_tensor(0.0)
    logp = torch.log_softmax(logits, dim=-1)
    diff = (logp[:, 1:] - logp[:, :-1]).pow(2).mean(dim=-1)
    mask = (labels[:, 1:] != ignore_index) & (labels[:, :-1] != ignore_index)
    if mask.sum() == 0:
        return logits.new_tensor(0.0)
    diff = torch.clamp(diff[mask], max=16.0)
    if weights is None:
        return diff.mean()
    pair_weights = torch.minimum(weights[:, 1:].float(), weights[:, :-1].float())[mask].clamp_min(1e-4)
    return (diff * pair_weights).sum() / pair_weights.sum().clamp_min(1e-6)
