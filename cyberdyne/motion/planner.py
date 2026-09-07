"""Global path planner: A* over the world-model occupancy grid.

Unknown cells are treated as free (optimistic exploration); occupied cells
are inflated by ``inflate`` cells so the path keeps the robot's body clear.
The controller replans periodically, so the path improves as the map fills.
"""
from __future__ import annotations

import heapq
import math

from ..world_model.grid import OccupancyGrid

_NEIGHBOURS = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
               (1, 1, math.sqrt(2)), (1, -1, math.sqrt(2)), (-1, 1, math.sqrt(2)), (-1, -1, math.sqrt(2))]


class GridPlanner:
    def __init__(self, inflate: int = 1, threshold: float = 0.6) -> None:
        self.inflate = inflate
        self.threshold = threshold
        self.plans = 0
        self.failures = 0

    def _blocked_mask(self, g: OccupancyGrid) -> list[bool]:
        occ = [False] * (g.cols * g.rows)
        for r in range(g.rows):
            for c in range(g.cols):
                lo = g.cells[r * g.cols + c]
                if 1.0 - 1.0 / (1.0 + math.exp(lo)) >= self.threshold:
                    for dr in range(-self.inflate, self.inflate + 1):
                        for dc in range(-self.inflate, self.inflate + 1):
                            rr, cc = r + dr, c + dc
                            if 0 <= rr < g.rows and 0 <= cc < g.cols:
                                occ[rr * g.cols + cc] = True
        # walls: the outermost ring is never traversable
        for c in range(g.cols):
            occ[c] = occ[(g.rows - 1) * g.cols + c] = True
        for r in range(g.rows):
            occ[r * g.cols] = occ[r * g.cols + g.cols - 1] = True
        return occ

    @staticmethod
    def _soft_cost(g: OccupancyGrid, soft: list[tuple[float, float, float, float]]) -> list[float]:
        """Per-cell extra cost from (x, y, radius, weight) sources, e.g. people's personal space."""
        cost = [0.0] * (g.cols * g.rows)
        for x, y, radius, weight in soft:
            c0, r0 = int(x / g.res), int(y / g.res)
            span = int(radius / g.res) + 1
            for r in range(max(0, r0 - span), min(g.rows, r0 + span + 1)):
                for c in range(max(0, c0 - span), min(g.cols, c0 + span + 1)):
                    d = math.hypot((c + 0.5) * g.res - x, (r + 0.5) * g.res - y)
                    if d < radius:
                        cost[r * g.cols + c] += weight * (1.0 - d / radius)
        return cost

    def plan(self, g: OccupancyGrid, start: tuple[float, float], goal: tuple[float, float],
             soft: list[tuple[float, float, float, float]] | None = None) -> list[tuple[float, float]]:
        self.plans += 1
        occ = self._blocked_mask(g)
        extra = self._soft_cost(g, soft) if soft else None
        sc, sr = int(start[0] / g.res), int(start[1] / g.res)
        gc, gr = int(goal[0] / g.res), int(goal[1] / g.res)
        if not (0 <= gc < g.cols and 0 <= gr < g.rows):
            self.failures += 1
            return []
        occ[sr * g.cols + sc] = False       # we are standing here, so it is free
        occ[gr * g.cols + gc] = False       # let the controller decide about the last cell

        def h(c: int, r: int) -> float:
            return math.hypot(c - gc, r - gr)

        start_k, goal_k = (sc, sr), (gc, gr)
        best = {start_k: 0.0}
        came: dict[tuple[int, int], tuple[int, int]] = {}
        heap = [(h(sc, sr), 0.0, start_k)]
        while heap:
            _, cost, cur = heapq.heappop(heap)
            if cur == goal_k:
                break
            if cost > best.get(cur, math.inf):
                continue
            for dc, dr, w in _NEIGHBOURS:
                nc, nr = cur[0] + dc, cur[1] + dr
                if not (0 <= nc < g.cols and 0 <= nr < g.rows) or occ[nr * g.cols + nc]:
                    continue
                if dc and dr and (occ[cur[1] * g.cols + nc] or occ[nr * g.cols + cur[0]]):
                    continue                # no corner cutting
                nk, ncost = (nc, nr), cost + w + (extra[nr * g.cols + nc] if extra else 0.0)
                if ncost < best.get(nk, math.inf):
                    best[nk] = ncost
                    came[nk] = cur
                    heapq.heappush(heap, (ncost + h(nc, nr), ncost, nk))
        if goal_k not in came and goal_k != start_k:
            self.failures += 1
            return []
        cells = [goal_k]
        while cells[-1] != start_k:
            cells.append(came[cells[-1]])
        cells.reverse()
        pts = [((c + 0.5) * g.res, (r + 0.5) * g.res) for c, r in cells]
        pts[-1] = goal
        return self._simplify(pts)

    @staticmethod
    def _simplify(pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
        if len(pts) < 3:
            return pts
        out = [pts[0]]
        for i in range(1, len(pts) - 1):
            (x0, y0), (x1, y1), (x2, y2) = out[-1], pts[i], pts[i + 1]
            if abs((x1 - x0) * (y2 - y1) - (y1 - y0) * (x2 - x1)) > 1e-9:
                out.append(pts[i])
        out.append(pts[-1])
        return out
