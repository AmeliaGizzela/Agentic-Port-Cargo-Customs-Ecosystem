"""Routing & penjadwalan truk GlobalCargo.

1) Graf jalan (networkx) + Dijkstra -> waktu tempuh Port->Gudang (berubah saat jalan ditutup/kongesti).
2) Multi-trip Vehicle Routing Problem with Time Windows (full-truckload):
   - setiap job = 1 kontainer dengan ready time (bongkar + release Bea Cukai) dan deadline gudang
   - setiap truk menjalankan urutan trip Port -> Gudang -> Port
   Algoritma: konstruksi greedy (EDD / FCFS) + local search (relocate & swap) sesuai fungsi tujuan strategi.
"""
from __future__ import annotations

import math
import time
from collections import defaultdict

import networkx as nx

from data.master_data import COSTS

LOADED_FACTOR = 1.15   # truk bermuatan lebih lambat
LOAD_H = 0.25          # antre gate + loading di terminal
UNLOAD_H = 0.5         # bongkar di gudang
TRUCK_FIXED_JT = 0.8   # biaya shift sopir per truk yang dipakai
DWELL_JT_PER_CONTAINER_H = 0.1  # biaya kongesti/penumpukan yard per kontainer-jam (biaya ekosistem)


def build_graph(nodes: list[dict], edges: list[dict]) -> nx.Graph:
    G = nx.Graph()
    for n in nodes:
        G.add_node(n["NodeId"], **n)
    for e in edges:
        if e["Status"] == "OPEN":
            G.add_edge(e["FromNode"], e["ToNode"], minutes=float(e["Minutes"]) * float(e["CongestionFactor"]),
                       edge_id=e["EdgeId"])
    return G


def shortest(G: nx.Graph, a: str, b: str) -> tuple[float, list[str]]:
    try:
        path = nx.shortest_path(G, a, b, weight="minutes")
        return float(nx.path_weight(G, path, "minutes")), path
    except nx.NetworkXNoPath:
        return math.inf, []


def travel_table(G: nx.Graph, dests: set[str]) -> dict:
    tt = {}
    for d in dests:
        m, path = shortest(G, "PORT", d)
        tt[d] = {"to_h": m * LOADED_FACTOR / 60.0, "back_h": m / 60.0, "path": path, "minutes": m}
    m, path = shortest(G, "DEPOT", "PORT")
    tt["DEPOT"] = {"to_port_h": m / 60.0, "path": path}
    return tt


def _night_h(start: float, end: float) -> float:
    if end <= start:
        return 0.0
    n, steps = 0.0, max(1, int((end - start) / 0.25))
    for i in range(steps):
        h = (start + (i + 0.5) * (end - start) / steps) % 24
        if h >= 22 or h < 6:
            n += (end - start) / steps
    return n


