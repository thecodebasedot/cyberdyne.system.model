"""Configuration model.

Everything the runtime needs is a plain dataclass so a config can come from
TOML, JSON, a scenario file or a test fixture without any framework.
"""
from __future__ import annotations

import json
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class KernelConfig:
    mode: str = "sim"               # "sim" | "real"
    realtime_factor: float | None = None   # None = as fast as possible (sim only)
    log_level: str = "INFO"
    history: int = 2000
    strict_bus: bool = False        # validate every payload against kernel.schema
    record: str = ""                # path: write every bus message as JSON lines (replay / time-travel)


@dataclass
class SafetyConfig:
    max_linear: float = 0.8          # m/s
    max_angular: float = 1.5         # rad/s
    obstacle_stop_distance: float = 0.25
    perception_stale: float = 0.5    # seconds without a scan before forward motion is refused
    keep_out: list[dict[str, float]] = field(default_factory=list)
    battery_critical: float = 0.05
    permissions: dict[str, str] = field(default_factory=dict)   # action glob -> tier
    guest_max_tier: str = "log"          # highest tier a recognised non-owner may invoke
    unknown_max_tier: str = "free"       # highest tier an unrecognised speaker may invoke


@dataclass
class WorldConfig:
    width: float = 10.0
    height: float = 10.0
    obstacles: list[dict[str, float]] = field(default_factory=list)
    robot_start: dict[str, float] = field(default_factory=lambda: {"x": 1.0, "y": 1.0, "theta": 0.0})
    charger: dict[str, float] = field(default_factory=lambda: {"x": 0.5, "y": 0.5})
    battery_start: float = 1.0
    battery_drain_idle: float = 0.0005     # per second
    battery_drain_moving: float = 0.004    # per second at full speed
    battery_charge_rate: float = 0.05      # per second
    rooms: list[dict[str, Any]] = field(default_factory=list)      # {name,x,y,w,h}
    actors: list[dict[str, Any]] = field(default_factory=list)     # {id,kind,x,y,signature,route,speed,active_from}


@dataclass
class SocialConfig:
    known: list[dict[str, str]] = field(default_factory=list)      # {name, signature, trust}
    wake_words: list[str] = field(default_factory=lambda: ["hey cyberdyne", "cyberdyne", "oi robot", "robot"])
    require_wake_word: bool = False     # True: ignore speech that is not addressed to the robot
    greet_cooldown: float = 60.0
    armed: bool = False                 # security mode: unknown people raise alerts
    speak_results: bool = True
    personal_space: float = 1.0         # metres; social navigation cost radius


@dataclass
class HardwareConfig:
    port: str = "loopback"              # serial device path, or "loopback" for the built-in fake firmware
    baud: int = 115200
    linear_scale: float = 1.0           # odometry calibration
    angular_scale: float = 1.0


@dataclass
class HomeConfig:
    hub: str = "virtual"                # "virtual" | "mqtt" | "hass"
    url: str = ""                       # mqtt://host:port or http://hass:8123
    token: str = ""                     # Home Assistant long-lived token (or env HASS_TOKEN)
    devices: list[dict[str, Any]] = field(default_factory=list)   # {id, kind, room, name?}


@dataclass
class LearningConfig:
    data_dir: str = ""                  # "" = no persistence
    user_model: bool = True
    suggest_after: int = 3              # observations before the robot suggests a habit
    suggest_cooldown: float = 300.0


@dataclass
class BrainConfig:
    patrol: list[dict[str, float]] = field(default_factory=list)
    battery_low: float = 0.25
    battery_full: float = 0.95
    goal_tolerance: float = 0.2
    llm: str = "none"                 # "none" | "anthropic" | "scripted"
    model: str = "claude-opus-5"
    effort: str = "high"              # planner effort; the interpreter always runs at "low"
    confidence_threshold: float = 0.6 # below this the brain asks the human before acting
    question_timeout: float = 120.0   # seconds an unanswered question stays open


@dataclass
class DashboardConfig:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8080


@dataclass
class RobotConfig:
    name: str = "cyberdyne-01"
    kernel: KernelConfig = field(default_factory=KernelConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    world: WorldConfig = field(default_factory=WorldConfig)
    brain: BrainConfig = field(default_factory=BrainConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    social: SocialConfig = field(default_factory=SocialConfig)
    hardware: HardwareConfig = field(default_factory=HardwareConfig)
    home: HomeConfig = field(default_factory=HomeConfig)
    learning: LearningConfig = field(default_factory=LearningConfig)
    tasks: list[dict[str, Any]] = field(default_factory=list)      # {name, steps:[...]}
    routines: list[dict[str, Any]] = field(default_factory=list)   # {name, every|at, goal|task, speak?}
    events: list[dict[str, Any]] = field(default_factory=list)   # scripted scenario events
    skills: list[str] = field(default_factory=list)              # extra skill module paths

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RobotConfig:
        d = dict(d)
        return cls(
            name=d.get("name", "cyberdyne-01"),
            kernel=KernelConfig(**d.get("kernel", {})),
            safety=SafetyConfig(**d.get("safety", {})),
            world=WorldConfig(**d.get("world", {})),
            brain=BrainConfig(**d.get("brain", {})),
            dashboard=DashboardConfig(**d.get("dashboard", {})),
            social=SocialConfig(**d.get("social", {})),
            hardware=HardwareConfig(**d.get("hardware", {})),
            home=HomeConfig(**d.get("home", {})),
            learning=LearningConfig(**d.get("learning", {})),
            tasks=list(d.get("tasks", [])),
            routines=list(d.get("routines", [])),
            events=list(d.get("events", [])),
            skills=list(d.get("skills", [])),
        )


def load_config(path: str | Path) -> RobotConfig:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix == ".toml":
        data = tomllib.loads(text)
    elif p.suffix == ".json":
        data = json.loads(text)
    else:
        raise ValueError(f"unsupported config format: {p.suffix}")
    return RobotConfig.from_dict(data)
