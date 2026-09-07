"""Phase 4: tasks, routines, home hub, teaching, macros, user model, tuner, persistence."""
import json

from cyberdyne.cognition.constitution import Constitution
from cyberdyne.home.hub import VirtualHub
from cyberdyne.kernel.config import HomeConfig, RobotConfig
from cyberdyne.learning.tuner import PARAM_BOUNDS, ControllerTuner
from cyberdyne.learning.user_model import UserModel
from cyberdyne.memory.persist import Store
from cyberdyne.safety.permissions import PermissionPolicy
from cyberdyne.sim.scenario import load_scenario
from cyberdyne.skills.authoring import render, validate_macro
from cyberdyne.tasks.model import Task, TaskStep
from tests.conftest import booted, run


def test_task_step_shapes_and_status():
    s = TaskStep.from_dict({"skill": "goto", "args": {"x": 1, "y": 2}})
    assert s.kind == "goto" and s.args == {"x": 1, "y": 2}
    s = TaskStep.from_dict({"skill": "device", "args": {"on": True}})
    assert s.kind == "skill" and s.args["skill"] == "device"
    t = Task("t", [s])
    assert t.current is s and not t.active and t.to_dict()["steps"] == 1


def test_chores_scenario_end_to_end():
    async def go():
        rt = await booted(load_scenario("chores"))
        log = {t: [] for t in ("task/done", "task/failed", "task/cancelled", "routine/fired", "speech/said",
                               "home/device", "skill/defined", "skill/result")}
        for t in log:
            rt.bus.subscribe(t, lambda m, t=t: log[t].append(m.payload))
        await rt.run(100)
        await rt.shutdown()
        return rt, log
    rt, log = run(go())
    assert [t["name"] for t in log["task/done"]] == ["rounds", "corner_run", "lights_out"]
    assert [t["name"] for t in log["task/cancelled"]] == ["rounds"]        # routine run superseded by the user
    assert log["task/cancelled"][0]["error"] == "superseded"
    assert not log["task/failed"]
    assert [r["name"] for r in log["routine/fired"]][:2] == ["reminder:check the oven", "hourly rounds"]
    assert any(s["text"] == "Reminder: check the oven" for s in log["speech/said"])
    assert [d["id"] for d in log["home/device"]][:2] == ["kitchen_light", "kitchen_light"]
    assert log["skill/defined"][0]["name"] == "lights_out"
    teach = [r for r in log["skill/result"] if r["skill"] == "teach"]
    assert teach[-1]["output"]["task"] == "corner_run" and teach[-1]["output"]["steps"] == 2
    assert "corner_run" in rt.tasks.library and rt.tasks.completed == 3
    assert any(e.action == "define" for e in rt.safety.audit.entries)


def test_task_pauses_for_battery_and_resumes(config):
    config.world.battery_start = 0.32
    config.brain.battery_low = 0.3
    config.brain.battery_full = 0.9
    config.world.battery_drain_moving = 0.02
    config.brain.patrol = []

    async def go():
        rt = await booted(config)
        events = []
        for t in ("task/paused", "task/resumed", "task/done", "kernel/state"):
            rt.bus.subscribe(t, lambda m, t=t: events.append((t, m.payload)))
        await rt.bus.publish("task/start", {"name": "far", "steps": [{"kind": "goto", "args": {"x": 8.5, "y": 8.5}},
                                                                     {"kind": "goto", "args": {"x": 1.0, "y": 8.5}}]})
        await rt.run(160)
        await rt.shutdown()
        return events
    events = run(go())
    kinds = [t for t, _ in events]
    assert "task/paused" in kinds and "task/resumed" in kinds and "task/done" in kinds
    assert kinds.index("task/paused") < kinds.index("task/resumed") < kinds.index("task/done")
    assert any(t == "kernel/state" and p["to"] == "charging" for t, p in events)
    paused = next(p for t, p in events if t == "task/paused")
    assert paused["reason"] == "battery"


def test_task_retry_timeout_and_subtask(config):
    config.brain.patrol = []
    config.tasks = [{"name": "blink", "steps": [{"kind": "wait", "args": {"seconds": 0.5}}]}]

    async def go():
        rt = await booted(config)
        log = []
        for t in ("task/retry", "task/failed", "task/done", "task/step"):
            rt.bus.subscribe(t, lambda m, t=t: log.append((t, m.payload)))
        await rt.bus.publish("task/start", {"name": "composed", "steps": [
            {"kind": "task", "args": {"name": "blink"}},
            {"kind": "skill", "args": {"skill": "echo", "args": {"text": "hi"}}},
            {"kind": "skill", "args": {"skill": "nope"}, "retries": 1},
        ]})
        await rt.run(5)
        await rt.bus.publish("task/start", {"name": "slow", "steps": [
            {"kind": "goto", "args": {"x": 9.0, "y": 9.0}, "timeout": 1.0, "retries": 0}]})
        await rt.run(3)
        await rt.shutdown()
        return log
    log = run(go())
    kinds = [t for t, _ in log]
    steps = [p["kind"] for t, p in log if t == "task/step"]
    assert steps[:3] == ["task", "wait", "skill"]                      # sub-task spliced in place
    assert kinds.count("task/retry") == 1
    failed = [p for t, p in log if t == "task/failed"]
    assert len(failed) == 2 and "unknown skill" in failed[0]["error"] and "timed out" in failed[1]["error"]


