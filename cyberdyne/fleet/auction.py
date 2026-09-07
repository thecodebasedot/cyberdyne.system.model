"""AuctionModule: sealed-bid, first-price task allocation.

    fleet/offer  {offer_id, name, steps, deadline}   -> everyone (incl. self) bids
    fleet/bid    {offer_id, robot, cost}             -> the auctioneer collects
    fleet/award  {offer_id, robot}                   -> winner starts the task

Cost = predicted seconds to reach the first goto (mental simulation on the
believed map; straight-line fallback) + battery penalty + busy penalty.
A robot in ESTOP, with a critical battery, or without a peer link does not
bid. Ties break on robot name, so the outcome is deterministic.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..cognition.planner import Plan, PlanStep
from ..cognition.simulate import MentalSimulator
from ..kernel.context import Context
from ..kernel.module import Module


@dataclass
class Offer:
    offer_id: str
    name: str
    steps: list[dict]
    deadline: float
    bids: dict[str, float] = field(default_factory=dict)
    awarded: str | None = None
    origin: str = ""

    def to_dict(self) -> dict:
        return {"offer_id": self.offer_id, "name": self.name, "steps": len(self.steps), "bids": self.bids,
                "awarded": self.awarded, "origin": self.origin}


class AuctionModule(Module):
    name = "auction"
    rate_hz = 5.0
    priority = 82

    def __init__(self, robot: str, bid_window: float = 1.5) -> None:
        super().__init__()
        self.robot = robot
        self.bid_window = bid_window
        self.open: dict[str, Offer] = {}          # offers I am running
        self.seen: dict[str, Offer] = {}          # offers I have bid on
        self.won = self.lost = 0
        self._next_offer = 1

    async def setup(self, ctx: Context) -> None:
        self.ctx = ctx
        self.sim = MentalSimulator(ctx.config)
        self._subs = [ctx.bus.subscribe("fleet/auction", self._on_request, name="auction.request"),
                      ctx.bus.subscribe("fleet/*/fleet/offer", self._on_offer, name="auction.offer"),
                      ctx.bus.subscribe("fleet/*/fleet/bid", self._on_bid, name="auction.bid"),
                      ctx.bus.subscribe("fleet/*/fleet/award", self._on_award, name="auction.award")]
        ctx.extras["auction"] = self

    async def teardown(self) -> None:
        for s in self._subs:
            self.ctx.bus.unsubscribe(s)

    # -- auctioneer ------------------------------------------------------------
    async def _on_request(self, msg) -> None:
        p = msg.payload or {}
        offer = Offer(f"{self.robot}-{self._next_offer}", str(p.get("name", "task")), list(p.get("steps") or []),
                      self.ctx.now + self.bid_window, origin=msg.source)
        self._next_offer += 1
        self.open[offer.offer_id] = offer
        payload = {"offer_id": offer.offer_id, "name": offer.name, "steps": offer.steps, "deadline": offer.deadline}
        await self.ctx.bus.publish("fleet/send", {"topic": "fleet/offer", "payload": payload}, source=self.name)
        await self._bid(offer.offer_id, offer.name, offer.steps, local=True)

    async def _on_bid(self, msg) -> None:
        p = msg.payload
        offer = self.open.get(p["offer_id"])
        if offer and offer.awarded is None:
            offer.bids[p["robot"]] = float(p["cost"])

    async def _close(self, offer: Offer) -> None:
        if not offer.bids:
            offer.awarded = "nobody"
            await self.ctx.bus.publish("fleet/auction_failed", offer.to_dict(), source=self.name)
            return
        winner = min(offer.bids.items(), key=lambda kv: (kv[1], kv[0]))[0]
        offer.awarded = winner
        self.ctx.safety.audit.record(self.ctx.now, "auction", "award", offer=offer.offer_id, winner=winner,
                                     bids=offer.bids)
        award = {"offer_id": offer.offer_id, "robot": winner, "name": offer.name, "steps": offer.steps}
        await self.ctx.bus.publish("fleet/send", {"topic": "fleet/award", "payload": award}, source=self.name)
        await self.ctx.bus.publish("fleet/awarded", offer.to_dict(), source=self.name)
        if winner == self.robot:
            self.won += 1
            await self._start(offer.name, offer.steps, offer.offer_id)
        elif self.robot in offer.bids:
            self.lost += 1

    # -- bidder --------------------------------------------------------------------
    def cost(self, steps: list[dict]) -> float | None:
        if self.ctx.safety.estop.engaged:
            return None
        bus = self.ctx.bus
        batt = (bus.latest_payload("sensor/battery") or {}).get("level", 1.0)
        if batt <= self.ctx.config.brain.battery_low:
            return None
        pose = bus.latest_payload("sensor/odometry")
        first = next((s for s in steps if s.get("kind", "goto") == "goto" or s.get("skill") == "goto"), None)
        seconds = 0.0
        if first and pose:
            gx, gy = float(first["args"]["x"]), float(first["args"]["y"])
            wm = self.ctx.extras.get("world_model")
            if wm:
                r = self.sim.rollout(Plan("bid", [PlanStep("goto", {"x": gx, "y": gy})]), wm.grid, pose, batt)
                seconds = r.total_time if r.feasible else 1e6
            else:
                seconds = math.hypot(gx - pose["x"], gy - pose["y"]) / max(self.ctx.config.safety.max_linear, 0.1)
        busy = 30.0 if (bus.latest_payload("task/status") or {}).get("active") else 0.0
        return round(seconds + busy + 40.0 * (1.0 - batt), 2)

    async def _bid(self, offer_id: str, name: str, steps: list[dict], local: bool = False) -> None:
        cost = self.cost(steps)
        if cost is None:
            return
        self.seen[offer_id] = Offer(offer_id, name, steps, 0.0)
        bid = {"offer_id": offer_id, "robot": self.robot, "cost": cost}
        if local:
            self.open[offer_id].bids[self.robot] = cost
        else:
            await self.ctx.bus.publish("fleet/send", {"topic": "fleet/bid", "payload": bid}, source=self.name)
        await self.ctx.bus.publish("fleet/bid_placed", bid, source=self.name)

    async def _on_offer(self, msg) -> None:
        p = msg.payload
        await self._bid(p["offer_id"], p["name"], p["steps"])

    async def _on_award(self, msg) -> None:
        p = msg.payload
        if p["offer_id"] not in self.seen:
            return
        if p["robot"] == self.robot:
            self.won += 1
            await self._start(p["name"], p["steps"], p["offer_id"])
        else:
            self.lost += 1

    async def _start(self, name: str, steps: list[dict], offer_id: str) -> None:
        await self.ctx.bus.publish("task/start", {"name": name, "steps": steps, "origin": f"auction:{offer_id}",
                                                  "queue": True}, source=self.name)

    async def tick(self, dt: float) -> None:
        for offer in list(self.open.values()):
            if offer.awarded is None and self.ctx.now >= offer.deadline:
                await self._close(offer)

    def describe(self) -> dict:
        return {"robot": self.robot, "won": self.won, "lost": self.lost,
                "open": [o.to_dict() for o in self.open.values() if o.awarded is None]}
