import numpy as np
import pandas as pd
import pytest

from orbit.optim.replenish import InfeasiblePlanError, Items, OptParams, optimise, validate_plan
from orbit.optim.safety_stock import reorder_point, safety_stock


def items(n=6, **kw):
    r = np.random.default_rng(0)
    d = dict(mu_d=r.uniform(2, 30, n), sigma_d=r.uniform(1, 8, n), lead_mean=np.full(n, 5.0), lead_sd=np.full(n, 1.0),
             on_hand=r.uniform(0, 50, n).round(), on_order=np.zeros(n), unit_cost=r.uniform(1, 8, n), price=r.uniform(9, 14, n),
             pack=np.array([6.0, 12, 6, 4, 10, 6][:n]), moq=np.array([12.0, 12, 12, 8, 10, 12][:n]), vol=np.full(n, 0.02),
             supplier=np.array([0, 0, 1, 1, 2, 2][:n]), store=np.zeros(n, int),
             ids=pd.DataFrame(dict(item_id=list("abcdef")[:n], store_id="s", supplier_id="x", cat_id="c")))
    d.update(kw)
    return Items(**d)


def test_plan_is_feasible_and_pack_aligned():
    it = items(); p = OptParams()
    plan = optimise(it, p)
    assert validate_plan(it, p, plan.Q) == []
    assert (plan.Q >= 0).all()


def test_zero_budget_orders_nothing_and_reports_binding():
    it = items(); p = OptParams(budget=0.0)
    plan = optimise(it, p)
    assert plan.Q.sum() == 0 and any("budget" in b for b in plan.binding)


def test_tight_budget_respected():
    it = items(); p = OptParams(budget=100.0)
    plan = optimise(it, p)
    assert (plan.Q * it.unit_cost).sum() <= 100.0 + 1e-6
    assert validate_plan(it, p, plan.Q) == []


def test_supplier_capacity_respected():
    it = items(); p = OptParams(supplier_capacity={0: 20.0, 1: 20.0, 2: 20.0})
    plan = optimise(it, p)
    for s in (0, 1, 2):
        assert plan.Q[it.supplier == s].sum() <= 20.0 + 1e-6


def test_storage_capacity_respected():
    it = items(); p = OptParams(storage_capacity={0: 5.0})
    plan = optimise(it, p)
    assert ((it.on_hand + it.on_order + plan.Q) * it.vol).sum() <= 5.0 + 1e-6 or plan.Q.sum() == 0


def test_overfull_storage_is_infeasible_or_orders_nothing():
    it = items(on_hand=np.full(6, 5000.0)); p = OptParams(storage_capacity={0: 1.0})
    try:
        plan = optimise(it, p)
        assert plan.Q.sum() == 0
    except InfeasiblePlanError:
        pass


def test_zero_demand_item_is_not_ordered():
    it = items(mu_d=np.array([0.0, 10, 10, 10, 10, 10]), sigma_d=np.array([0.0, 2, 2, 2, 2, 2]), on_hand=np.zeros(6))
    plan = optimise(it, OptParams())
    assert plan.Q[0] == 0 and plan.Q[1:].sum() > 0


def test_ample_stock_means_no_order():
    it = items(on_hand=np.full(6, 10_000.0))
    assert optimise(it, OptParams()).Q.sum() == 0


def test_higher_demand_does_not_reduce_orders():
    base = optimise(items(), OptParams()).Q.sum()
    high = optimise(items(mu_d=items().mu_d * 2, sigma_d=items().sigma_d * 2), OptParams()).Q.sum()
    assert high >= base


def test_service_mode_meets_target_when_unconstrained():
    it = items(on_hand=np.zeros(6)); p = OptParams(mode="service", service_level=0.95, batching=False)
    plan = optimise(it, p)
    assert (plan.cost["service_level"] >= 0.93).all()


def test_costlier_stockouts_raise_orders():
    lo = optimise(items(), OptParams(stockout_cost_multiplier=0.5, batching=False)).Q.sum()
    hi = optimise(items(), OptParams(stockout_cost_multiplier=4.0, batching=False)).Q.sum()
    assert hi >= lo


def test_safety_stock_validation_and_method_switch():
    with pytest.raises(ValueError):
        safety_stock(10, 2, 5, 1, 7, 1.2)
    low = safety_stock(np.array([1.0]), np.array([1.5]), np.array([3.0]), np.array([0.5]), 7, 0.95)
    hi = safety_stock(np.array([60.0]), np.array([5.0]), np.array([5.0]), np.array([1.0]), 7, 0.95)
    assert low["method"][0] != hi["method"][0]
    assert (low["ss"] >= 0).all()
    assert reorder_point(np.array([10.0]), np.array([2.0]), np.array([5.0]), np.array([1.0]), 0.95)["rop"][0] > 50


def test_safety_stock_monotone_in_service_level():
    a = safety_stock(np.array([20.0]), np.array([4.0]), np.array([5.0]), np.array([1.0]), 7, 0.90)["ss"][0]
    b = safety_stock(np.array([20.0]), np.array([4.0]), np.array([5.0]), np.array([1.0]), 7, 0.99)["ss"][0]
    assert b > a
