"""Phase 2: council, constitution, mental simulation, LLM seams, semantic memory."""
import json

from cyberdyne.cognition.constitution import Constitution
from cyberdyne.cognition.llm import LLMError, ScriptedBackend, extract_json
from cyberdyne.cognition.llm_planner import LLMPlanner
from cyberdyne.cognition.planner import Plan, PlanStep, RulePlanner
from cyberdyne.cognition.simulate import MentalSimulator
from cyberdyne.kernel.config import RobotConfig
from cyberdyne.language.llm_interpreter import LLMInterpreter
from cyberdyne.memory.episodic import EpisodicMemory
from cyberdyne.memory.index import TextIndex
from cyberdyne.memory.semantic import KnowledgeGraph
from cyberdyne.runtime import Runtime
from cyberdyne.safety.permissions import PermissionPolicy
from cyberdyne.sim.scenario import load_scenario
from cyberdyne.world_model.grid import OccupancyGrid
from tests.conftest import booted, run


def _cfg() -> RobotConfig:
    cfg = RobotConfig()
    cfg.world.obstacles = [{"x": 4.0, "y": 3.0, "w": 2.0, "h": 1.0, "name": "sofa"}]
    cfg.safety.keep_out = [{"x": 0.0, "y": 8.5, "w": 1.5, "h": 1.5, "name": "stairs"}]
    return cfg


def test_constitution_rules():
    c = Constitution(_cfg(), PermissionPolicy(), known_skills={"goto", "estop_reset", "echo"})
    v = c.review(Plan("x", [PlanStep("goto", {"x": 0.5, "y": 9.5}),
                            PlanStep("goto", {"x": 50, "y": 1}),
                            PlanStep("goto", {"x": 5.0, "y": 3.5}),
                            PlanStep("fly", {}),
                            PlanStep("estop_reset", {})]))
    rules = {(x.rule, x.step) for x in v}
    assert {("keep_out", 0), ("out_of_bounds", 1), ("inside_obstacle", 2), ("unknown_skill", 3),
            ("needs_confirmation", 4)} <= rules
    assert not c.review(Plan("x", [PlanStep("estop_reset", {})]), human_confirmed=True)
    assert {x.rule for x in Constitution.hard(v)} == {"keep_out", "out_of_bounds", "inside_obstacle", "unknown_skill"}
    assert c.review(Plan("x", [PlanStep("echo", {})] * 13))[0].rule == "max_steps"


def test_mental_simulation_predicts_reachability():
    cfg = _cfg()
    grid = OccupancyGrid(10, 10, 0.5)
    for r in range(0, 15):                        # believed wall x=[5,5.5) y=[0,7.5)
        grid.cells[r * grid.cols + 10] = 5.0
    sim = MentalSimulator(cfg)
    pose = {"x": 1.0, "y": 1.0, "theta": 0.0}
    ok = sim.rollout(Plan("a", [PlanStep("goto", {"x": 3.0, "y": 1.0})]), grid, pose, 0.5)
    assert ok.feasible and ok.total_time > 0 and ok.battery_after < 0.5
    around = sim.rollout(Plan("b", [PlanStep("goto", {"x": 8.0, "y": 1.0})]), grid, pose, 0.5)
    assert around.feasible and around.total_distance > 9.0          # went around via the top gap
    for r in range(15, 20):
        grid.cells[r * grid.cols + 10] = 5.0                         # now fully walled off
    blocked = sim.rollout(Plan("c", [PlanStep("goto", {"x": 8.0, "y": 1.0})]), grid, pose, 0.5)
    assert not blocked.feasible and "no path" in blocked.outcomes[0].note


def test_llm_planner_parses_and_falls_back():
    good = json.dumps({"rationale": "straight there", "steps": [{"skill": "goto", "args": {"x": 2, "y": 3}}]})
    backend = ScriptedBackend(["Sure! ```json\n" + good + "\n```", "I cannot produce JSON today"])
    planner = LLMPlanner(backend)
    p1 = run(planner.plan("go to the window", {}))
    assert p1.steps[0].args == {"x": 2, "y": 3} and p1.rationale == "straight there"
    p2 = run(planner.plan("charge", {"charger": {"x": 0.5, "y": 0.5}}))
    assert p2.rationale.startswith("[rule fallback") and p2.steps[0].args["name"] == "charger"
    assert planner.fallbacks == 1
    p3 = run(planner.plan("patrol", {"patrol": []}))                 # backend exhausted -> LLMError -> rules
    assert p3.rationale.startswith("[rule fallback")
    assert extract_json('prefix {"a": {"b": 1}} suffix') == {"a": {"b": 1}}


def test_llm_interpreter_rules_first_then_model():
    backend = ScriptedBackend([json.dumps({"skill": "plan", "args": {"goal": "tidy the kitchen"}, "confidence": 0.9}),
                               json.dumps({"skill": None, "args": {}, "confidence": 0})])
    it = LLMInterpreter(backend, [{"name": "plan", "description": "free goal", "args": {"goal": "text"}}])
    assert run(it.parse_async("thamo")).skill == "estop" and backend.calls == []
    intent = run(it.parse_async("ranna ghor ta guchiye dao"))
    assert intent.skill == "plan" and intent.args["goal"] == "tidy the kitchen"
    assert run(it.parse_async("nice weather")) is None
    assert it.llm_calls == 2


