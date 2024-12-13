# AUTOGENERATED! DO NOT EDIT! File to edit: ../notebooks/api/02_detector.ipynb.

# %% ../notebooks/api/02_detector.ipynb 3
from __future__ import annotations

import torch
from fastcore.basics import patch
from torch.nn.functional import normalize

# %% auto 0
__all__ = ['Detector', 'get_focal_length', 'get_principal_point', 'parse_intrinsic_matrix', 'make_intrinsic_matrix']

# %% ../notebooks/api/02_detector.ipynb 5
from .pose import RigidTransform


class Detector(torch.nn.Module):
    """Construct a 6 DoF X-ray detector system. This model is based on a C-Arm."""

    def __init__(
        self,
        sdd: float,  # Source-to-detector distance (in units length)
        height: int,  # Y-direction length (in units pixels)
        width: int,  # X-direction length (in units pixels)
        delx: float,  # X-direction spacing (in units length / pixel)
        dely: float,  # Y-direction spacing (in units length / pixel)
        x0: float,  # Principal point x-coordinate (in units length)
        y0: float,  # Principal point y-coordinate (in units length)
        reorient: torch.Tensor,  # Frame-of-reference change matrix
        n_subsample: int | None = None,  # Number of target points to randomly sample
        reverse_x_axis: bool = False,  # If pose includes reflection (in E(3) not SE(3)), reverse x-axis
    ):
        super().__init__()
        self.height = height
        self.width = width
        self.n_subsample = n_subsample
        if self.n_subsample is not None:
            self.subsamples = []
        self.reverse_x_axis = reverse_x_axis

        # Initialize the source and detector plane in default positions (along the x-axis)
        source, target = self._initialize_carm()
        self.register_buffer("source", source)
        self.register_buffer("target", target)

        # Create a pose to reorient the scanner
        self.register_buffer("_reorient", reorient)

        # Create a calibration matrix that holds the detector's intrinsic parameters
        self.register_buffer(
            "_calibration",
            torch.tensor(
                [
                    [dely, 0, 0, -y0],
                    [0, delx, 0, -x0],
                    [0, 0, sdd, 0],
                    [0, 0, 0, 1],
                ]
            ),
        )

    @property
    def sdd(self):
        return self._calibration[2, 2].item()

    @property
    def delx(self):
        return self._calibration[1, 1].item()

    @property
    def dely(self):
        return self._calibration[0, 0].item()

    @property
    def x0(self):
        return -self._calibration[1, -1].item()

    @property
    def y0(self):
        return -self._calibration[0, -1].item()

    @property
    def reorient(self):
        return RigidTransform(self._reorient)

    @property
    def calibration(self):
        """A 4x4 matrix that rescales the detector plane to world coordinates."""
        return RigidTransform(self._calibration)

    @property
    def intrinsic(self):
        """The 3x3 intrinsic matrix."""
        return make_intrinsic_matrix(self).to(self.source)

# %% ../notebooks/api/02_detector.ipynb 6
@patch
def _initialize_carm(self: Detector):
    """Initialize the default position for the source and detector plane."""
    try:
        device = self.sdd.device
    except AttributeError:
        device = torch.device("cpu")

    # Initialize the source at the origin and the center of the detector plane on the positive z-axis
    source = torch.tensor([[0.0, 0.0, 0.0]], device=device)
    center = torch.tensor([[0.0, 0.0, 1.0]], device=device)

    # Use the standard basis for the detector plane
    basis = torch.tensor([[0.0, 1.0, 0.0], [1.0, 0.0, 0.0]], device=device)

    # Construct the detector plane with different offsets for even or odd heights
    # These ensure that the detector plane is centered around (0, 0, 1)
    h_off = 1.0 if self.height % 2 else 0.5
    w_off = 1.0 if self.width % 2 else 0.5

    # Construct equally spaced points along the basis vectors
    t = torch.arange(-self.height // 2, self.height // 2, device=device) + h_off
    s = torch.arange(-self.width // 2, self.width // 2, device=device) + w_off
    if self.reverse_x_axis:
        s = -s
    coefs = torch.cartesian_prod(t, s).reshape(-1, 2)
    target = torch.einsum("cd,nc->nd", basis, coefs)
    target += center

    # Add a batch dimension to the source and target so multiple poses can be passed at once
    source = source.unsqueeze(0)
    target = target.unsqueeze(0)

    if self.n_subsample is not None:
        sample = torch.randperm(self.height * self.width)[: int(self.n_subsample)]
        target = target[:, sample, :]
        self.subsamples.append(sample.tolist())
    return source, target

# %% ../notebooks/api/02_detector.ipynb 7
from .pose import RigidTransform


@patch
def forward(self: Detector, extrinsic: RigidTransform, calibration: RigidTransform):
    """Create source and target points for X-rays to trace through the volume."""
    if calibration is None:
        target = self.calibration(self.target)
    else:
        target = calibration(self.target)
    pose = self.reorient.compose(extrinsic)
    source = pose(self.source)
    target = pose(target)
    return source, target

# %% ../notebooks/api/02_detector.ipynb 9
def get_focal_length(
    intrinsic,  # Intrinsic matrix (3 x 3 tensor)
    delx: float,  # X-direction spacing (in units length)
    dely: float,  # Y-direction spacing (in units length)
) -> float:  # Focal length (in units length)
    fx = intrinsic[0, 0]
    fy = intrinsic[1, 1]
    return abs((fx * delx) + (fy * dely)).item() / 2.0

# %% ../notebooks/api/02_detector.ipynb 10
def get_principal_point(
    intrinsic,  # Intrinsic matrix (3 x 3 tensor)
    height: int,  # Y-direction length (in units pixels)
    width: int,  # X-direction length (in units pixels)
    delx: float,  # X-direction spacing (in units length)
    dely: float,  # Y-direction spacing (in units length)
):
    x0 = delx * (intrinsic[0, 2] - width / 2)
    y0 = dely * (intrinsic[1, 2] - height / 2)
    return x0.item(), y0.item()

# %% ../notebooks/api/02_detector.ipynb 11
def parse_intrinsic_matrix(
    intrinsic,  # Intrinsic matrix (3 x 3 tensor)
    height: int,  # Y-direction length (in units pixels)
    width: int,  # X-direction length (in units pixels)
    delx: float,  # X-direction spacing (in units length)
    dely: float,  # Y-direction spacing (in units length)
):
    focal_length = get_focal_length(intrinsic, delx, dely)
    x0, y0 = get_principal_point(intrinsic, height, width, delx, dely)
    return focal_length, x0, y0

# %% ../notebooks/api/02_detector.ipynb 12
def make_intrinsic_matrix(detector: Detector):
    fx = detector.sdd / detector.delx
    fy = detector.sdd / detector.dely
    u0 = detector.x0 / detector.delx + detector.width / 2
    v0 = detector.y0 / detector.dely + detector.height / 2
    return torch.tensor(
        [
            [fx, 0.0, u0],
            [0.0, fy, v0],
            [0.0, 0.0, 1.0],
        ]
    )
