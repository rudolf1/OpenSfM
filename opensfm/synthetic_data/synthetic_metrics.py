# pyre-strict
import copy
from typing import Dict, List, Tuple

import cv2
import numpy as np
import opensfm.transformations as tf
from numpy.typing import NDArray
from opensfm import align, geo, multiview, pymap, types


def points_errors(
    reference: types.Reconstruction, candidate: types.Reconstruction
) -> NDArray:
    common_points = set(reference.points.keys()).intersection(
        set(candidate.points.keys())
    )

    return np.array(
        [
            reference.points[p].coordinates - candidate.points[p].coordinates
            for p in common_points
        ]
    )


def completeness_errors(
    reference: types.Reconstruction, candidate: types.Reconstruction
) -> Tuple[float, float]:
    return (
        float(len(candidate.shots)) / float(len(reference.shots)),
        float(len(candidate.points)) / float(len(reference.points)),
    )


def gps_errors(candidate: types.Reconstruction) -> NDArray:
    errors = []
    for shot in candidate.shots.values():
        bias = candidate.biases[shot.camera.id]
        pose1 = bias.transform(shot.metadata.gps_position.value)
        pose2 = shot.pose.get_origin()
        errors.append(pose1 - pose2)
    return np.array(errors)


def gcp_errors(
    candidate: types.Reconstruction, gcps: Dict[str, pymap.GroundControlPoint]
) -> NDArray:
    errors = []
    for gcp in gcps.values():
        if not gcp.lla:
            continue

        triangulated = multiview.triangulate_gcp(gcp, candidate.shots, 1.0, 0.1)
        if triangulated is None:
            continue

        gcp_enu = candidate.reference.to_topocentric(*gcp.lla_vec)
        errors.append(triangulated - gcp_enu)
    return np.array(errors)


def position_errors(
    reference: types.Reconstruction, candidate: types.Reconstruction
) -> NDArray:
    common_shots = set(reference.shots.keys()).intersection(set(candidate.shots.keys()))
    errors = []
    for s in common_shots:
        pose1 = reference.shots[s].pose.get_origin()
        pose2 = candidate.shots[s].pose.get_origin()
        errors.append(pose1 - pose2)
    return np.array(errors)


def rotation_errors(
    reference: types.Reconstruction, candidate: types.Reconstruction
) -> NDArray:
    common_shots = set(reference.shots.keys()).intersection(set(candidate.shots.keys()))
    errors = []
    for s in common_shots:
        pose1 = reference.shots[s].pose.get_rotation_matrix()
        pose2 = candidate.shots[s].pose.get_rotation_matrix()
        difference = np.transpose(pose1).dot(pose2)
        rodrigues = cv2.Rodrigues(difference)[0].ravel()
        angle = np.linalg.norm(rodrigues)
        errors.append(angle)
    return np.array(errors)


def find_alignment(
    points0: List[NDArray], points1: List[NDArray]
) -> Tuple[float, NDArray, NDArray]:
    """Compute similarity transform between point sets.

    Returns (s, A, b) such that ``points1 = s * A * points0 + b``
    """
    v0, v1 = [], []
    for p0, p1 in zip(points0, points1):
        if p0 is not None and p1 is not None:
            v0.append(p0)
            v1.append(p1)
    v0 = np.array(v0).T
    v1 = np.array(v1).T
    M = tf.affine_matrix_from_points(v0, v1, shear=False)
    s = np.linalg.det(M[:3, :3]) ** (1.0 / 3.0)
    A = M[:3, :3] / s
    b = M[:3, 3]
    return s, A, b


def aligned_to_reference(
    reference: types.Reconstruction, reconstruction: types.Reconstruction
) -> types.Reconstruction:
    """Align a reconstruction to a reference."""
    coords1, coords2 = [], []
    for point1 in reconstruction.points.values():
        point2 = reference.points.get(point1.id)
        if point2 is not None:
            coords1.append(point1.coordinates)
            coords2.append(point2.coordinates)

    if len(coords1) == 0 or len(coords2) == 0:
        for shot1 in reconstruction.shots.values():
            shot2 = reference.shots.get(shot1.id)
            if shot2 is not None:
                coords1.append(shot1.pose.get_origin())
                coords2.append(shot2.pose.get_origin())

    s, A, b = find_alignment(coords1, coords2)
    aligned = copy.deepcopy(reconstruction)
    align.apply_similarity(aligned, s, A, b)
    return aligned


def change_geo_reference(
    reconstruction: types.Reconstruction,
    latitude: float,
    longitude: float,
    altitude: float,
) -> types.Reconstruction:
    """Change the geo reference of a reconstruction.

    This assumes that the new lla is close enough that rotation differences
    between references can be ignored.
    """
    t_old_new = reconstruction.reference.to_topocentric(latitude, longitude, altitude)

    s = 1.0
    A = np.eye(3)
    b = -np.array(t_old_new)
    aligned = copy.deepcopy(reconstruction)
    aligned.reference = geo.TopocentricConverter(latitude, longitude, altitude)
    align.apply_similarity(aligned, s, A, b)
    for shot in aligned.shots.values():
        if shot.metadata.gps_position.has_value:
            shot.metadata.gps_position.value = shot.metadata.gps_position.value + b
    return aligned


def rmse(errors: NDArray) -> float:
    return np.sqrt(np.mean(errors**2))


def mad(errors: NDArray) -> float:
    return np.median(np.absolute(errors - np.median(errors)))
