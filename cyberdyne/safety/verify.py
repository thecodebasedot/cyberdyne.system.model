"""Exhaustive verification of the safety core's finite-state behaviour.

This is a small model checker over the *real* transition table and the
*real* envelope/gate rules (not a hand-copied model). It enumerates every
abstract state the gate can be in and every event, and checks invariants
that must hold everywhere:

    I1  e-stop engaged                -> applied linear = angular = 0
    I2  battery critical, not charging -> applied linear <= 0
    I3  perception stale              -> applied linear <= 0
    I4  front clearance < stop dist   -> applied linear <= 0
    I5  |applied| never exceeds the envelope limits
    I6  ESTOP leaves only to DIAGNOSTIC or SHUTDOWN; SHUTDOWN is terminal
    I7  every operational state can reach ESTOP in one step
    I8  BOOT reaches IDLE only through DIAGNOSTIC

``docs/formal/SafetyGate.tla`` states the same properties for TLC.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from ..kernel.config import SafetyConfig
from ..kernel.state import _TRANSITIONS, SystemState
from .envelope import SafetyEnvelope


@dataclass
class Verdict:
    ok: bool
    states_checked: int
    violations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "states_checked": self.states_checked, "violations": self.violations}


def gate_decision(env: SafetyEnvelope, cfg: SafetyConfig, *, req_lin: float, req_ang: float, estop: bool,
                  battery: float, charging: bool, stale: bool, clearance: float) -> tuple[float, float]:
    """The SafetyGate.tick decision, extracted so the checker and the module share it."""
    lin, ang = req_lin, req_ang
    if estop:
        lin, ang = 0.0, 0.0
    if battery <= cfg.battery_critical and not charging:
        lin = min(lin, 0.0)
    lin, ang, _ = env.clamp(lin, ang, front_clearance=clearance)
    if lin > 0 and stale:
        lin = 0.0
    return lin, ang


def check_gate(cfg: SafetyConfig | None = None) -> Verdict:
    cfg = cfg or SafetyConfig()
    env = SafetyEnvelope(cfg)
    lims = cfg.max_linear, cfg.max_angular
    v: list[str] = []
    n = 0
    req = [-2 * lims[0], -0.5 * lims[0], 0.0, 0.5 * lims[0], 2 * lims[0]]
    reqa = [-2 * lims[1], 0.0, 2 * lims[1]]
    batt = [0.0, cfg.battery_critical, cfg.battery_critical + 0.01, 1.0]
    clr = [0.0, cfg.obstacle_stop_distance - 0.01, cfg.obstacle_stop_distance, 10.0]
    for rl, ra, es, b, ch, st, c in itertools.product(req, reqa, (False, True), batt, (False, True),
                                                      (False, True), clr):
        n += 1
        lin, ang = gate_decision(env, cfg, req_lin=rl, req_ang=ra, estop=es, battery=b, charging=ch, stale=st,
                                 clearance=c)
        ctx = f"req=({rl},{ra}) estop={es} batt={b} charging={ch} stale={st} clearance={c} -> ({lin},{ang})"
        if es and (lin != 0.0 or ang != 0.0):
            v.append("I1 " + ctx)
        if b <= cfg.battery_critical and not ch and lin > 0:
            v.append("I2 " + ctx)
        if st and lin > 0:
            v.append("I3 " + ctx)
        if c < cfg.obstacle_stop_distance and lin > 0:
            v.append("I4 " + ctx)
        if abs(lin) > lims[0] + 1e-9 or abs(ang) > lims[1] + 1e-9:
            v.append("I5 " + ctx)
    return Verdict(not v, n, v)


def check_state_machine(table: dict[SystemState, set[SystemState]] | None = None) -> Verdict:
    t = table or _TRANSITIONS
    v: list[str] = []
    S = SystemState
    if not t[S.ESTOP] <= {S.DIAGNOSTIC, S.SHUTDOWN}:
        v.append(f"I6 ESTOP may leave to {sorted(x.value for x in t[S.ESTOP])}")
    if t[S.SHUTDOWN]:
        v.append("I6 SHUTDOWN is not terminal")
    for s in (S.IDLE, S.ACTIVE, S.CHARGING, S.DIAGNOSTIC):
        if S.ESTOP not in t[s]:
            v.append(f"I7 {s.value} cannot reach ESTOP in one step")
    if S.IDLE in t[S.BOOT] or S.ACTIVE in t[S.BOOT]:
        v.append("I8 BOOT skips DIAGNOSTIC")
    # reachability: every state except SHUTDOWN can still reach SHUTDOWN (no live-lock in a corner)
    for s in S:
        seen, frontier = {s}, [s]
        while frontier:
            cur = frontier.pop()
            for nxt in t[cur]:
                if nxt not in seen:
                    seen.add(nxt)
                    frontier.append(nxt)
        if S.SHUTDOWN not in seen:
            v.append(f"I6 {s.value} cannot reach SHUTDOWN")
    return Verdict(not v, len(t), v)


def verify_all(cfg: SafetyConfig | None = None) -> dict:
    g, s = check_gate(cfg), check_state_machine()
    return {"ok": g.ok and s.ok, "gate": g.to_dict(), "state_machine": s.to_dict()}
