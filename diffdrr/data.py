# AUTOGENERATED! DO NOT EDIT! File to edit: ../notebooks/api/03_data.ipynb.

# %% ../notebooks/api/03_data.ipynb 3
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torchio import LabelMap, ScalarImage, Subject
from torchio.transforms import Resample

# %% auto 0
__all__ = ['load_example_ct', 'read']

# %% ../notebooks/api/03_data.ipynb 5
def load_example_ct(
    labels=None,
    orientation="AP",
    bone_attenuation_multiplier=1.0,
    **kwargs,
) -> Subject:
    """Load an example chest CT for demonstration purposes."""
    datadir = Path(__file__).resolve().parent / "data"
    volume = datadir / "cxr.nii.gz"
    labelmap = datadir / "mask.nii.gz"
    structures = pd.read_csv(datadir / "structures.csv")
    return read(
        volume,
        labelmap,
        labels,
        orientation=orientation,
        bone_attenuation_multiplier=bone_attenuation_multiplier,
        structures=structures,
        **kwargs,
    )

# %% ../notebooks/api/03_data.ipynb 6
from .pose import RigidTransform


def read(
    volume: str | Path | ScalarImage,  # CT volume
    labelmap: str | Path | LabelMap = None,  # Labelmap for the CT volume
    labels: int | list = None,  # Labels from the mask of structures to render
    orientation: str | None = "AP",  # Frame-of-reference change
    bone_attenuation_multiplier: float = 1.0,  # Scalar multiplier on density of high attenuation voxels
    fiducials: torch.Tensor = None,  # 3D fiducials in world coordinates
    transform: RigidTransform = None,  # RigidTransform to apply to the volume's affine
    **kwargs,  # Any additional information to be stored in the torchio.Subject
) -> Subject:
    """
    Read an image volume from a variety of formats, and optionally, any
    given labelmap for the volume. Converts volume to a RAS+ coordinate
    system and moves the volume isocenter to the world origin.
    """
    # Read the volume
    if isinstance(volume, ScalarImage):
        pass
    else:
        volume = ScalarImage(volume)

    # Read the mask if passed
    if labelmap is not None:
        if isinstance(labelmap, LabelMap):
            mask = labelmap
        else:
            mask = LabelMap(labelmap)
        _ = mask.data  # Load and cache the labelmap
    else:
        mask = None

    # Optionally apply transform
    if transform is not None:
        T = transform.matrix[0].numpy()
        volume = ScalarImage(tensor=volume.data, affine=T.dot(volume.affine))

    # Convert the volume to density
    density = transform_hu_to_density(volume.data, bone_attenuation_multiplier)
    density = ScalarImage(tensor=density, affine=volume.affine)

    # Frame-of-reference change
    if orientation == "AP":
        # Rotates the C-arm about the x-axis by 90 degrees
        # Rotates the C-arm about the z-axis by -90 degrees
        reorient = torch.tensor(
            [
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, -1.0, 0.0],
                [-1.0, 0.0, 0.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ]
        )
    elif orientation == "PA":
        # Rotates the C-arm about the x-axis by 90 degrees
        # Rotates the C-arm about the z-axis by 90 degrees
        reorient = torch.tensor(
            [
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
                [-1.0, 0.0, 0.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ]
        )
    elif orientation is None:
        # Identity transform
        reorient = torch.tensor(
            [
                [1.0, 0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ]
        )
    else:
        raise ValueError(f"Unrecognized orientation {orientation}")

    # Package the subject
    isocenter = volume.get_center()
    isocenter_to_origin = np.array(
        [
            [1.0, 0.0, 0.0, -isocenter[0]],
            [0.0, 1.0, 0.0, -isocenter[1]],
            [0.0, 0.0, 1.0, -isocenter[2]],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )
    subject = Subject(
        volume=volume,
        mask=mask,
        reorient=reorient,
        isocenter_to_origin=isocenter_to_origin,
        density=density,
        fiducials=fiducials,
        **kwargs,
    )

    # Apply mask
    if labels is not None:
        if isinstance(labels, int):
            labels = [labels]
        mask = torch.any(
            torch.stack([subject.mask.data.squeeze() == idx for idx in labels]), dim=0
        )
        subject.density.data = subject.density.data * mask

    return subject

# %% ../notebooks/api/03_data.ipynb 8
def transform_hu_to_density(volume, bone_attenuation_multiplier):
    # volume can be loaded as int16, need to convert to float32 to use float bone_attenuation_multiplier
    volume = volume.to(torch.float32)
    air = torch.where(volume <= -800)
    soft_tissue = torch.where((-800 < volume) & (volume <= 350))
    bone = torch.where(350 < volume)

    density = torch.empty_like(volume)
    density[air] = volume[soft_tissue].min()
    density[soft_tissue] = volume[soft_tissue]
    density[bone] = volume[bone] * bone_attenuation_multiplier
    density -= density.min()
    density /= density.max()
    return density
