import pytest

from cyberdyne.kernel.config import SafetyConfig
from cyberdyne.safety.audit import AuditEntry, AuditLog
from cyberdyne.safety.envelope import SafetyEnvelope
from cyberdyne.safety.permissions import PermissionDenied, PermissionPolicy, Tier


def test_envelope_clamps_speed_and_obstacles():
    env = SafetyEnvelope(SafetyConfig(max_linear=0.5, max_angular=1.0, obstacle_stop_distance=0.3))
    lin, ang, v = env.clamp(2.0, -3.0)
    assert (lin, ang) == (0.5, -1.0) and {x.rule for x in v} == {"max_linear", "max_angular"}
    lin, ang, v = env.clamp(0.4, 0.0, front_clearance=0.1)
    assert lin == 0.0 and v[0].rule == "obstacle_stop"
    lin, _, v = env.clamp(-0.4, 0.0, front_clearance=0.1)      # reversing away is allowed
    assert lin == -0.4 and not v


def test_envelope_keep_out_zone():
    env = SafetyEnvelope(SafetyConfig(keep_out=[{"x": 2.0, "y": 0.0, "w": 1.0, "h": 1.0, "name": "stairs"}]))
    pose = {"x": 1.6, "y": 0.5, "theta": 0.0}
    lin, _, v = env.clamp(0.8, 0.0, pose=pose)
    assert lin == 0.0 and v[0].rule == "keep_out" and v[0].detail == "stairs"
    pose["theta"] = 3.14159
    lin, _, v = env.clamp(0.8, 0.0, pose=pose)
    assert lin == 0.8 and not v


def test_permission_tiers_most_specific_wins():
    pol = PermissionPolicy({"skill.*": "log", "skill.danger*": "confirm"})
    assert pol.tier("skill.time") == Tier.FREE          # builtin default beats the glob
    assert pol.tier("skill.other") == Tier.LOG
    assert pol.tier("skill.danger_zone") == Tier.CONFIRM
    assert pol.tier("safety.reconfigure") == Tier.FORBIDDEN
    with pytest.raises(PermissionDenied):
        pol.check("skill.danger_zone")
    assert pol.check("skill.danger_zone", confirmed=True) == Tier.CONFIRM
    with pytest.raises(PermissionDenied):
        pol.check("self.modify", confirmed=True)


def test_audit_chain_detects_tampering():
    log = AuditLog()
    for i in range(5):
        log.record(float(i), "test", "step", i=i)
    assert log.verify() == (True, -1)
    e = log.entries[2]
    log.entries[2] = AuditEntry(e.seq, e.ts, e.actor, "tampered", e.detail, e.prev_hash, e.hash)
    assert log.verify() == (False, 2)
