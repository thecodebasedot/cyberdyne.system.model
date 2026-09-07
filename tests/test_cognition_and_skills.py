from cyberdyne.cognition.behavior_tree import Action, Condition, Inverter, Selector, Sequence, Status
from cyberdyne.cognition.planner import RulePlanner
from cyberdyne.kernel.clock import SimClock
from cyberdyne.language.interpreter import RuleInterpreter
from cyberdyne.memory.episodic import EpisodicMemory
from cyberdyne.memory.working import WorkingMemory
from cyberdyne.skills.registry import SkillRegistry
from tests.conftest import run


def test_behavior_tree_semantics():
    trace = []
    tree = Selector(
        "root",
        Sequence("a", Condition("false", lambda bb: False), Action("never", lambda bb: trace.append("never"))),
        Sequence("b", Inverter(Condition("false", lambda bb: False)),
                 Action("run", lambda bb: trace.append("run") or Status.RUNNING)),
        Action("fallback", lambda bb: trace.append("fallback")),
    )
    assert run(tree.tick({})) == Status.RUNNING
    assert trace == ["run"] and tree.last_active == "b"


def test_rule_planner_and_interpreter():
    plan = run(RulePlanner().plan("goto 3 4 kitchen", {}))
    assert plan.steps[0].args == {"x": 3.0, "y": 4.0, "name": "kitchen"}
    plan = run(RulePlanner().plan("patrol", {"patrol": [{"x": 1, "y": 1}, {"x": 2, "y": 2}]}))
    assert [s.args["name"] for s in plan.steps] == ["waypoint_0", "waypoint_1"]
    it = RuleInterpreter()
    assert it.parse("Go to 6, 7!").args == {"x": 6.0, "y": 7.0}
    assert it.parse("jao 1 2").skill == "goto"
    assert it.parse("thamo").skill == "estop"
    assert it.parse("charge koro").args == {"charger": True}
    assert it.parse("mone rakho owner = ijtihad").args == {"key": "owner", "value": "ijtihad"}
    assert it.parse("koyta baje").skill == "time"
    assert it.parse("blah blah") is None


def test_memory_ttl_and_recall():
    clock = SimClock()
    wm = WorkingMemory(clock)
    wm.set("k", 1, ttl=1.0)
    clock.advance(0.5)
    assert wm.get("k") == 1
    clock.advance(0.6)
    assert wm.get("k") is None
    em = EpisodicMemory(capacity=8)
    for i in range(10):
        em.remember(float(i), "x", f"e{i}", importance=0.1 if i % 2 else 0.9)
    assert len(em) < 10
    top = em.query(now=10.0, limit=1)[0]
    assert top.importance == 0.9


def test_skill_registry_loads_builtins():
    reg = SkillRegistry()
    names = {s.manifest.name for s in reg.load_builtin()}
    assert {"time", "echo", "status", "goto", "estop", "estop_reset", "remember", "recall"} <= names
    assert reg.get("estop_reset").manifest.permission == "skill.estop_reset"
