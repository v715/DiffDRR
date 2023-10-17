# AUTOGENERATED! DO NOT EDIT! File to edit: ../notebooks/api/00_drr.ipynb.

# %% ../notebooks/api/00_drr.ipynb 3
from __future__ import annotations

import numpy as np
import torch
import torch.nn as nn
from fastcore.basics import patch

from .detector import Detector
from .siddon import siddon_raycast

# %% auto 0
__all__ = ['DRR', 'Registration']

# %% ../notebooks/api/00_drr.ipynb 7
class DRR(nn.Module):
    """PyTorch module that computes differentiable digitally reconstructed radiographs."""

    def __init__(
        self,
        volume: np.ndarray,  # CT volume
        spacing: np.ndarray,  # Dimensions of voxels in the CT volume
        sdr: float,  # Source-to-detector radius for the C-arm (half of the source-to-detector distance)
        height: int,  # Height of the rendered DRR
        delx: float,  # X-axis pixel size
        width: int
        | None = None,  # Width of the rendered DRR (if not provided, set to `height`)
        dely: float | None = None,  # Y-axis pixel size (if not provided, set to `delx`)
        p_subsample: float | None = None,  # Proportion of pixels to randomly subsample
        reshape: bool = True,  # Return DRR with shape (b, 1, h, w)
        reverse_x_axis: bool = False,  # If pose includes reflection (in E(3) not SE(3)), reverse x-axis
        patch_size: int
        | None = None,  # If the entire DRR can't fit in memory, render patches of the DRR in series
        bone_attenuation_multiplier: float = 1.0,  # Contrast ratio of bone to soft tissue
        mode: str = "perspective",  # Either `perspective` or `orthographic`
    ):
        super().__init__()

        # Initialize the X-ray detector
        width = height if width is None else width
        dely = delx if dely is None else dely
        n_subsample = (
            int(height * width * p_subsample) if p_subsample is not None else None
        )
        self.detector = Detector(
            sdr,
            height,
            width,
            delx,
            dely,
            n_subsample=n_subsample,
            reverse_x_axis=reverse_x_axis,
            mode=mode,
        )

        # Initialize the volume
        self.register_buffer("spacing", torch.tensor(spacing))
        self.register_buffer("volume", torch.tensor(volume).flip([0]))
        self.reshape = reshape
        self.patch_size = patch_size
        if self.patch_size is not None:
            self.n_patches = (height * width) // (self.patch_size**2)

        # Parameters for segmenting the CT volume and reweighting voxels
        self.air = torch.where(self.volume <= -800)
        self.soft_tissue = torch.where((-800 < self.volume) & (self.volume <= 350))
        self.bone = torch.where(350 < self.volume)
        self.bone_attenuation_multiplier = bone_attenuation_multiplier

    def reshape_transform(self, img, batch_size):
        if self.reshape:
            if self.detector.n_subsample is None:
                img = img.view(-1, 1, self.detector.height, self.detector.width)
            else:
                img = reshape_subsampled_drr(img, self.detector, batch_size)
        return img

# %% ../notebooks/api/00_drr.ipynb 8
def reshape_subsampled_drr(
    img: torch.Tensor,
    detector: Detector,
    batch_size: int,
):
    n_points = detector.height * detector.width
    drr = torch.zeros(batch_size, n_points).to(img)
    drr[:, detector.subsamples[-1]] = img
    drr = drr.view(batch_size, 1, detector.height, detector.width)
    return drr

# %% ../notebooks/api/00_drr.ipynb 10
from pytorch3d.transforms import Transform3d

from .detector import make_xrays


@patch
def forward(
    self: DRR,
    rotation: torch.Tensor,
    translation: torch.Tensor,
    parameterization: str,
    convention: str = None,
    pose: Transform3d = None,  # If you have a preformed pose, can pass it directly
    bone_attenuation_multiplier: float = None,  # Contrast ratio of bone to soft tissue
):
    """Generate DRR with rotational and translational parameters."""
    if not hasattr(self, "density"):
        self.set_bone_attenuation_multiplier(self.bone_attenuation_multiplier)
    if bone_attenuation_multiplier is not None:
        self.set_bone_attenuation_multiplier(bone_attenuation_multiplier)

    if pose is None:
        assert len(rotation) == len(translation)
        batch_size = len(rotation)
        source, target = self.detector(
            rotation=rotation,
            translation=translation,
            parameterization=parameterization,
            convention=convention,
        )
    else:
        batch_size = len(pose)
        source, target = make_xrays(pose, self.detector.source, self.detector.target)

    if self.patch_size is not None:
        n_points = target.shape[1] // self.n_patches
        img = []
        for idx in range(self.n_patches):
            t = target[:, idx * n_points : (idx + 1) * n_points]
            partial = siddon_raycast(source, t, self.density, self.spacing)
            img.append(partial)
        img = torch.cat(img, dim=1)
    else:
        img = siddon_raycast(source, target, self.density, self.spacing)
    return self.reshape_transform(img, batch_size=batch_size)

# %% ../notebooks/api/00_drr.ipynb 11
@patch
def set_bone_attenuation_multiplier(self: DRR, bone_attenuation_multiplier: float):
    self.density = torch.empty_like(self.volume)
    self.density[self.air] = self.volume[self.soft_tissue].min()
    self.density[self.soft_tissue] = self.volume[self.soft_tissue]
    self.density[self.bone] = self.volume[self.bone] * bone_attenuation_multiplier
    self.density -= self.density.min()
    self.density /= self.density.max()
    self.bone_attenuation_multiplier = bone_attenuation_multiplier

# %% ../notebooks/api/00_drr.ipynb 13
from .utils import convert


class Registration(nn.Module):
    """Perform automatic 2D-to-3D registration using differentiable rendering."""

    def __init__(
        self,
        drr: DRR,
        rotation: torch.Tensor,
        translation: torch.Tensor,
        parameterization: str,
        input_convention: str = None,
        output_convention: str = "ZYX",
    ):
        super().__init__()
        self.drr = drr
        self.rotation = nn.Parameter(rotation)
        self.translation = nn.Parameter(translation)
        self.parameterization = parameterization
        self.input_convention = input_convention
        self.output_convention = output_convention

    def forward(self):
        return self.drr(
            self.rotation,
            self.translation,
            self.parameterization,
            self.input_convention,
        )

    def get_rotation(self):
        return (
            convert(
                self.rotation,
                input_parameterization=self.parameterization,
                output_parameterization="euler_angles",
                input_convention=self.input_convention,
                output_convention=self.output_convention,
            )
            .clone()
            .detach()
            .cpu()
        )

    def get_translation(self):
        return self.translation.clone().detach().cpu()
