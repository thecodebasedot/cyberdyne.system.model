"""Cyberdyne System Model -- a layered cognitive robotics platform.

The package is organised as concentric layers (see docs/ARCHITECTURE.md):

    kernel        -> scheduler, message bus, clock, state machine, watchdog
    hal           -> hardware abstraction (real / virtual backends)
    sim           -> 2D physics world used by the virtual backend
    safety        -> envelope, permissions, e-stop, audit log (immutable core)
    perception    -> sensor -> structured observations
    world_model   -> occupancy grid, entity store
    memory        -> working / episodic memory
    cognition     -> behaviour trees, planner, brain
    motion        -> navigation and velocity control
    skills        -> plugin task system
    language      -> command interpreter (rule-based now, LLM later)
    observability -> telemetry + web dashboard
    learning/fleet-> reserved for later phases
"""

__version__ = "0.1.0"
