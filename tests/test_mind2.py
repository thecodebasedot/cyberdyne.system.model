"""Batch 3: attention/investigation, drives/exploration, decomposition, whatif, dreaming, personality,
LLM critic, LLM-drafted macros, conversation context, Bangla script."""
import json

from cyberdyne.cognition.llm import ScriptedBackend
from cyberdyne.cognition.planner import RulePlanner
from cyberdyne.kernel.config import PersonalityConfig
from cyberdyne.language.interpreter import RuleInterpreter, transliterate
from cyberdyne.language.llm_interpreter import LLMInterpreter
from cyberdyne.language.personality import Personality
from cyberdyne.memory.episodic import EpisodicMemory
from cyberdyne.runtime import Runtime
from cyberdyne.sim.scenario import load_scenario
from tests.conftest import booted, run


def test_curiosity_explores_frontiers_and_dreams():
    cfg = load_scenario("home")
    cfg.events = []
    cfg.brain.patrol = []

    async def go():
        rt = await booted(cfg)
        dreams = []
        rt.bus.subscribe("memory/dream", lambda m: dreams.append(m.payload))
        explored0 = rt.world_model.grid.explored_fraction()
        await rt.run(90)
        await rt.shutdown()
        return rt, explored0, dreams
    rt, explored0, dreams = run(go())
    assert rt.brain.explorations >= 3 and rt.world_model.grid.explored_fraction() > explored0 + 0.3
    assert rt.bus.latest_payload("brain/drives")["dominant"] in ("energy", "curiosity", "social")
    assert rt.ctx.extras["memory"].dreams >= 1 and dreams and dreams[0]["facts"]
    assert any(f.predicate == "often_visits" for f in rt.ctx.extras["memory"].semantic.query("self"))
    assert rt.report()["world"]["collisions"] == 0


def test_attention_focus_triggers_investigation(config):
    config.brain.patrol = []
    config.perception.fuse_pose = False

    async def go():
        rt = await booted(config)
        rt.brain.ctx.config.brain.explore_above = 2.0          # no exploring; we want a clean idle
        done = []
        rt.bus.subscribe("brain/investigated", lambda m: done.append(m.payload))
        await rt.run(2)
        await rt.bus.publish("security/alert", {"track_id": 9, "x": 6.0, "y": 6.0, "reason": "test"})
        await rt.run(1)
        att = rt.bus.latest_payload("brain/attention")
        assert att["focus"] and att["focus"]["topic"] == "security/alert"
        assert rt.brain.mode == "investigate"
        await rt.run(40)
        assert done and rt.brain.investigations == 1
        assert rt.bus.latest_payload("brain/attention")["focus"] is None      # handled
        await rt.shutdown()
        return rt
    rt = run(go())
    assert any(e.action == "investigate" for e in rt.safety.audit.entries)


def test_goal_decomposition_and_library_tasks():
    ctx = {"charger": {"x": 0.5, "y": 0.5}, "patrol": [{"x": 1, "y": 1}], "tasks": ["rounds"],
           "places": {"kitchen": (7.0, 1.5)}}
    plan = run(RulePlanner().plan("goto kitchen, then rounds and then charge", ctx))
    assert [s.skill for s in plan.steps] == ["goto", "task", "goto"]
    assert plan.steps[1].args == {"name": "rounds"} and plan.steps[2].args["name"] == "charger"
    assert "then" in plan.rationale
    bad = run(RulePlanner().plan("goto kitchen then juggle", ctx))
    assert not bad.steps and "juggle" in bad.rationale


def test_whatif_is_side_effect_free_and_explains(config):
    config.safety.keep_out = [{"x": 0, "y": 8.5, "w": 1.5, "h": 1.5, "name": "stairs"}]
    config.brain.patrol = []

    async def go():
        rt = await booted(config)
        results = []
        rt.bus.subscribe("skill/result", lambda m: results.append(m.payload))
        await rt.bus.publish("skill/invoke", {"skill": "whatif", "args": {"goal": "goto 0.5 9.5"}})
        await rt.bus.publish("skill/invoke", {"skill": "whatif", "args": {"goal": "goto 5 5"}})
        await rt.run(1)
        await rt.shutdown()
        return rt, results
    rt, results = run(go())
    a, b = results[0]["output"], results[1]["output"]
    assert not a["would_approve"] and a["violations"][0]["rule"] == "keep_out" and "would not" in a["speech"]
    assert b["would_approve"] and b["prediction"]["feasible"]
    assert rt.brain.council.history == [] and rt.brain.plan is None and rt.tasks.task is None


