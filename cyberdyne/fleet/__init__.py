"""Fleet layer (Phase 5): multi-robot coordination.

    transport   FleetMessage + Transport ABC, InMemoryTransport, UDPTransport
    bridge      FleetBridge module: presence, topic mirroring, LWW entity sync
    auction     AuctionModule: sealed-bid task allocation
    sim         FleetSim: lock-step multi-robot simulation on one clock
"""
from .auction import AuctionModule
from .bridge import FleetBridge
from .interfaces import FleetMessage, Transport
from .sim import FleetSim
from .transport import InMemoryTransport, UDPTransport

__all__ = ["AuctionModule", "FleetBridge", "FleetMessage", "Transport", "FleetSim", "InMemoryTransport",
           "UDPTransport"]