def test_knowledge_graph_and_text_index():
    kg = KnowledgeGraph()
    kg.add("self", "owner", "Ijtihad", "user", 1.0)
    kg.add("kitchen", "location", "3,4")
    kg.add("self", "owner", "Ijtihad Emon")                          # newest wins
    assert kg.get("self", "owner") == "Ijtihad Emon" and len(kg) == 2
    assert [f.subject for f in kg.neighbours("ijtihad emon")] == ["self"]
    assert kg.forget("ijtihad") == 1 and kg.get("self", "owner") is None
    idx = TextIndex()
    idx.add(1, "battery low heading to charger")
    idx.add(2, "user said go to the kitchen")
    idx.add(3, "estop engaged by dashboard")
    assert idx.search("charging battery")[0][0] == 1
    assert idx.search("kitchen")[0][0] == 2


def test_episodic_search_and_forget():
    em = EpisodicMemory()
    em.remember(1.0, "nav/arrived", "arrived at kitchen")
    em.remember(2.0, "safety/estop_state", "e-stop engaged: dashboard")
    em.remember(3.0, "language/utterance", "user said: owner is Ijtihad")
    assert em.search("kitchen arrival")[0].summary == "arrived at kitchen"
    assert em.forget("ijtihad") == 1 and len(em) == 2
    assert all("Ijtihad" not in e.summary for e in em.search("owner"))


def test_council_blocks_keep_out_and_accepts_answer():
    async def go():
        rt = await booted(load_scenario("council"))
        events = []
        for t in ("brain/question", "brain/question_closed", "nav/arrived", "brain/plan_done"):
            rt.bus.subscribe(t, lambda m: events.append((m.topic, m.payload)))
        await rt.run(2)
        q = [p for t, p in events if t == "brain/question"]
        assert q and "keep_out" in q[0]["question"] and rt.brain.pending is not None
        await rt.run(20)
        assert [p["outcome"] for t, p in events if t == "brain/question_closed"] == ["regoal"]
        assert any(t == "nav/arrived" and p["name"] == "corner" for t, p in events)
        assert any(t == "brain/plan_done" for t, _ in events)
        await rt.run(15)
        closed = [p["outcome"] for t, p in events if t == "brain/question_closed"]
        assert closed == ["regoal", "cancelled"]
        await rt.shutdown()
        return rt
    rt = run(go())
    actions = [e.action for e in rt.safety.audit.entries]
    assert actions.count("decision") == 3 and "plan.adopt" in actions and "answer" in actions
    assert rt.report()["audit"]["chain_ok"]


def test_proceed_cannot_override_hard_rules_but_can_override_confirmation():
    cfg = _cfg()
    cfg.world.battery_start = 1.0

    async def go():
        rt = await booted(cfg)
        closed = []
        rt.bus.subscribe("brain/question_closed", lambda m: closed.append(m.payload["outcome"]))
        await rt.bus.publish("brain/goal", {"goal": "goto 0.5 9.5"})          # stairs
        await rt.run(1)
        await rt.bus.publish("human/answer", {"answer": "proceed"})
        await rt.run(1)
        assert closed == ["refused"] and rt.brain.plan is None
        await rt.bus.publish("safety/estop", {"engage": True, "reason": "test"})
        await rt.run(1)
        await rt.bus.publish("safety/estop", {"engage": False, "confirmed": True})
        await rt.run(1)
        rt.brain.council.threshold = 0.99                                       # force a confidence question
        await rt.bus.publish("brain/goal", {"goal": "goto 8 1"})
        await rt.run(1)
        assert rt.brain.pending is not None and "confident" in rt.brain.pending.question
        await rt.bus.publish("human/answer", {"answer": "yes"})
        await rt.run(1)
        assert closed[-1] == "proceed" and rt.brain.plan is not None
        await rt.shutdown()
    run(go())


def test_runtime_with_scripted_llm_and_skills():
    cfg = _cfg()
    cfg.brain.llm = "scripted"
    replies = [json.dumps({"skill": "plan", "args": {"goal": "goto 2 2 window"}, "confidence": 0.95}),
               json.dumps({"rationale": "window is at (2,2)",
                           "steps": [{"skill": "goto", "args": {"x": 2, "y": 2, "name": "window"}}]})]

    async def go():
        rt = Runtime(cfg, llm=ScriptedBackend(replies))
        await rt.boot()
        results = []
        rt.bus.subscribe("skill/result", lambda m: results.append(m.payload))
        await rt.say("janala-r kache jao")                 # unknown to the rules -> LLM -> plan skill -> LLM planner
        await rt.run(15)
        assert rt.brain.council.history[-1].plan.rationale == "window is at (2,2)"
        await rt.say("explain")
        await rt.say("mone rakho owner = ijtihad")
        await rt.say("khojo window")
        await rt.say("bhule jao ijtihad")
        await rt.run(2)
        by = {r["skill"]: r for r in results}
        assert by["explain"]["output"]["decision"] == "approved"
        assert by["remember"]["output"]["fact"] == "self owner ijtihad"
        assert any("window" in e["summary"] for e in by["recall"]["output"]["episodes"])
        assert by["forget"]["output"]["facts"] == 1
        assert rt.ctx.extras["memory"].semantic.get("self", "owner") is None
        await rt.shutdown()
    run(go())


def test_backend_factory():
    assert __import__("cyberdyne.cognition.llm", fromlist=["build_backend"]).build_backend("none") is None
    try:
        from cyberdyne.cognition.llm import build_backend
        build_backend("nope")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown backend must raise")
    try:
        RulePlanner()
        raise LLMError("x")
    except LLMError:
        pass