class Planner:
    def __init__(self, jobs: list[dict], trucks: list[dict], tt: dict, mode: str, now_h: float,
                 fixed_trips: list[dict] | None = None):
        self.jobs = {j["job_id"]: j for j in jobs}
        self.trucks = {t["truck_id"]: t for t in trucks}
        self.tt, self.mode, self.now = tt, mode, now_h
        self.ship_info = {}
        for j in jobs:
            self.ship_info.setdefault(j["shipment"], (j["deadline_h"], j.get("priority_weight", 0.0)))
        self.fixed = defaultdict(list)
        for tr in fixed_trips or []:
            self.fixed[tr["truck_id"]].append(tr)
        self.evals = 0

    # ------------------------------------------------------------ simulation
    def truck_start(self, tid: str) -> tuple[float, str]:
        t = self.trucks[tid]
        if self.fixed[tid]:
            last = max(self.fixed[tid], key=lambda x: x["done_h"])
            return max(last["done_h"], t["available_from_h"]), last["dest"]
        return max(t["available_from_h"], self.now), t.get("start_node", "DEPOT")

    def simulate(self, tid: str, seq: list[str]) -> list[dict]:
        t, at = self.truck_start(tid)
        out = []
        for jid in seq:
            j = self.jobs[jid]
            if at == "DEPOT":
                t += self.tt["DEPOT"]["to_port_h"]
            elif at != "PORT":
                t += self.tt[at]["back_h"]
            pickup = max(t, j["ready_h"])
            deliver = pickup + LOAD_H + self.tt[j["dest"]]["to_h"]
            done = deliver + UNLOAD_H
            out.append({"job_id": jid, "truck_id": tid, "pickup_h": round(pickup, 3), "deliver_h": round(deliver, 3),
                        "done_h": round(done, 3), "dest": j["dest"]})
            t, at = done, j["dest"]
        return out

    def objective(self, routes: dict[str, list[str]], cache: dict | None = None) -> tuple[float, dict]:
        self.evals += 1
        trips = {}
        for tid, seq in routes.items():
            if cache is not None and tid in cache and cache[tid][0] == tuple(seq):
                tr = cache[tid][1]
            else:
                tr = self.simulate(tid, seq)
                if cache is not None:
                    cache[tid] = (tuple(seq), tr)
            for x in tr:
                trips[x["job_id"]] = x
        cost = 0.0
        last_by_ship: dict[str, float] = {}
        for jid, x in trips.items():
            j = self.jobs[jid]
            late = max(0.0, x["deliver_h"] - j["deadline_h"])
            cost += COSTS["gc_sla_penalty_jt_per_container_h"] * late
            cost += COSTS["truck_overtime_jt_per_h"] * _night_h(x["pickup_h"], x["done_h"])
            if self.trucks[x["truck_id"]]["type"] == "SUBCON":
                cost += COSTS["subcon_trip_cost_jt"] - COSTS["truck_trip_cost_jt"]
            if self.mode == "collaborative":
                cost += DWELL_JT_PER_CONTAINER_H * (x["pickup_h"] - j["ready_h"])  # biaya kongesti yard
                last_by_ship[j["shipment"]] = max(last_by_ship.get(j["shipment"], -1e9), x["deliver_h"])
        if self.mode == "collaborative":
            for sid, last in last_by_ship.items():
                deadline, weight = self.ship_info[sid]
                cost += weight * max(0.0, last - deadline)
        cost += TRUCK_FIXED_JT * sum(1 for tid, s in routes.items() if s and self.trucks[tid]["type"] == "OWN")
        return cost, trips

    # ---------------------------------------------------------- construction
    def allowed(self, tid: str, jid: str) -> bool:
        t = self.trucks[tid]
        return t.get("dedicated_shipment") in (None, self.jobs[jid]["shipment"])

    def construct(self) -> dict[str, list[str]]:
        routes = {tid: [] for tid in self.trucks}
        if self.mode == "local":
            order = sorted(self.jobs, key=lambda j: (self.jobs[j]["ready_h"], j))
        else:  # EDD dengan bobot prioritas
            order = sorted(self.jobs, key=lambda j: (self.jobs[j]["deadline_h"] - 2.0 * min(self.jobs[j].get(
                "priority_weight", 0), 10) / 10, self.jobs[j]["ready_h"], j))
        cache: dict = {}
        for jid in order:
            best, best_c = None, math.inf
            for tid in routes:
                if not self.allowed(tid, jid):
                    continue
                routes[tid].append(jid)
                c, _ = self.objective(routes, cache)
                routes[tid].pop()
                if c < best_c - 1e-9:
                    best, best_c = tid, c
            routes[best].append(jid)
        return routes

    def improve(self, routes: dict[str, list[str]], max_evals: int = 2500, time_budget_s: float = 30.0) -> dict:
        t_end = time.perf_counter() + time_budget_s
        cache: dict = {}
        best_c, _ = self.objective(routes, cache)
        improved = True
        while improved and self.evals < max_evals and time.perf_counter() < t_end:
            improved = False
            tids = list(routes)
            # relocate
            for a in tids:
                for i in range(len(routes[a])):
                    jid = routes[a][i]
                    for b in tids:
                        if not self.allowed(b, jid):
                            continue
                        for pos in range(len(routes[b]) + (0 if a == b else 1)):
                            if a == b and pos == i:
                                continue
                            ra, rb = list(routes[a]), list(routes[b]) if a != b else None
                            new = dict(routes)
                            if a == b:
                                seq = list(routes[a])
                                seq.pop(i)
                                seq.insert(pos, jid)
                                new[a] = seq
                            else:
                                ra.pop(i)
                                rb.insert(pos, jid)
                                new[a], new[b] = ra, rb
                            c, _ = self.objective(new, cache)
                            if c < best_c - 1e-6:
                                routes, best_c, improved = new, c, True
                                break
                        if improved or (time.perf_counter() > t_end or self.evals >= max_evals):
                            break
                    if improved or (time.perf_counter() > t_end or self.evals >= max_evals):
                        break
                if improved or (time.perf_counter() > t_end or self.evals >= max_evals):
                    break
        return routes

    def solve(self) -> dict:
        t0 = time.perf_counter()
        routes = self.construct()
        routes = self.improve(routes)
        cost, trips = self.objective(routes)
        return {"routes": routes, "trips": trips, "objective": round(cost, 3),
                "solve_ms": round((time.perf_counter() - t0) * 1000, 1), "evaluations": self.evals}

    def keep_sequence(self, prev_routes: dict[str, list[str]]) -> dict:
        """Strategi statis: pertahankan penugasan & urutan truk; hanya waktu yang bergeser."""
        routes = {tid: [j for j in prev_routes.get(tid, []) if j in self.jobs] for tid in self.trucks}
        assigned = {j for s in routes.values() for j in s}
        for jid in sorted(set(self.jobs) - assigned, key=lambda j: self.jobs[j]["ready_h"]):
            tid = min((t for t in routes if self.allowed(t, jid)),
                      key=lambda t: (self.simulate(t, routes[t])[-1]["done_h"] if routes[t] else 0))
            routes[tid].append(jid)
        cost, trips = self.objective(routes)
        return {"routes": routes, "trips": trips, "objective": round(cost, 3), "solve_ms": 0.0, "evaluations": 1}
