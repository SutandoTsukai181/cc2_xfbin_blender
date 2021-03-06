import math
from typing import Tuple

from mathutils import Euler, Vector


def pos_cm_to_m(pos: Tuple[float, float, float]) -> Vector:
    # From centimeter to meter
    return Vector(pos) * 0.01


def pos_cm_to_m_tuple(pos: Tuple[float]) -> Tuple[float]:
    # From centimeter to meter
    return tuple(map(lambda x: x * 0.01, pos))


def pos_to_blender(pos) -> Vector:
    return Vector(pos)


def pos_scaled_to_blender(pos) -> Vector:
    return pos_cm_to_m(pos_to_blender(pos))


def rot_to_blender(rot):
    return Euler(tuple(map(lambda x: math.radians(x), rot)), 'ZYX')


def uv_to_blender(uv):
    return (uv[0], 1.0 - uv[1])


def pos_m_to_cm(pos: Vector) -> Tuple[float, float, float]:
    # From meter to centimeter
    return (pos * 100)[:]


def pos_m_to_cm_tuple(pos: Tuple[float]) -> Tuple[float]:
    # From meter to centimeter
    return tuple(map(lambda x: x * 100, pos))


def pos_from_blender(pos: Vector) -> Tuple[float, float, float]:
    return pos[:]


def pos_scaled_from_blender(pos: Vector) -> Tuple[float, float, float]:
    return pos_from_blender(Vector(pos_m_to_cm(pos)))


def rot_from_blender(rot: Euler) -> Tuple[float, float, float]:
    return tuple(map(lambda x: math.degrees(x), rot))


def uv_from_blender(uv):
    return (uv[0], 1.0 - uv[1])
