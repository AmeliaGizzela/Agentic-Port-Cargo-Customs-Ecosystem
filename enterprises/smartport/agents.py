"""AI Agents SmartPort Terminal (A_2).

- berth_planning_agent     : MIP berth allocation, adaptive replanning, negosiasi dengan OceanLine
- yard_agent               : status kontainer & customs release -> CONTAINER_AVAILABILITY ke GlobalCargo/ImportExport
- operations_monitor_agent : memantau kondisi crane/dermaga (perubahan lingkungan) dan memicu replanning
"""
from __future__ import annotations

import hashlib
import json
import uuid

from common import timeutil as tu
from common.a2a import new_conversation_id
from common.agent_base import Agent
from enterprises.smartport import berth_mip as bm
from enterprises.smartport.repo import SmartPortRepo


def _sig(obj) -> str:
    return hashlib.md5(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _fmt_plan(plan: list[dict]) -> str:
    return "; ".join(f"{p['vessel_id']}->{p['berth_id']} {tu.clock(p['start_h'])}-{tu.clock(p['end_h'])}" for p in plan)


class BerthPlanningAgent(Agent):
    name = "berth_planning_agent"
    role = "Planning, Optimization & Negotiation Agent"
    description = "Menyusun jadwal sandar optimal (MIP), melakukan adaptive replanning dan negosiasi jendela sandar."
    skills = [{"id": "berth_allocation", "name": "Berth allocation MIP", "description": "Time-indexed MIP (HiGHS)",
               "tags": ["optimization", "mip", "planning"]},
              {"id": "berth_negotiation", "name": "Negosiasi jadwal sandar", "description": "Alternating offers + mediasi",
               "tags": ["negotiation"]}]

    def __init__(self, ctx, repo: SmartPortRepo):
        self.repo = repo
        super().__init__(ctx)

    def reset_state(self) -> None:
        super().reset_state()
        self.replan_pending = False

    @property
    def policy(self) -> dict:
        return self.ctx.governance["decision_policy"]

    # ------------------------------------------------------------- perceive
    def on_eta_update(self, msg: dict) -> dict:
        changed = []
        for v in msg["payload"]["vessels"]:
            row = self.repo.db.one("SELECT eta_current FROM vessel_call WHERE vessel_id=?", (v["vessel_id"],))
            if row and abs(tu.from_local(row["eta_current"]) - v["eta_h"]) >= 0.25:
                changed.append(v["vessel_id"])
            self.repo.update_eta(v["vessel_id"], v["eta_h"])
        self.beliefs["last_eta_update"] = {v["vessel_id"]: tu.clock(v["eta_h"]) for v in msg["payload"]["vessels"]}
        if not self.repo.current_plan() or changed:
            return self.replan("eta_update", changed)
        return {"no_change": True}

    def on_document_status(self, msg: dict) -> dict:
        changed = []
        for item in msg["payload"]["vessels"]:
            row = self.repo.db.one("SELECT doc_status FROM vessel_call WHERE vessel_id=?", (item["vessel_id"],))
            if row and row["doc_status"] != item["status"]:
                self.repo.db.execute("UPDATE vessel_call SET doc_status=? WHERE vessel_id=?",
                                     (item["status"], item["vessel_id"]))
                changed.append(item["vessel_id"])
        self.beliefs["doc_status"] = {i["vessel_id"]: i["status"] for i in msg["payload"]["vessels"]}
        if changed and self.repo.current_plan():
            return self.replan("document_status", changed)
        return {"changed": changed}

    # ---------------------------------------------------------- reason/act
    def replan(self, trigger: str, affected: list[str]) -> dict:
        if self.repo.open_negotiation():
            self.replan_pending = True
            self.decide("DEFER_REPLAN", f"Pemicu {trigger} saat negosiasi berjalan",
                        ["Negosiasi aktif harus diselesaikan dahulu agar komitmen konsisten"],
                        "Tunda replanning hingga negosiasi selesai", share=False)
            return {"deferred": True}
        self.state = "planning"
        self.goal = "Minimalkan waktu tunggu & konflik dermaga"
        vessels, berths, prev, now = self.repo.vessels(), self.repo.berths(), self.repo.current_plan(), self.ctx.now()
        frozen = {vid for vid, p in prev.items() if p["start_h"] <= now + self.policy["freeze_window_h"]}
        if prev and not self.ctx.adaptive:
            res = bm.fcfs_shift(vessels, berths, prev, now)
            return self.publish(res, trigger, "static", vessels, berths, prev,
                                ["Strategi STATIC: rencana dibekukan, tidak ada re-optimasi",
                                 "Kapal mempertahankan dermaga & urutan; mulai = max(ETA baru, dermaga kosong)"])
        mode = "local" if self.ctx.strategy == "local" else "global"
        res = bm.solve(vessels, berths, now, mode, prev, frozen, policy=self.policy)
        if not res["plan"]:
            res = bm.fcfs_shift(vessels, berths, prev, now) if prev else res
        reasoning = [f"MIP {mode}: {res.get('n_vars', 0)} variabel biner, {res.get('n_constraints', 0)} kendala, "
                     f"status {res['status']}, {res.get('solve_ms', 0)} ms",
                     f"Kapal dibekukan (sudah/sedang sandar): {sorted(frozen) or '-'}",
                     f"Kapal terdampak: {affected or '-'}"]
        if mode == "local":
            reasoning.append("Fungsi tujuan LOKAL: hanya penalti kontrak (>2 jam) + biaya crane SmartPort")
        if self.ctx.strategy == "collaborative" and prev:
            partner_ids = {v["vessel_id"] for v in vessels if v["is_partner_agent"]}
            new = {p["vessel_id"]: p for p in res["plan"]}
            changed = [vid for vid in partner_ids if vid not in frozen and vid in prev and (
                new[vid]["berth_id"] != prev[vid]["berth_id"] or abs(new[vid]["start_h"] - prev[vid]["start_h"]) > 1e-6)]
            if changed:
                return self.start_negotiation(res, trigger, affected, changed, vessels, berths, prev, frozen, reasoning)
        return self.publish(res, trigger, mode, vessels, berths, prev, reasoning)

    def start_negotiation(self, base: dict, trigger: str, affected: list[str], changed: list[str], vessels, berths,
                          prev, frozen, reasoning) -> dict:
        self.state = "negotiating"
        partner_ids = {v["vessel_id"] for v in vessels if v["is_partner_agent"]}
        primary = next((v for v in affected if v in partner_ids), changed[0])
        candidates = [base]
        vmap = {v["vessel_id"]: v for v in vessels}
        base_berth = next(p["berth_id"] for p in base["plan"] if p["vessel_id"] == primary)
        for b in berths:
            if b["berth_id"] != base_berth and bm.compatible(vmap[primary], b):
                r = bm.solve(vessels, berths, self.ctx.now(), "global", prev, frozen, force={primary: b["berth_id"]},
                             policy=self.policy)
                if r["plan"]:
                    candidates.append(r)
        keep = {vid: p["berth_id"] for vid, p in prev.items() if vid not in frozen}
        r = bm.solve(vessels, berths, self.ctx.now(), "global", prev, frozen, force=keep, policy=self.policy)
        if r["plan"]:
            candidates.append(r)
        # Opsi mahal bagi terminal tetapi menguntungkan pelayaran: mobilisasi mobile crane + gang lembur
        gang_cost = bm.COSTS["extra_gang_jt"]
        gb = next((b for b in berths if b["berth_id"] == base_berth), None)
        if gb and gb["cranes"] < 4:
            berths_x = [dict(b, cranes=b["cranes"] + 1) if b["berth_id"] == base_berth else b for b in berths]
            r = bm.solve(vessels, berths_x, self.ctx.now(), "global", prev, frozen, policy=self.policy)
            if r["plan"]:
                r["extra_gang_berth"] = base_berth
                r["berths_used"] = berths_x
                r["objective"] = round(r["objective"] + gang_cost, 3)
                candidates.append(r)
        seen, options = set(), []
        for r in candidates:
            sig = (tuple((p["vessel_id"], p["berth_id"], p["start_h"], p["end_h"]) for p in r["plan"]),
                   r.get("extra_gang_berth"))
            if sig in seen:
                continue
            seen.add(sig)
            oid = "ABCDEFG"[len(options)]
            bused = r.get("berths_used", berths)
            extra = gang_cost if r.get("extra_gang_berth") else 0.0
            options.append({"option_id": oid, "plan": r["plan"], "objective": r["objective"],
                            "sp_utility": round(bm.sp_utility(r["plan"], vessels, bused, prev)["utility"] - extra, 2),
                            "metrics": bm.plan_metrics(r["plan"], vessels, bused),
                            "extra_gang_berth": r.get("extra_gang_berth"),
                            "label": (f"+1 mobile crane & gang lembur di {r['extra_gang_berth']} (biaya terminal "
                                      f"Rp{gang_cost:.0f} jt)") if r.get("extra_gang_berth") else "",
                            "ol_view": [{"vessel_id": p["vessel_id"], "berth_id": p["berth_id"], "start_h": p["start_h"],
                                         "end_h": p["end_h"]} for p in r["plan"] if p["vessel_id"] in partner_ids]})
            if len(options) >= self.policy["max_options"]:
                break
        if len(options) == 1:
            return self.publish(base, trigger, "global", vessels, berths, prev, reasoning + ["Hanya satu opsi layak"])
        ranked = sorted(options, key=lambda o: -o["sp_utility"])
        neg = {"neg_id": f"NEG-{uuid.uuid4().hex[:6].upper()}", "conversation_id": new_conversation_id("berth"),
               "counterpart": "oceanline.negotiation_agent", "trigger": trigger, "status": "OPEN", "round": 1,
               "max_rounds": self.policy["negotiation_max_rounds"], "options": options, "offers": [ranked[0]["option_id"]],
               "rejected": [], "created_sim": tu.to_local(self.ctx.now()), "log": [],
               "context": {"trigger": trigger, "affected": affected}}
        neg["log"].append({"round": 1, "by": "smartport", "action": "PROPOSE", "option": ranked[0]["option_id"]})
        self.repo.save_negotiation(neg)
        self.decide("NEGOTIATE", f"Replanning ({trigger}) mengubah jendela sandar kapal mitra {changed}",
                    reasoning + [f"Opsi {o['option_id']}{' [' + o['label'] + ']' if o['label'] else ''}: "
                                 f"U_SP={o['sp_utility']} jt, objektif global={o['objective']}, "
                                 f"rata tunggu {o['metrics']['avg_wait_h']} j" for o in options] +
                    [f"Strategi negosiasi: alternating offers, konsesi linear, maks {neg['max_rounds']} ronde"],
                    f"PROPOSE opsi {ranked[0]['option_id']} ke OceanLine", alternatives=[o["option_id"] for o in options],
                    conversation_id=neg["conversation_id"])
        self._propose(neg, ranked[0]["option_id"])
        return {"negotiation": neg["neg_id"], "options": len(options)}

    def _propose(self, neg: dict, offer: str) -> None:
        self.send(neg["counterpart"], "PROPOSE", "berth_negotiation", {
            "neg_id": neg["neg_id"], "round": neg["round"], "max_rounds": neg["max_rounds"], "offer": offer,
            "trigger": neg["trigger"], "rejected_by_terminal": neg.get("rejected", []),
            "options": [{"option_id": o["option_id"], "vessels": o["ol_view"], "note": o.get("label", "")}
                        for o in neg["options"]]},
            conversation_id=neg["conversation_id"], reply_required=True)

    def on_berth_negotiation(self, msg: dict) -> dict:
        p, mt = msg["payload"], msg["message_type"]
        neg = self.repo.get_negotiation(p["neg_id"])
        if not neg or neg["status"] not in ("OPEN",):
            return {"ignored": "negotiation closed"}
        neg.setdefault("rejected", [])
        stored = self.repo.db.one("SELECT offers_json FROM negotiation WHERE neg_id=?", (neg["neg_id"],))
        neg["offers"] = json.loads(stored["offers_json"] or "[]")
        neg["rejected"] = [x["option"] for x in neg["log"] if x.get("action") == "REJECT_COUNTER"]
        util = {o["option_id"]: o["sp_utility"] for o in neg["options"]}
        k, K = neg["round"], neg["max_rounds"]
        if mt == "ACCEPT":
            neg["log"].append({"round": k, "by": "oceanline", "action": "ACCEPT", "option": p["option_id"]})
            return self.finalize(neg, p["option_id"], f"OceanLine menerima opsi {p['option_id']} pada ronde {k}")
        # COUNTER_PROPOSE
        counter = p["option_id"]
        neg["log"].append({"round": k, "by": "oceanline", "action": "COUNTER_PROPOSE", "option": counter})
        u_best = max(util.values())
        u_res = u_best - float(self.policy["max_concession_jt"])

        def aspiration(r: int) -> float:  # konsesi linear berbasis waktu: ronde 1 = utilitas terbaik, ronde K = reservasi
            return u_best - (u_best - u_res) * ((r - 1) / max(K - 1, 1))

        reasoning = [f"Counter OceanLine: opsi {counter} U_SP={util[counter]} jt",
                     f"Aspirasi SmartPort ronde {k}/{K} = {aspiration(k):.1f} jt (konsesi maks {self.policy['max_concession_jt']} jt)"]
        if util[counter] >= aspiration(k) - 1e-6:
            self.decide("NEGOTIATE", "COUNTER_PROPOSE diterima untuk dievaluasi", reasoning,
                        f"ACCEPT counter-proposal opsi {counter}", conversation_id=neg["conversation_id"])
            neg["log"].append({"round": k, "by": "smartport", "action": "ACCEPT", "option": counter})
            return self.finalize(neg, counter, f"SmartPort menerima counter opsi {counter} pada ronde {k}")
        # tawaran baru yang rasional hanya opsi yang belum ditawarkan DAN lebih baik bagi terminal daripada counter
        remaining = [o for o in sorted(neg["options"], key=lambda o: -o["sp_utility"])
                     if o["option_id"] not in neg["offers"] and o["option_id"] not in neg["rejected"]
                     and o["option_id"] != counter and o["sp_utility"] > util[counter]]
        if k < K and not remaining and util[counter] >= aspiration(k + 1) - 1e-6:
            # tidak ada opsi baru untuk ditawarkan -> konsesi waktu: terima counter pada ronde berikutnya
            neg["round"] = k + 1
            reasoning.append(f"Tidak ada opsi lain; aspirasi ronde {k + 1} turun ke {aspiration(k + 1):.1f} jt")
            self.decide("NEGOTIATE", "Konsesi berbasis waktu", reasoning,
                        f"ACCEPT counter-proposal opsi {counter} (ronde {k + 1})", conversation_id=neg["conversation_id"])
            neg["log"].append({"round": k + 1, "by": "smartport", "action": "ACCEPT", "option": counter})
            return self.finalize(neg, counter, f"SmartPort berkonsesi dan menerima opsi {counter} pada ronde {k + 1}")
        neg["log"].append({"round": k, "by": "smartport", "action": "REJECT_COUNTER", "option": counter})
        neg["rejected"].append(counter)
        if k < K and remaining:
            neg["round"] = k + 1
            nxt = remaining[0]["option_id"]
            neg["offers"].append(nxt)
            neg["log"].append({"round": k + 1, "by": "smartport", "action": "PROPOSE", "option": nxt})
            self.repo.save_negotiation(neg)
            self.decide("NEGOTIATE", "Counter di bawah aspirasi", reasoning,
                        f"Tolak counter {counter}; konsesi -> PROPOSE opsi {nxt} (ronde {k + 1})",
                        conversation_id=neg["conversation_id"])
            self._propose(neg, nxt)
            return {"round": k + 1, "offer": nxt}
        if k < K:  # bertahan pada tawaran terbaik sebelumnya; pihak lawan mungkin berkonsesi di ronde berikutnya
            neg["round"] = k + 1
            keep_offer = max((o for o in neg["options"] if o["option_id"] in neg["offers"]),
                             key=lambda o: o["sp_utility"])["option_id"]
            neg["log"].append({"round": k + 1, "by": "smartport", "action": "PROPOSE", "option": keep_offer})
            self.repo.save_negotiation(neg)
            self.decide("NEGOTIATE", "Counter di bawah aspirasi & tidak ada opsi yang lebih baik", reasoning,
                        f"Tolak counter {counter}; ulangi tawaran {keep_offer} (ronde {k + 1})",
                        conversation_id=neg["conversation_id"])
            self._propose(neg, keep_offer)
            return {"round": k + 1, "offer": keep_offer}
        neg["status"] = "MEDIATION"
        self.repo.save_negotiation(neg)
        self.decide("REQUEST_MEDIATION", "Tidak ada kesepakatan hingga batas ronde", reasoning,
                    "Minta mediasi Global Control Center (Nash bargaining)", conversation_id=neg["conversation_id"])
        self.send("control_center.coordinator_agent", "MEDIATION_REQUEST", "berth_mediation", {
            "neg_id": neg["neg_id"], "partner": neg["counterpart"], "requester": self.address,
            "options": [{"option_id": o["option_id"], "vessels": o["ol_view"], "objective": o["objective"]}
                        for o in neg["options"]],
            "requester_utilities": util, "requester_reservation": u_res},
            conversation_id=neg["conversation_id"])
        return {"mediation": True}

    def on_mediation_result(self, msg: dict) -> dict:
        p = msg["payload"]
        neg = self.repo.get_negotiation(p["neg_id"])
        if not neg:
            return {}
        neg["log"].append({"round": neg["round"], "by": "control_center", "action": "MEDIATE", "option": p["option_id"]})
        return self.finalize(neg, p["option_id"], f"Mediator memilih opsi {p['option_id']} ({p.get('method')})")

    def finalize(self, neg: dict, option_id: str, how: str) -> dict:
        neg["status"] = "AGREED"
        neg["agreed_option"] = option_id
        neg["closed_sim"] = tu.to_local(self.ctx.now())
        self.repo.save_negotiation(neg)
        opt = next(o for o in neg["options"] if o["option_id"] == option_id)
        self.send(neg["counterpart"], "CONFIRM", "berth_negotiation",
                  {"neg_id": neg["neg_id"], "option_id": option_id, "round": neg["round"]},
                  conversation_id=neg["conversation_id"])
        extra = []
        if opt.get("extra_gang_berth"):  # komitmen sumber daya tambahan dicatat permanen di data dermaga
            self.repo.db.execute("UPDATE berth SET cranes=cranes+1, status='EXTRA_GANG' WHERE berth_id=?",
                                 (opt["extra_gang_berth"],))
            extra = [f"Komitmen: +1 mobile crane & gang lembur di {opt['extra_gang_berth']} "
                     f"(biaya terminal Rp{bm.COSTS['extra_gang_jt']:.0f} jt)"]
        vessels, berths = self.repo.vessels(), self.repo.berths()
        prev = self.repo.current_plan()
        res = {"plan": opt["plan"], "objective": opt["objective"], "status": "negotiated", "solve_ms": 0.0}
        out = self.publish(res, neg["trigger"], "global", vessels, berths, prev,
                           [how, f"Negosiasi {neg['neg_id']} selesai dalam {neg['round']} ronde"] + extra,
                           negotiation_id=neg["neg_id"])
        if self.replan_pending:
            self.replan_pending = False
            self.replan("deferred_trigger", [])
        return out

    def publish(self, res: dict, trigger: str, mode: str, vessels, berths, prev, reasoning: list[str],
                negotiation_id: str | None = None) -> dict:
        plan = res["plan"]
        spu = bm.sp_utility(plan, vessels, berths, prev)
        metrics = bm.plan_metrics(plan, vessels, berths)
        moved = [p["vessel_id"] for p in plan if prev and p["vessel_id"] in prev and
                 prev[p["vessel_id"]]["berth_id"] != p["berth_id"]]
        version = self.repo.save_plan(plan, {"sim_h": self.ctx.now(), "trigger": trigger, "strategy": self.ctx.strategy,
                                             "mode": mode, "solver": res.get("status"), "objective": res.get("objective"),
                                             "solve_ms": res.get("solve_ms"), "sp_utility": spu["utility"],
                                             "negotiation_id": negotiation_id, "note": "; ".join(reasoning)[:500]},
                                      vessels)
        human = len(moved) > 3
        self.decide("PUBLISH_BERTH_PLAN", f"Pemicu: {trigger}", reasoning + [
            f"Kapal pindah dermaga: {moved or '-'}", f"U_SP = {spu['utility']} jt"],
            f"Rencana sandar v{version}: {_fmt_plan(plan)}", kpi=metrics, human_required=human,
            details={"plan": plan})
        vstatus = {v["vessel_id"]: ("PENDING" if v["doc_status"] == "INCOMPLETE" else "SCHEDULED") for v in vessels}
        partner = {v["vessel_id"] for v in vessels if v["is_partner_agent"]}
        self.send("oceanline.negotiation_agent", "INFORM", "berth_plan", {
            "plan_version": version, "mode": mode, "negotiation_id": negotiation_id,
            "assignments": [dict(p, status=vstatus[p["vessel_id"]]) for p in plan if p["vessel_id"] in partner]})
        self.state = "idle"
        self.ctx.agents["yard_agent"].publish_availability(f"berth_plan_v{version}")
        return {"plan_version": version, "metrics": metrics}


class YardAgent(Agent):
    name = "yard_agent"
    role = "Yard & Container Release Agent"
    description = "Menghitung waktu bongkar & ketersediaan kontainer (setelah release Bea Cukai), memberi tahu trucker."
    skills = [{"id": "container_availability", "name": "Ketersediaan kontainer", "description": "Discharge + release",
               "tags": ["yard", "monitoring"]}]

    def __init__(self, ctx, repo: SmartPortRepo):
        self.repo = repo
        super().__init__(ctx)

    def reset_state(self) -> None:
        super().reset_state()
        self.last_sig: dict[str, str] = {}

    def compute_availability(self) -> list[dict]:
        pol = self.ctx.governance["decision_policy"]
        plan = self.repo.current_plan()
        out = []
        for b in self.repo.batches():
            if b["trucker"] != "GlobalCargo":
                continue
            p = plan.get(b["vessel_id"])
            if not p:
                continue
            pure = max(0.5, (p["end_h"] - p["start_h"]) - 1.0)
            n = b["containers"]
            discharge = [round(p["start_h"] + 0.5 + (b["stow_from"] + (b["stow_to"] - b["stow_from"]) * (i + 0.5) / n) * pure
                               + pol["yard_transfer_h"], 2) for i in range(n)]
            if b["hold"]:
                status, ready, rel = "HOLD", [None] * n, None
            elif b["customs_status"] in ("RELEASED", "RELEASED_MITA"):
                rel = tu.from_local(b["release_time"]) if b["release_time"] else 0.0
                status, ready = "CONFIRMED", [round(max(d, rel), 2) for d in discharge]
            else:
                rel = None
                status, ready = "ESTIMATED", [round(d + pol["est_customs_lead_h"], 2) for d in discharge]
            out.append({"shipment_id": b["shipment_id"], "consignee": b["consignee"], "vessel_id": b["vessel_id"],
                        "berth_id": p["berth_id"], "containers": n, "discharge_h": discharge, "ready_h": ready,
                        "status": status, "customs_status": b["customs_status"], "release_h": rel, "lane": b["lane"]})
        return out

    def publish_availability(self, reason: str) -> None:
        avail = self.compute_availability()
        sig = _sig(avail)
        if self.last_sig.get("gc") != sig:
            self.last_sig["gc"] = sig
            self.send("globalcargo.fleet_planning_agent", "INFORM", "container_availability",
                      {"reason": reason, "batches": avail})
        ie = [{"shipment_id": a["shipment_id"], "vessel_id": a["vessel_id"], "berth_id": a["berth_id"],
               "discharge_from_h": a["discharge_h"][0], "discharge_to_h": a["discharge_h"][-1],
               "ready_from_h": a["ready_h"][0], "ready_to_h": a["ready_h"][-1], "status": a["status"]}
              for a in avail if a["consignee"] == "PT ImportExport Corp"]
        sig2 = _sig(ie)
        if self.last_sig.get("ie") != sig2:
            self.last_sig["ie"] = sig2
            self.send("importexport.procurement_agent", "INFORM", "container_status", {"reason": reason, "batches": ie})
        self.beliefs = {"batches_tracked": len(avail), "on_hold": sum(a["status"] == "HOLD" for a in avail),
                        "awaiting_customs": sum(a["status"] == "ESTIMATED" for a in avail)}

    def on_customs_release(self, msg: dict) -> dict:
        p = msg["payload"]
        bid = f"BT-{p['shipment_ref']}"
        if p["status"] == "RELEASED":
            self.repo.db.execute("UPDATE container_batch SET customs_status='RELEASED', release_time=?, hold=0, lane=? "
                                 "WHERE batch_id=?", (tu.to_local(p["release_h"]), p.get("lane"), bid))
            decision = f"Kontainer {p['shipment_ref']} boleh keluar gate mulai {tu.clock(p['release_h'])}"
        else:  # HOLD / INSPECTION
            self.repo.db.execute("UPDATE container_batch SET customs_status=?, hold=1, lane=? WHERE batch_id=?",
                                 (p["status"], p.get("lane"), bid))
            decision = f"Kontainer {p['shipment_ref']} DITAHAN (pindah ke area inspeksi)"
        self.decide("UPDATE_RELEASE", f"Status Bea Cukai {p['status']} untuk {p['shipment_ref']} (jalur {p.get('lane')})",
                    ["Governance: gate-out hanya bila status RELEASED dari otoritas resmi"], decision)
        self.publish_availability(f"customs_{p['status'].lower()}_{p['shipment_ref']}")
        return {"batch": bid, "status": p["status"]}


class OperationsMonitorAgent(Agent):
    name = "operations_monitor_agent"
    role = "Monitoring Agent"
    description = "Memantau status crane & dermaga; perubahan kapasitas memicu adaptive replanning."
    skills = [{"id": "equipment_monitoring", "name": "Monitoring crane", "description": "Deteksi degradasi kapasitas",
               "tags": ["monitoring"]}]

    def __init__(self, ctx, repo: SmartPortRepo):
        self.repo = repo
        super().__init__(ctx)

    def on_crane_breakdown(self, msg: dict) -> dict:
        p = msg["payload"]
        b = self.repo.db.one("SELECT degradations, cranes FROM berth WHERE berth_id=?", (p["berth_id"],))
        degr = json.loads(b["degradations"] or "[]")
        degr.append({"from_h": float(p["from_h"]), "to_h": float(p["to_h"]), "factor": float(p["factor"]),
                     "note": p.get("description", "")})
        self.repo.db.execute("UPDATE berth SET degradations=?, status='DEGRADED' WHERE berth_id=?",
                             (json.dumps(degr), p["berth_id"]))
        plan = self.repo.current_plan()
        affected = [vid for vid, x in plan.items() if x["berth_id"] == p["berth_id"] and x["end_h"] > p["from_h"]
                    and x["start_h"] < p["to_h"]]
        self.beliefs = {"degraded_berth": p["berth_id"], "factor": p["factor"]}
        self.decide("DETECT_DISRUPTION", f"Crane {p['berth_id']} rusak: produktivitas x{p['factor']} "
                    f"{tu.clock(p['from_h'])}-{tu.clock(p['to_h'])}",
                    [f"Kapal terdampak pada rencana aktif: {affected or '-'}",
                     "Durasi bongkar dihitung ulang dengan integrasi profil produktivitas"],
                    "Minta berth_planning_agent melakukan adaptive replanning")
        return self.ctx.agents["berth_planning_agent"].replan("crane_breakdown", affected)
