"""``cyberdyne skill new <name>``: scaffold a skill package with a test."""
from __future__ import annotations

from pathlib import Path

SKILL_TEMPLATE = '''"""{name}: {description}"""
from __future__ import annotations

from typing import Any

from cyberdyne.kernel.context import Context
from cyberdyne.skills import Skill, SkillManifest, SkillResult


class {cls}(Skill):
    manifest = SkillManifest("{name}", description="{description}",
                             args={{"who": "who to address"}}, tags=("custom",))

    async def run(self, ctx: Context, args: dict[str, Any]) -> SkillResult:
        who = args.get("who", "everyone")
        await ctx.bus.publish("speech/say", {{"text": f"Hello {{who}}, this is {name}."}}, source="skill.{name}")
        return SkillResult(True, {{"who": who, "speech": f"Done: {name} for {{who}}."}})
'''

TEST_TEMPLATE = '''from cyberdyne.testing import robot, run


def test_{name}_runs():
    async def go():
        async with robot(skills=["{module}"]) as r:
            res = await r.invoke("{name}", {{"who": "Rafi"}})
            assert res.ok and res.output["who"] == "Rafi"
            await r.run(0.5)
            assert any(m["text"].startswith("Hello Rafi") for m in r.messages("speech/said"))
    run(go())
'''


def scaffold(name: str, root: Path, description: str = "a custom skill") -> list[Path]:
    if not name.isidentifier() or not name.islower():
        raise ValueError("skill name must be a lowercase identifier")
    pkg = root / "skills_ext"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").touch()
    cls = "".join(p.capitalize() for p in name.split("_")) + "Skill"
    skill_file = pkg / f"{name}.py"
    skill_file.write_text(SKILL_TEMPLATE.format(name=name, cls=cls, description=description), encoding="utf-8")
    tests = root / "tests"
    tests.mkdir(exist_ok=True)
    test_file = tests / f"test_skill_{name}.py"
    test_file.write_text(TEST_TEMPLATE.format(name=name, module=f"skills_ext.{name}"), encoding="utf-8")
    return [skill_file, test_file]