def test_personality_wrapping():
    formal = Personality(PersonalityConfig(formality=0.9, language="bn"))
    assert formal.wrap("done.", "Ijtihad") == "Ijtihad sahib, done."
    warm = Personality(PersonalityConfig(warmth=0.9, verbosity=0.8))
    assert warm.wrap("hello.", "Rafi", "friendly") == "Hey Rafi, Sure. hello!"
    terse = Personality(PersonalityConfig(verbosity=0.1))
    assert terse.wrap("First sentence is long enough to be cut somewhere sensible ok. Second sentence. Third.") == \
        "First sentence is long enough to be cut somewhere sensible ok."
    assert formal.wrap("Intruder!", "Rafi", "alert") == "Intruder!"


def test_memory_valence_and_consolidation():
    em = EpisodicMemory()
    for i in range(4):
        em.remember(float(i), "language/utterance", "user said: light on", 0.6)
    em.remember(4.0, "nav/arrived", "arrived at kitchen", 0.5, valence=0.9)
    em.remember(5.0, "nav/arrived", "arrived at hall", 0.5)
    top = em.query(now=6.0, kind="nav/arrived", limit=1)[0]
    assert top.summary == "arrived at kitchen"                 # emotional weight beats recency here
    facts = em.consolidate_into_facts(now=200.0, min_count=3, older_than=60.0)
    assert ("user", "often_says", "light on", 4) in facts and len(em) == 2


def test_bangla_script_and_conversation_context():
    assert transliterate("রান্নাঘরে যাও") == "kitchen e jao"
    it = RuleInterpreter()
    assert it.parse("রান্নাঘরে যাও").args == {"goal": "goto kitchen"}
    assert it.parse("থামো").skill == "estop" and it.parse("আমার চাবি কোথায়").args == {"name": "keys"}
    backend = ScriptedBackend([json.dumps({"skill": "device", "args": {"kind": "fan", "on": True}, "confidence": 0.9})])
    llm = LLMInterpreter(backend, [{"name": "device", "description": "", "args": {}}])
    run(llm.parse_async("turn on the kitchen light"))          # rules handle it, but it enters the history
    intent = run(llm.parse_async("and the fan too"))
    assert intent.skill == "device"
    assert "turn on the kitchen light" in backend.calls[0][1] and "PREVIOUS UTTERANCES" in backend.calls[0][1]


def test_llm_critic_and_drafted_macro():
    cfg = load_scenario("chores")
    cfg.events = []
    cfg.brain.llm = "scripted"
    replies = [
        json.dumps({"rationale": "kitchen", "steps": [{"skill": "goto", "args": {"x": 7, "y": 1.5}}]}),   # planner
        json.dumps({"risk": 0.4, "notes": ["someone may be in the kitchen"]}),                             # critic
        json.dumps({"name": "night_mode", "description": "all off", "params": {},                          # draft
                    "steps": [{"kind": "skill", "args": {"skill": "device", "args": {"kind": "light", "on": False}}}]}),
        json.dumps({"name": "bad_one", "description": "", "params": {},
                    "steps": [{"kind": "goto", "args": {"x": 50, "y": 50}}]}),
    ]

    async def go():
        rt = Runtime(cfg, llm=ScriptedBackend(replies))
        await rt.boot()
        await rt.bus.publish("brain/goal", {"goal": "go to the kitchen please"})
        await rt.run(1)
        d = rt.brain.council.history[-1]
        assert d.plan.rationale == "kitchen" and any("critic:" in c for c in d.critique)
        assert 0.6 <= d.confidence < 1.0
        results = []
        rt.bus.subscribe("skill/result", lambda m: results.append(m.payload))
        await rt.bus.publish("skill/invoke", {"skill": "draft_skill", "confirmed": True,
                                              "args": {"description": "turn everything off"}})
        await rt.run(1)
        assert results[-1]["ok"] and "night_mode" in rt.ctx.extras["skills"].names()
        await rt.bus.publish("skill/invoke", {"skill": "draft_skill", "confirmed": True,
                                              "args": {"description": "go far away"}})
        await rt.run(1)
        assert not results[-1]["ok"] and "out_of_bounds" in results[-1]["error"]
        await rt.shutdown()
        return rt
    rt = run(go())
    assert [e.action for e in rt.safety.audit.entries if e.actor == "skills" and e.action.startswith("d")] == \
        ["draft", "define", "draft", "define.rejected"]
