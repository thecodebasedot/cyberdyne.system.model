"""Message schemas: the typed contract for every bus topic.

Dependency-free (no protobuf) but the same idea: a topic has a declared
payload shape; ``validate`` checks a payload against it. The bus runs in
``strict`` mode in tests, so a module that drifts from the contract fails
fast instead of corrupting a neighbour.

Shape mini-language:
    "float" | "int" | "str" | "bool" | "any" | "float?"  (optional)
    {"x": "float", ...}                                  object
    ["float"]                                            list of
    None                                                 payload must be None
"""
from __future__ import annotations

import fnmatch
from typing import Any

SCHEMAS: dict[str, Any] = {
    "kernel/state": {"from": "str", "to": "str", "reason": "str"},
    "kernel/fault": {"kind": "str", "error": "str", "module": "str?", "handler": "str?", "topic": "str?"},
    "kernel/module_fault": {"module": "str", "critical": "bool"},
    "kernel/module_restart": {"module": "str", "restarts": "int", "ok": "bool"},
    "sensor/odometry": {"x": "float", "y": "float", "theta": "float", "linear": "float", "angular": "float"},
    "sensor/battery": {"level": "float", "charging": "bool", "voltage": "float"},
    "sensor/imu": {"heading": "float", "angular_velocity": "float", "accel_x": "float", "accel_y": "float"},
    "perception/scan": [{"angle": "float", "distance": "float"}],
    "perception/front_clearance": "float",
    "perception/obstacles": [{"angle": "float", "distance": "float"}],
    "perception/people": [{"x": "float", "y": "float", "track_id": "int", "px": "float?", "py": "float?"}],
    "sensor/odometry_raw": {"x": "float", "y": "float", "theta": "float", "linear": "float", "angular": "float"},
    "sensor/pose_cov": "float",
    "world/scene": {"room": "any", "relations": "any", "summary": "str"},
    "world/anomaly": {"id": "str", "kind": "str", "detail": "str"},
    "language/affect": {"speaker": "any", "mood": "float", "words": "any"},
    "motion/cmd": {"linear": "float", "angular": "float"},
    "motion/cmd_applied": {"linear": "float", "angular": "float"},
    "nav/goal": {"x": "float", "y": "float", "name": "str?"},
    "nav/cancel": None,
    "nav/arrived": {"x": "float", "y": "float", "name": "str?"},
    "safety/estop": {"engage": "bool?", "reason": "str?", "confirmed": "bool?"},
    "safety/estop_state": {"engaged": "bool", "reason": "str"},
    "skill/invoke": {"skill": "str", "args": "any", "confirmed": "bool?", "trust": "str?", "request_id": "any",
                     "speaker": "any"},
    "skill/result": {"skill": "any", "ok": "bool", "output": "any", "error": "str", "request_id": "any"},
    "language/utterance": {"text": "str", "speaker": "any", "trust": "str?", "confirmed": "bool?",
                           "signature": "str?", "addressed": "bool?", "request_id": "any"},
    "brain/goal": {"goal": "str", "confirmed": "bool?", "steps": "any"},
    "human/answer": {"answer": "str", "id": "any"},
    "task/start": {"name": "str", "steps": "any", "origin": "str?", "trust": "str?", "queue": "bool?"},
    "task/pause": {"reason": "any"},
    "speech/say": {"text": "str", "voice": "str?"},
    "speech/said": {"text": "str", "voice": "str"},
    "world/observe": {"id": "str", "kind": "str", "x": "float", "y": "float", "attrs": "any"},
    "sim/hear": {"text": "str", "signature": "str?", "loudness": "float?"},
    "nav/face": {"bearing": "float"},
    "brain/drives": {"energy": "float", "curiosity": "float", "social": "float", "safety": "float", "dominant": "str"},
    "brain/attention": {"top": "any", "focus": "any"},
    "memory/dream": {"facts": "any"},
    "fleet/peers": [{"robot": "str", "last_seen": "float", "state": "any", "pose": "any", "battery": "any"}],
}

_SCALARS = {"float": (int, float), "int": int, "str": str, "bool": bool}


class SchemaError(TypeError):
    pass


def _check(shape: Any, value: Any, path: str) -> None:
    if shape == "any":
        return
    if shape is None:
        if value is not None:
            raise SchemaError(f"{path}: expected None")
        return
    if isinstance(shape, str):
        optional = shape.endswith("?")
        base = shape.rstrip("?")
        if value is None:
            if optional:
                return
            raise SchemaError(f"{path}: missing")
        t = _SCALARS[base]
        if isinstance(value, bool) and base in ("float", "int"):
            raise SchemaError(f"{path}: bool is not {base}")
        if not isinstance(value, t):
            raise SchemaError(f"{path}: expected {base}, got {type(value).__name__}")
        return
    if isinstance(shape, list):
        if not isinstance(value, (list, tuple)):
            raise SchemaError(f"{path}: expected list")
        for i, item in enumerate(value):
            _check(shape[0], item, f"{path}[{i}]")
        return
    if isinstance(shape, dict):
        if not isinstance(value, dict):
            raise SchemaError(f"{path}: expected object, got {type(value).__name__}")
        for k, sub in shape.items():
            _check(sub, value.get(k), f"{path}.{k}")
        return
    raise SchemaError(f"{path}: bad shape {shape!r}")


def schema_for(topic: str) -> Any | None:
    if topic in SCHEMAS:
        return SCHEMAS[topic]
    for pattern, shape in SCHEMAS.items():
        if "*" in pattern and fnmatch.fnmatchcase(topic, pattern):
            return shape
    return None


def validate(topic: str, payload: Any) -> bool:
    """True if the topic has a schema and the payload satisfies it; raises SchemaError otherwise.
    Topics without a schema pass (returns False)."""
    shape = schema_for(topic)
    if shape is None and topic not in SCHEMAS:
        return False
    _check(shape, payload, topic)
    return True
