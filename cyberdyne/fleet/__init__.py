"""Fleet layer (Phase 5): multi-robot coordination.

Reserved. The wire format is the bus ``Message`` serialised to JSON; a
``FleetBridge`` module will mirror selected topics between robots.
"""
from .interfaces import FleetMessage, Transport

__all__ = ["FleetMessage", "Transport"]
