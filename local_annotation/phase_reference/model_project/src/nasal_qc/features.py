from __future__ import annotations

from pathlib import Path

import torch
from torch import nn
from torchvision import models


def build_resnet18_feature_extractor(pretrained: bool = True) -> tuple[nn.Module, int]:
    model = models.resnet18(weights=None)
    if pretrained:
        local_weights = Path(__file__).resolve().parents[2] / "weights" / "resnet18-f37072fd.pth"
        if local_weights.exists():
            state = torch.load(local_weights, map_location="cpu", weights_only=True)
            model.load_state_dict(state)
        else:
            try:
                weights = models.ResNet18_Weights.IMAGENET1K_V1
                model = models.resnet18(weights=weights)
            except Exception:
                model = models.resnet18(weights=None)
    feature_dim = model.fc.in_features
    model.fc = nn.Identity()
    model.eval()
    for p in model.parameters():
        p.requires_grad_(False)
    return model, feature_dim


def normalize_frames(frames: torch.Tensor) -> torch.Tensor:
    mean = torch.tensor([0.485, 0.456, 0.406], dtype=frames.dtype, device=frames.device).view(1, 3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225], dtype=frames.dtype, device=frames.device).view(1, 3, 1, 1)
    return (frames - mean) / std