def test_home_hub_room_lookup():
    hub = VirtualHub(HomeConfig(devices=[{"id": "k", "kind": "light", "room": "kitchen"},
                                         {"id": "l", "kind": "light", "room": "living"},
                                         {"id": "f", "kind": "fan", "room": "living", "name": "ceiling fan"}]))
    assert [d.id for d in hub.find("light")] == ["k", "l"]
    assert [d.id for d in hub.find("light", room="Living")] == ["l"]
    assert [d.id for d in hub.find(name="ceiling fan")] == ["f"]
    assert run(hub.set_state(hub.devices["f"], on=True, speed=2)) == {"on": True, "speed": 2}
    assert hub.commands == 1


def test_macro_validation_and_render():
    cfg = RobotConfig()
    cfg.safety.keep_out = [{"x": 0, "y": 9, "w": 1, "h": 1, "name": "stairs"}]
    c = Constitution(cfg, PermissionPolicy(), {"goto", "device", "echo"})
    ok = {"name": "greet_room", "steps": [{"kind": "goto", "args": {"x": "{x}", "y": "{y}"}},
                                          {"kind": "skill", "args": {"skill": "echo", "args": {"text": "hi {who}"}}}]}
    assert validate_macro(ok, c, {"goto"}) == []
    bad = {"name": "Bad Name", "steps": [{"kind": "goto", "args": {"x": 0.5, "y": 9.5}},
                                         {"kind": "skill", "args": {"skill": "fly"}}]}
    problems = validate_macro(bad, c, set())
    assert any("identifier" in p for p in problems) and any("keep_out" in p for p in problems)
    assert any("unknown_skill" in p for p in problems)
    dup = {"name": "goto", "steps": [{"kind": "wait", "args": {}}]}
    assert validate_macro(dup, c, {"goto"}) == ["goto already exists"]
    assert render({"x": "{x}", "text": "hi {who}", "n": 3}, {"x": 2.5, "who": "Ijtihad"}) == \
        {"x": 2.5, "text": "hi Ijtihad", "n": 3}


def test_self_modify_is_forbidden_by_default(config):
    async def go():
        rt = await booted(config)
        results = []
        rt.bus.subscribe("skill/result", lambda m: results.append(m.payload))
        await rt.bus.publish("skill/invoke", {"skill": "define_skill", "confirmed": True,
                                              "args": {"name": "x", "steps": [{"kind": "wait", "args": {}}]}})
        await rt.run(1)
        await rt.shutdown()
        return results
    res = run(go())
    assert res[0]["ok"] is False and "forbidden" in res[0]["error"]


def test_user_model_habits_and_persistence(tmp_path):
    m = UserModel()
    for _ in range(3):
        m.observe("Ijtihad", "device:kind=light,on=True", 19)
    m.observe("Ijtihad", "goto:name=kitchen", 8)
    assert m.habits("Ijtihad", 20) == [("device:kind=light,on=True", 3)]
    assert m.habits("Ijtihad", 8) == []
    assert list(m.preferences("Ijtihad"))[0] == "device:kind=light,on=True"
    m2 = UserModel.from_dict(json.loads(json.dumps(m.to_dict())))
    assert m2.habits("Ijtihad", 21) == m.habits("Ijtihad", 21)
    store = Store(tmp_path)
    store.save_macro({"name": "m", "description": "", "steps": [], "params": {}})
    store.save_task("route", [{"kind": "wait", "args": {"seconds": 1}}])
    assert store.load("macros", {})["m"]["name"] == "m" and store.load("tasks", {})["route"][0]["kind"] == "wait"


def test_learning_persists_across_runs(tmp_path):
    cfg = RobotConfig()
    cfg.brain.patrol = []
    cfg.learning.data_dir = str(tmp_path)
    cfg.safety.permissions = {"self.modify": "confirm"}

    async def first():
        rt = await booted(cfg)
        await rt.say("mone rakho owner = ijtihad")
        await rt.bus.publish("skill/invoke", {"skill": "define_skill", "confirmed": True, "args": {
            "name": "blink", "description": "", "steps": [{"kind": "wait", "args": {"seconds": 0.1}}]}})
        await rt.run(2)
        await rt.shutdown()

    async def second():
        rt = await booted(cfg)
        assert rt.ctx.extras["memory"].semantic.get("self", "owner") == "ijtihad"
        assert "blink" in rt.ctx.extras["skills"].names()
        await rt.shutdown()
    run(first())
    run(second())


def test_tuner_runs_short_episodes():
    cfg = load_scenario("patrol")
    tuner = ControllerTuner(cfg, episode_seconds=20.0, seed=1)
    res = run(tuner.tune(episodes=2))
    assert len(res.history) == 2 and set(res.best) == set(PARAM_BOUNDS)
    for k, v in res.best.items():
        lo, hi = PARAM_BOUNDS[k]
        assert lo <= v <= hi
    assert abs(res.best_score - max(h["score"] for h in res.history)) < 0.01
