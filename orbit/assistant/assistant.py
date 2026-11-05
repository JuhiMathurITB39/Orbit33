"""Grounded assistant. Works with NO LLM: intent + entity extraction -> fixed, parameterised tool functions ->
numbered evidence [E#] -> template answer. An optional LLM provider may rephrase the answer, but its output is
accepted only if every number and citation it uses appears in the evidence; otherwise the template answer is used.
The assistant never executes model-generated SQL and says so when a question is unsupported."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from orbit.api import services as sv

INTENTS = [
    ("stockout", r"stock ?out|run out|out of stock|at risk|shortage|risk of"),
    ("reorder", r"reorder|re-order|order (how|what|more)|how (much|many).*(order|buy)|replenish|what should (i|we) order"),
    ("supplier", r"supplier|vendor|late deliver|lead time|on[- ]time"),
    ("forecast", r"forecast|predict|demand (next|for)|expect(ed)? (sales|demand)|next (7|14|28|week|month)"),
    ("model", r"accuracy|wape|model|backtest|how good|error"),
    ("simulation", r"saving|cost|simulat|policy|baseline|compare"),
    ("kpi", r"overview|summary|kpi|status|priorit|what needs|attention|today"),
    ("inventory", r"inventory|on[- ]hand|cover|stock level|how much stock"),
]


@dataclass
class Evidence:
    items: list = field(default_factory=list)

    def add(self, kind: str, title: str, source: str, values: dict) -> str:
        eid = f"E{len(self.items) + 1}"
        self.items.append(dict(id=eid, kind=kind, title=title, source=source, values=values))
        return f"[{eid}]"


def classify(q: str) -> str:
    ql = q.lower()
    for name, pat in INTENTS:
        if re.search(pat, ql):
            return name
    return "unsupported"


def extract_entities(rt: sv.Runtime, q: str, item_id: str | None, store_id: str | None) -> tuple[str | None, str | None, str | None]:
    pos = rt.position()
    ql = q.lower()
    if not item_id:
        for it in pos.item_id.unique():
            if it.lower() in ql:
                item_id = it
                break
    if not store_id:
        for s in pos.store_id.unique():
            if s.lower() in ql:
                store_id = s
                break
    m = re.search(r"\bS0\d\b", q, flags=re.I)
    return item_id, store_id, (m.group(0).upper() if m else None)


def _f(x, n=0):
    return f"{x:,.{n}f}"


def answer(rt: sv.Runtime, question: str, item_id=None, store_id=None, provider=None) -> dict:
    meta = rt.meta()
    ev = Evidence()
    intent = classify(question)
    item_id, store_id, supplier_id = extract_entities(rt, question, item_id, store_id)
    notes: list[str] = []
    supported = True
    pos = rt.position()
    tag = "(simulated data)" if meta["data_kind"] != "REAL_M5" else ""

    if intent == "stockout":
        if item_id:
            df = pos[pos.item_id == item_id]
            if store_id:
                df = df[df.store_id == store_id]
            lines = []
            for r in df.sort_values("prob", ascending=False).itertuples():
                c = ev.add("stockout_risk", f"{r.item_id} @ {r.store_id}", "serving_stockout_risk",
                           dict(prob=round(r.prob, 3), on_hand=r.on_hand, on_order=r.on_order, forecast7=round(r.forecast7, 1),
                                flagged=bool(r.flagged)))
                lines.append(f"{r.item_id} at {r.store_id}: stockout probability over the next 7 days is {r.prob:.0%} "
                             f"(on hand {_f(r.on_hand)}, on order {_f(r.on_order)}, forecast demand {_f(r.forecast7)}) {c}.")
            text = " ".join(lines) or f"No data for {item_id}."
        else:
            df = pos[pos.store_id == store_id] if store_id else pos
            top = df.sort_values("expected_loss", ascending=False).head(5)
            parts = []
            for r in top.itertuples():
                c = ev.add("stockout_risk", f"{r.item_id} @ {r.store_id}", "serving_stockout_risk",
                           dict(prob=round(r.prob, 3), expected_loss=round(r.expected_loss, 2), on_hand=r.on_hand))
                parts.append(f"{r.item_id} ({r.store_id}) {r.prob:.0%}, est. exposure ${_f(r.expected_loss)} {c}")
            text = (f"{int(df.flagged.sum())} of {len(df)} product-store series are flagged at risk {tag}. "
                    f"Highest exposure: " + "; ".join(parts) + ".")
    elif intent == "reorder":
        if not (item_id and store_id):
            text = "Please name a product and a store (for example FOODS_3_090 at CA_1) so I can pull its recommendation."
            supported = False
        else:
            from orbit.api.schemas import RecommendRequest
            rec = sv.recommend(rt, RecommendRequest(store_id=store_id, item_ids=[item_id]))
            r = rec["rows"][0]
            c = ev.add("replenishment", f"{item_id} @ {store_id}", "optimiser (default assumptions)",
                       dict(qty=r["qty"], packs=r["packs"], reorder_level=round(r["reorder_level"], 1),
                            safety_stock=round(r["safety_stock"], 1), on_hand=r["on_hand"], on_order=r["on_order"]))
            text = f"{r['reason']} {c} Safety stock at 95% service is {_f(r['safety_stock'])} units ({r['ss_method']} method)."
    elif intent == "supplier":
        data = sv.suppliers(rt)["items"]
        if supplier_id:
            data = [s for s in data if s["supplier_id"] == supplier_id]
        elif item_id:
            sid = pos[pos.item_id == item_id].supplier_id.iloc[0]
            data = [s for s in data if s["supplier_id"] == sid]
        else:
            data = sorted(data, key=lambda s: -(s["pred_late_prob"] or 0))[:3]
        parts = []
        for s in data:
            c = ev.add("supplier", s["supplier_id"], "serving_supplier_risk",
                       dict(risk=s["risk_level"], recent_late_rate=s["recent_late_rate"], pred_late_prob=round(s["pred_late_prob"] or 0, 3),
                            recent_lead_mean=round(s["recent_lead_mean"] or 0, 1)))
            parts.append(f"{s['supplier_id']} is {s['risk_level']} risk: recent late rate {(s['recent_late_rate'] or 0):.0%}, "
                         f"average lead time {(s['recent_lead_mean'] or 0):.1f} days {c}")
        text = "; ".join(parts) + ". Supplier behaviour is synthetic and the late-delivery model has modest skill (see model card)."
    elif intent == "forecast":
        if not (item_id and store_id):
            text = "Please name a product and a store so I can look up its forecast."
            supported = False
        else:
            f = sv.forecast_for(rt, item_id, store_id)
            parts = []
            for h in f["horizons"]:
                c = ev.add("forecast", f"{item_id} @ {store_id} {h['horizon']}d", "serving_forecast",
                           dict(horizon=h["horizon"], point=round(h["point"], 1), lo=round(h["lo"], 1), hi=round(h["hi"], 1)))
                parts.append(f"{h['horizon']}-day: {h['point']:.0f} units (80% interval {h['lo']:.0f}-{h['hi']:.0f}) {c}")
            text = f"Forecast demand for {item_id} at {store_id}: " + "; ".join(parts) + "."
    elif intent == "model":
        m = sv.model_comparison(rt)["forecast"]["horizons"]
        parts = []
        for h, d in m.items():
            sel = d["selected"]
            w = d["metrics"][sel]["test"]["wape"]
            nv = d["metrics"].get("seasonal_naive", {}).get("test", {}).get("wape")
            c = ev.add("model_metric", f"{h}-day forecast", "serving_metrics", dict(selected=sel, test_wape=round(w, 3),
                       seasonal_naive_wape=round(nv, 3) if nv else None))
            parts.append(f"{h}-day: {sel} test WAPE {w:.1%}" + (f" vs seasonal-naive {nv:.1%}" if nv else "") + f" {c}")
        text = "Held-out (latest rolling-origin fold) accuracy: " + "; ".join(parts) + ". These are results on simulated data."
    elif intent == "simulation":
        s = sv.simulation_results(rt)
        pol = s["policies"]
        base, tuned, orb = (pol[k]["total_cost"]["mean"] for k in ("baseline_default", "baseline_tuned", "orbit"))
        iv = {k: pol[k]["avg_inventory_value"]["mean"] for k in ("baseline_tuned", "orbit")}
        c = ev.add("simulation", "policy backtest", "serving_metrics:simulation",
                   dict(default=round(base), tuned=round(tuned), orbit=round(orb), inv_value_tuned=round(iv["baseline_tuned"]),
                        inv_value_orbit=round(iv["orbit"])))
        text = (f"In the simulated 365-day backtest, total cost was ${_f(base)} for the default baseline, ${_f(tuned)} for a tuned "
                f"baseline and ${_f(orb)} for ORBIT ({(orb / tuned - 1):+.1%} vs tuned) {c}. ORBIT holds about "
                f"${_f(iv['orbit'])} average inventory vs ${_f(iv['baseline_tuned'])} for the tuned baseline, so much of the "
                f"gain comes from retuned cover and batching rather than the ML forecast. This is a simulation, not realised savings.")
    elif intent in ("kpi", "inventory") and not item_id:
        k = sv.kpis(rt)
        c = ev.add("kpi", "portfolio KPIs", "kpis", dict(at_risk=k["products_at_risk"], series=k["series_total"],
                   exposure=round(k["financial_exposure"]), inventory_value=round(k["total_inventory_value"])))
        a = k["recommended_actions"][0]
        c2 = ev.add("action", f"{a['item_id']} @ {a['store_id']}", "kpis", dict(prob=round(a["prob"], 3), qty=a["recommended_qty"]))
        text = (f"{k['products_at_risk']} of {k['series_total']} series are at stockout risk, with estimated exposure "
                f"${_f(k['financial_exposure'])} and inventory value ${_f(k['total_inventory_value'])} {c}. "
                f"Top priority: {a['item_id']} at {a['store_id']} ({a['prob']:.0%} risk) - {a['reason']} {c2}")
    elif intent in ("inventory", "kpi") and item_id:
        d = sv.inventory(rt, store_id, item_id, None, 5, 0)["items"]
        parts = []
        for r in d:
            c = ev.add("inventory", f"{r['item_id']} @ {r['store_id']}", "inventory",
                       dict(on_hand=r["on_hand"], reorder_point=round(r["reorder_point"], 1), cover_days=round(r["cover_days"], 1)))
            parts.append(f"{r['store_id']}: on hand {_f(r['on_hand'])}, reorder point {_f(r['reorder_point'])}, "
                         f"{r['cover_days']:.1f} days of cover {c}")
        text = f"{item_id} - " + "; ".join(parts) + "."
    else:
        supported = False
        text = ("I can answer questions about stockout risk, reorder quantities, forecasts, suppliers, model accuracy, "
                "inventory and the policy simulation using this platform's data. I can't answer that one from the available data.")
        intent = "unsupported"

    provider_name = "template"
    if provider is not None and ev.items and supported:
        out = provider.rewrite(question, text, ev.items)
        if out and _grounded(out, text, ev.items):
            text, provider_name = out, provider.name
        elif out:
            notes.append("LLM output rejected: it contained figures or citations not present in the evidence")
    return dict(answer=text, supported=supported, intent=intent, citations=ev.items, provider=provider_name,
                data_kind=meta["data_kind"], notes=notes)


def _numbers(s: str) -> set[str]:
    return {x.replace(",", "") for x in re.findall(r"\d[\d,]*\.?\d*", s)}


def _grounded(out: str, template: str, evidence: list) -> bool:
    allowed = _numbers(template) | _numbers(str(evidence))
    cites = set(re.findall(r"\[E(\d+)\]", out))
    ids = {e["id"][1:] for e in evidence}
    if not cites <= ids:
        return False
    return all(n.rstrip(".") in allowed or n.rstrip(".") in {"7", "14", "28", "80", "95"} for n in _numbers(out)
               if len(n.rstrip(".")) > 0)


class NullProvider:
    name = "none"

    def rewrite(self, *a):
        return None


class AnthropicProvider:
    """Optional. Needs ANTHROPIC_API_KEY and ORBIT_LLM_MODEL. NOT verified live in the build environment."""
    name = "anthropic"

    def __init__(self, key: str, model: str):
        self.key, self.model = key, model

    def rewrite(self, question, draft, evidence):
        import requests
        try:
            r = requests.post("https://api.anthropic.com/v1/messages", timeout=20,
                              headers={"x-api-key": self.key, "anthropic-version": "2023-06-01"},
                              json=dict(model=self.model, max_tokens=400,
                                        system="Rewrite the draft answer clearly. Use only facts, numbers and [E#] citations present in the draft/evidence. Never add new figures.",
                                        messages=[dict(role="user", content=f"Question: {question}\nDraft: {draft}\nEvidence: {evidence}")]))
            r.raise_for_status()
            return r.json()["content"][0]["text"]
        except Exception:
            return None


def get_provider():
    key, model = os.environ.get("ANTHROPIC_API_KEY"), os.environ.get("ORBIT_LLM_MODEL")
    return AnthropicProvider(key, model) if key and model else None
