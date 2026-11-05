"""Constrained replenishment optimiser.

Decision: order quantity Q_i >= 0 (units) for each product-store i at a review point.

Demand model. Demand over the protection period P_i = L_i + R is D_i ~ Gamma(mean mu_i, variance v_i)
(moment matched; non-negative and right-skewed, valid for slow and fast items; moments from safety_stock.period_moments).
Order-up-to position y_i = pos_i + Q_i where pos_i = on-hand + on-order.

Objective (the three costs requested; purchase cost of units that are eventually sold is not a decision-relevant
operating cost, it appears as working capital / budget instead):

    J_i(Q_i) = K_i * 1[Q_i > 0]                      ordering cost per PO line
             + h_i * R * E[(y_i - D_i)^+]            holding cost of expected leftover for one review cycle
             + p_i * E[(D_i - y_i)^+]                lost-sales (stockout) cost of expected shortfall

with h_i = holding_rate * unit_cost_i / 365 per unit-day, p_i = (price_i - cost_i) + goodwill * price_i unless
overridden. Without constraints the continuous optimum satisfies the newsvendor condition F(y*) = p / (p + h R).
In `service` mode p is set to alpha/(1-alpha) * h * R so that F(y*) = alpha (the target service level).

Hard constraints
  * integer packs:     Q_i = k_i * pack_i
  * minimum order:     Q_i = 0  or  Q_i >= MOQ_i
  * budget:            sum_i c_i Q_i <= B                      (optional)
  * supplier capacity: sum_{i in supplier s} Q_i <= cap_s       (optional)
  * storage:           sum_{i in store k} vol_i (pos_i + Q_i) <= storage_k   (optional)

Algorithm: (1) closed-form per-item optimum; if all coupling constraints hold it is exact for the relaxed problem.
(2) otherwise solve the coupled continuous relaxation with SciPy SLSQP (analytic gradients). (3) round to feasible pack
quantities choosing, per item, the cheapest of {0, floor-pack, ceil-pack} under MOQ; (4) greedy repair removing the
packs with the smallest cost increase per unit of violated resource until every hard constraint holds; (5) an
independent validator re-checks every constraint. This is a heuristic for the integer problem (the continuous
relaxation is convex); optimality of the integer solution is not claimed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import optimize, stats
from scipy.special import gammainc

from orbit.optim.safety_stock import gamma_params, period_moments, reorder_point, safety_stock


class InfeasiblePlanError(RuntimeError):
    pass


@dataclass
class OptParams:
    review_days: float = 7.0
    holding_rate_annual: float = 0.25
    order_cost: float = 12.0
    goodwill: float = 0.25
    stockout_cost_multiplier: float = 1.0
    mode: str = "cost"  # "cost" | "service"
    service_level: float = 0.95
    batching: bool = True          # (s,S) policy: order EOQ batches when position < reorder level
    max_cover_days: float = 60.0   # cap on batch size, in days of demand (shelf-life / obsolescence guard)
    budget: float | None = None
    supplier_capacity: dict | None = None  # supplier index -> units
    storage_capacity: dict | None = None  # store index -> volume units


@dataclass
class Items:
    """Per-item inputs (aligned arrays of length n)."""
    mu_d: np.ndarray          # forecast mean daily demand
    sigma_d: np.ndarray       # daily demand standard deviation
    lead_mean: np.ndarray
    lead_sd: np.ndarray
    on_hand: np.ndarray
    on_order: np.ndarray
    unit_cost: np.ndarray
    price: np.ndarray
    pack: np.ndarray
    moq: np.ndarray
    vol: np.ndarray
    supplier: np.ndarray      # integer index
    store: np.ndarray         # integer index
    ids: pd.DataFrame | None = None

    @property
    def n(self) -> int:
        return len(self.mu_d)


# ------------------------------------------------------------------------------------------ cost model
def _gamma_stats(y, k, th):
    """E[(D-y)+] and F(y) for D~Gamma(k, theta). y may be an array."""
    y = np.maximum(y, 0.0)
    z = y / th
    F_k, F_k1 = gammainc(k, z), gammainc(k + 1.0, z)
    mean = k * th
    short = mean * (1.0 - F_k1) - y * (1.0 - F_k)
    return np.maximum(short, 0.0), F_k


def unit_costs(it: Items, p: OptParams):
    h = p.holding_rate_annual * it.unit_cost / 365.0
    lost = ((it.price - it.unit_cost).clip(min=0) + p.goodwill * it.price) * p.stockout_cost_multiplier
    if p.mode == "service":
        lost = p.service_level / (1.0 - p.service_level) * h * p.review_days
    return h, np.maximum(lost, 1e-9)


def expected_costs(it: Items, p: OptParams, Q: np.ndarray) -> dict:
    """Per-item expected cost breakdown for order quantities Q."""
    mean, var = period_moments(it.mu_d, it.sigma_d, it.lead_mean, it.lead_sd, p.review_days)
    k, th = gamma_params(mean, var)
    h, lost = unit_costs(it, p)
    y = it.on_hand + it.on_order + Q
    short, F = _gamma_stats(y, k, th)
    left = np.maximum(y - mean + short, 0.0)
    dead = mean < 1e-9
    short, left = np.where(dead, 0.0, short), np.where(dead, y, left)
    hold_c = h * p.review_days * left
    order_c = np.where(Q > 0, p.order_cost, 0.0)
    lost_c = lost * short
    return dict(holding=hold_c, ordering=order_c, lost_sales=lost_c, total=hold_c + order_c + lost_c,
                service_level=np.where(dead, 1.0, F), expected_shortfall=short, expected_leftover=left,
                demand_mean=mean, demand_sd=np.sqrt(var), y=y)


# ------------------------------------------------------------------------------------------ relaxation
def eoq_batch(it: Items, p: OptParams) -> np.ndarray:
    """Economic order quantity sqrt(2 K d / h) (d = forecast daily demand, h = holding cost per unit-day), bounded
    below by the MOQ and above by `max_cover_days` of demand. Valid for roughly steady demand."""
    h_day = np.maximum(p.holding_rate_annual * it.unit_cost / 365.0, 1e-12)
    eoq = np.sqrt(2.0 * p.order_cost * np.maximum(it.mu_d, 0.0) / h_day)
    eoq = np.minimum(eoq, p.max_cover_days * np.maximum(it.mu_d, 0.0))
    return np.maximum(eoq, it.moq)


def _levels(it: Items, p: OptParams):
    """Reorder level s (= cost-optimal protection-period target position) and EOQ batch."""
    mean, var = period_moments(it.mu_d, it.sigma_d, it.lead_mean, it.lead_sd, p.review_days)
    k, th = gamma_params(mean, var)
    h, lost = unit_costs(it, p)
    cr = lost / (lost + h * p.review_days)
    y_star = stats.gamma.ppf(np.clip(cr, 1e-6, 1 - 1e-6), a=k, scale=th)
    return np.where(mean < 1e-9, 0.0, y_star), eoq_batch(it, p)


def _unconstrained(it: Items, p: OptParams) -> np.ndarray:
    y_star, eoq = _levels(it, p)
    pos = it.on_hand + it.on_order
    if p.batching:  # (s,S): when position < s, order up to s + EOQ
        return np.where((pos < y_star) & (it.mu_d > 1e-9), y_star + eoq - pos, 0.0)
    return np.maximum(y_star - pos, 0.0)


def _constraint_matrices(it: Items, p: OptParams):
    """Linear inequality rows A q <= b over Q (continuous relaxation)."""
    A, b, names = [], [], []
    if p.budget is not None:
        A.append(it.unit_cost.copy()); b.append(p.budget); names.append("budget")
    for s, cap in (p.supplier_capacity or {}).items():
        row = (it.supplier == s).astype(float)
        if row.any():
            A.append(row); b.append(cap); names.append(f"supplier_capacity[{s}]")
    for k_, cap in (p.storage_capacity or {}).items():
        m = it.store == k_
        if m.any():
            row = np.where(m, it.vol, 0.0)
            A.append(row); b.append(cap - float((it.vol[m] * (it.on_hand + it.on_order)[m]).sum())); names.append(f"storage[{k_}]")
    if not A:
        return np.zeros((0, it.n)), np.zeros(0), []
    return np.vstack(A), np.asarray(b, float), names


def _relaxed(it: Items, p: OptParams, q0: np.ndarray, A: np.ndarray, b: np.ndarray) -> np.ndarray:
    mean, var = period_moments(it.mu_d, it.sigma_d, it.lead_mean, it.lead_sd, p.review_days)
    k, th = gamma_params(mean, var)
    h, lost = unit_costs(it, p)
    pos = it.on_hand + it.on_order
    s = np.maximum(mean, 1.0)  # variable scaling x = Q / s  (conditioning)

    def f(x):
        Q = x * s
        y = pos + Q
        short, F = _gamma_stats(y, k, th)
        left = np.maximum(y - mean + short, 0.0)
        val = (h * p.review_days * left + lost * short).sum()
        grad = (h * p.review_days * F - lost * (1.0 - F)) * s
        return val, grad

    cons = []
    if len(b):
        # A Q <= b  ->  b - A (x*s) >= 0
        cons.append({"type": "ineq", "fun": lambda x: b - A @ (x * s), "jac": lambda x: -A * s[None, :]})
    x0 = np.minimum(q0 / s, 1e4)
    res = optimize.minimize(f, x0, jac=True, bounds=[(0.0, 1e4)] * it.n, constraints=cons, method="SLSQP",
                            options=dict(maxiter=300, ftol=1e-9))
    Q = np.maximum(res.x * s, 0.0)
    # numerical safety: scale into the feasible set if SLSQP stopped marginally outside
    if len(b):
        viol = A @ Q - b
        if (viol > 1e-6).any():
            fac = np.min(np.where(A @ Q > 1e-12, np.maximum(b, 0) / np.maximum(A @ Q, 1e-12), 1.0))
            Q *= min(1.0, max(fac, 0.0))
    return Q


# ------------------------------------------------------------------------------------------ rounding / repair
def _candidates(it: Items, Qc: np.ndarray):
    pk, moq = it.pack, it.moq
    floor_q = np.floor(Qc / pk + 1e-9) * pk
    ceil_q = np.ceil(Qc / pk - 1e-9) * pk
    moq_q = np.ceil(moq / pk) * pk  # smallest legal non-zero order
    lo = np.where(floor_q < moq_q, moq_q, floor_q)
    hi = np.where(ceil_q < moq_q, moq_q, ceil_q)
    return lo, hi


def _round(it: Items, p: OptParams, Qc: np.ndarray, rule_based: bool = False) -> np.ndarray:
    """Round a continuous quantity to a legal pack quantity (>= MOQ).
    * rule_based (the (s,S) trigger already decided to order): nearest pack, at least the MOQ, never skipped here.
    * service mode: round up so the service target is not undershot.
    * otherwise: choose the cheapest of {0, floor-pack, ceil-pack} by expected cycle cost (this is what stops
      tiny, uneconomic orders when the fixed order cost exceeds the expected benefit)."""
    lo, hi = _candidates(it, Qc)
    moq_q = np.ceil(it.moq / it.pack) * it.pack
    if rule_based:
        q = np.where(np.rint(Qc / it.pack) * it.pack < moq_q, moq_q, np.rint(Qc / it.pack) * it.pack)
        return np.where(Qc > 1e-9, q, 0.0)
    if p.mode == "service":
        return np.where(Qc > 1e-9, np.where(hi < moq_q, moq_q, hi), 0.0)
    cands = [np.zeros(it.n), lo, hi]
    costs = np.vstack([expected_costs(it, p, c)["total"] for c in cands])
    return np.vstack(cands)[np.argmin(costs, axis=0), np.arange(it.n)]


def _resource_rows(it: Items, p: OptParams):
    A, b, names = _constraint_matrices(it, p)
    return A, b, names


def _repair(it: Items, p: OptParams, Q: np.ndarray) -> np.ndarray:
    A, b, _ = _resource_rows(it, p)
    Q = Q.copy()
    moq_q = np.ceil(it.moq / it.pack) * it.pack
    for _ in range(100000):
        if not len(b):
            break
        viol = A @ Q - b
        if (viol <= 1e-9).all():
            break
        r = int(np.argmax(viol))  # most violated row
        members = np.where((A[r] > 0) & (Q > 0))[0]
        if len(members) == 0:
            raise InfeasiblePlanError("constraints cannot be satisfied even with zero orders "
                                      "(storage already exceeded?)")
        reduced = Q[members] - it.pack[members]
        reduced = np.where(reduced < moq_q[members], 0.0, reduced)  # dropping below MOQ cancels the line
        base = expected_costs(it, p, Q)["total"][members]
        new = np.array([expected_costs(_slice(it, [m]), p, np.array([rq]))["total"][0]
                        for m, rq in zip(members, reduced)])
        freed = A[r, members] * (Q[members] - reduced)
        score = (new - base) / np.maximum(freed, 1e-12)  # cost increase per unit of violated resource freed
        j = int(np.argmin(score))
        Q[members[j]] = reduced[j]
    return Q


def _slice(it: Items, idx) -> Items:
    idx = np.asarray(idx)
    return Items(*(getattr(it, f)[idx] for f in ("mu_d", "sigma_d", "lead_mean", "lead_sd", "on_hand", "on_order",
                                                   "unit_cost", "price", "pack", "moq", "vol", "supplier", "store")))


def validate_plan(it: Items, p: OptParams, Q: np.ndarray, tol: float = 1e-6) -> list[str]:
    """Independent feasibility check of a plan. Returns a list of violation messages (empty = feasible)."""
    v = []
    if (Q < -tol).any():
        v.append("negative quantity")
    ratio = Q / it.pack
    if (np.abs(ratio - np.round(ratio)) > 1e-6).any():
        v.append("quantity not a multiple of pack size")
    small = (Q > 0) & (Q < it.moq - tol)
    if small.any():
        v.append(f"{int(small.sum())} order(s) below MOQ")
    A, b, names = _constraint_matrices(it, p)
    for row, bound, name in zip(A, b, names):
        if row @ Q > bound + 1e-6:
            v.append(f"{name} violated: {row @ Q:.2f} > {bound:.2f}")
    return v


@dataclass
class Plan:
    Q: np.ndarray
    items: Items
    params: OptParams
    cost: dict
    binding: list = field(default_factory=list)
    method: str = ""
    safety: dict | None = None
    rop: dict | None = None
    constraints: list = field(default_factory=list)  # per-constraint used / limit / utilisation
    reorder_level: np.ndarray | None = None
    eoq: np.ndarray | None = None

    def to_frame(self) -> pd.DataFrame:
        it, c = self.items, self.cost
        df = pd.DataFrame({
            "qty": self.Q, "packs": self.Q / it.pack, "target_position": c["y"],
            "on_hand": it.on_hand, "on_order": it.on_order, "demand_mean_protection": c["demand_mean"],
            "expected_service_level": c["service_level"], "holding_cost": c["holding"],
            "ordering_cost": c["ordering"], "expected_lost_sales_cost": c["lost_sales"],
            "total_expected_cost": c["total"], "order_spend": self.Q * it.unit_cost,
            "safety_stock": self.safety["ss"] if self.safety else np.nan,
            "reorder_point": self.rop["rop"] if self.rop else np.nan,
            "ss_method": self.safety["method"] if self.safety else None,
            "reorder_level": self.reorder_level, "eoq_batch": self.eoq,
        })
        if it.ids is not None:
            df = pd.concat([it.ids.reset_index(drop=True), df], axis=1)
        return df


def optimise(it: Items, p: OptParams | None = None, service_for_ss: float | None = None) -> Plan:
    """Solve for order quantities. Raises InfeasiblePlanError only if even the empty order violates a constraint."""
    p = p or OptParams()
    n = it.n
    if n == 0:
        raise ValueError("no items to optimise")
    for name in ("mu_d", "sigma_d", "lead_mean", "lead_sd", "on_hand", "on_order", "unit_cost", "price", "pack",
                 "moq", "vol"):
        a = getattr(it, name)
        if len(a) != n or not np.all(np.isfinite(a)):
            raise ValueError(f"invalid input '{name}' (length or non-finite)")
    if (it.mu_d < 0).any() or (it.on_hand < 0).any() or (it.pack <= 0).any():
        raise ValueError("demand/on-hand must be >= 0 and pack > 0")
    A, b, names = _constraint_matrices(it, p)
    if len(b) and (b < -1e-9).any():
        raise InfeasiblePlanError("a capacity/budget constraint is already violated with zero orders")
    y_star, eoq = _levels(it, p)
    pos = it.on_hand + it.on_order
    rule_based = False
    if p.batching:
        trigger = (pos < y_star) & (it.mu_d > 1e-9)
        base = np.where(trigger, y_star - pos, 0.0)   # replenish the protection-period target
        batch = np.where(trigger, eoq, 0.0)           # plus an economic batch to amortise the fixed order cost
        if not len(b) or (A @ (base + batch) <= b + 1e-9).all():
            Qc, method, rule_based = base + batch, "sS_eoq_batch", True
        elif (A @ base <= b + 1e-9).all():
            lo_, hi_ = 0.0, 1.0   # largest batch fraction that satisfies every linear constraint (monotone -> bisect)
            for _ in range(40):
                mid = (lo_ + hi_) / 2
                lo_, hi_ = (mid, hi_) if (A @ (base + mid * batch) <= b + 1e-9).all() else (lo_, mid)
            Qc, method, rule_based = base + lo_ * batch, f"sS_eoq_batch_scaled({lo_:.2f})", True
        else:
            Qc, method = _relaxed(it, p, base, A, b), "slsqp_relaxation"
    else:
        q_unc = np.maximum(y_star - pos, 0.0)
        method = "closed_form_newsvendor"
        if len(b) and (A @ q_unc > b + 1e-9).any():
            Qc, method = _relaxed(it, p, q_unc, A, b), "slsqp_relaxation"
        else:
            Qc = q_unc
    Q = _round(it, p, Qc, rule_based)
    if len(b) and (A @ Q > b + 1e-9).any():
        Q = _repair(it, p, Q)
        method += "+greedy_repair"
    problems = validate_plan(it, p, Q)
    if problems:
        raise InfeasiblePlanError("; ".join(problems))
    cost = expected_costs(it, p, Q)
    A2, b2, names2 = _constraint_matrices(it, p)
    status = []
    for row, bound, n_ in zip(A2, b2, names2):
        used = float(row @ Q)
        util = used / bound if bound > 1e-9 else 1.0  # a zero limit is always binding
        status.append(dict(name=n_, used=used, limit=float(bound), utilisation=float(util), binding=bool(util >= 0.95)))
    binding = [s["name"] for s in status if s["binding"]]
    ssl = service_for_ss if service_for_ss is not None else p.service_level
    sstock = safety_stock(it.mu_d, it.sigma_d, it.lead_mean, it.lead_sd, p.review_days, ssl)
    rp = reorder_point(it.mu_d, it.sigma_d, it.lead_mean, it.lead_sd, ssl)
    lvl, eoq = _levels(it, p)
    return Plan(Q=Q, items=it, params=p, cost=cost, binding=binding, method=method, safety=sstock, rop=rp,
                constraints=status, reorder_level=lvl, eoq=eoq)


def cost_sensitivity(it: Items, p: OptParams, multipliers=(0.5, 1.0, 2.0)) -> list[dict]:
    """Re-solve under scaled stockout cost and holding rate; report change in total order quantity and expected cost."""
    base = optimise(it, p)
    rows = []
    for param in ("stockout_cost_multiplier", "holding_rate_annual"):
        for m in multipliers:
            q = OptParams(**{**p.__dict__})
            setattr(q, param, getattr(p, param) * m)
            plan = optimise(it, q)
            rows.append(dict(parameter=param, multiplier=m, total_qty=float(plan.Q.sum()),
                             qty_change_vs_base=float(plan.Q.sum() - base.Q.sum()),
                             expected_total_cost=float(plan.cost["total"].sum()),
                             expected_service_level=float(np.average(plan.cost["service_level"],
                                                                     weights=np.maximum(plan.cost["demand_mean"], 1e-9))),
                             items_ordered=int((plan.Q > 0).sum())))
    return rows
