"""Medallion pipeline stages: bronze (raw + ingestion metadata) -> silver (validated, typed) -> gold (star schema).

Design properties (each is exercised by tests/):
  * Incremental   : work is split into month batches; a batch is skipped when its input hash is unchanged.
  * Idempotent    : outputs are written atomically with deterministic names; DB loads are delete-then-insert
                    inside one transaction, so re-running never duplicates rows.
  * Recoverable   : checkpoints record per-batch status; after a crash a re-run does only the missing work.
  * Observable    : row counts, rejected rows, duplicates, DQ results and timings are persisted.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from orbit.config import Settings
from orbit.data.simulate_m5 import generate_m5_files
from orbit.data.supply_sim import (
    LATE_THRESHOLD_DAYS,
    WAREHOUSES,
    BaselinePolicy,
    build_world,
    run_engine,
    supplier_versions,
    warehouse_for_store,
)
from orbit.logging_setup import get_logger
from orbit.pipeline import quality as dq
from orbit.pipeline.checkpoint import Checkpoints, FaultInjector, now_iso, sha256_bytes, with_retry
from orbit.warehouse.db import transaction

log = get_logger("orbit.pipeline")

M5_FILES = ("calendar.csv", "sell_prices.csv", "sales_train_evaluation.csv")
BASELINE_TRIGGER, BASELINE_UPTO = 12.0, 24.0  # documented incumbent policy parameters (days of cover)
# Bump when supply_sim logic changes so checkpointed outputs are rebuilt (hash covers data + params + logic).
SUPPLY_LOGIC_VERSION = "2"
SALES_SCHEMA = {"item_id": "str", "store_id": "str", "date": "date", "d_idx": "int", "units": "float"}


class DataQualityError(RuntimeError):
    """A blocking data-quality failure (schema/columns); the batch is not promoted."""


@dataclass
class Ctx:
    settings: Settings
    conn: object
    ck: Checkpoints
    run_id: str
    fault: FaultInjector = field(default_factory=FaultInjector)
    retries: int = 2
    source_dir: Path | None = None
    data_kind: str = ""
    metrics: dict = field(default_factory=dict)
    calendar: pd.DataFrame | None = None

    def m(self, stage: str) -> dict:
        return self.metrics.setdefault(stage, dict(batches=0, skipped=0, rows_in=0, rows_out=0, rejected=0,
                                                     duplicates=0, dq_failures=0, duration_s=0.0))

    def record_dq(self, results: list[dq.DQResult]) -> int:
        failed = 0
        for r in results:
            self.conn.execute(
                "INSERT INTO dq_results(run_id,layer,table_name,check_name,passed,severity,n_checked,n_failed,"
                "detail,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (self.run_id, r.layer, r.table, r.check, int(r.passed), r.severity, r.n_checked, r.n_failed,
                 r.detail_json(), now_iso()),
            )
            failed += 0 if r.passed else 1
        self.conn.commit()
        return failed

    def lineage(self, output: str, inputs: list[str], stage: str) -> None:
        for i in inputs:
            self.conn.execute("INSERT OR REPLACE INTO data_lineage(output,input,stage,run_id) VALUES (?,?,?,?)",
                              (output, i, stage, self.run_id))
        self.conn.commit()


# --------------------------------------------------------------------------------------------- helpers
def _atomic_write_csv_gz(df: pd.DataFrame, path: Path) -> int:
    """Write deterministic gzip CSV atomically (tmp file + rename). Returns bytes written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    with open(tmp, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, compresslevel=6) as gz:
        gz.write(buf.getvalue().encode())
    os.replace(tmp, path)
    return path.stat().st_size


def _read_csv_gz(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


def _hash_df(df: pd.DataFrame) -> str:
    return sha256_bytes(df.to_csv(index=False).encode())


def _hash_file(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _manifest_add(layer_dir: Path, key: str, meta: dict) -> None:
    p = layer_dir / "_manifest.json"
    data = json.loads(p.read_text()) if p.exists() else {}
    data[key] = meta
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    os.replace(tmp, p)


def run_batch(ctx: Ctx, stage: str, batch_key: str, input_hash: str | None, fn) -> dict | None:
    """Run one idempotent batch with checkpointing, retry and fault injection. Returns fn's metrics or None if
    the batch was skipped because its input is unchanged and previously succeeded."""
    m = ctx.m(stage)
    if ctx.ck.is_done(stage, batch_key, input_hash):
        m["skipped"] += 1
        return None
    ctx.ck.start(stage, batch_key, input_hash)
    t0 = time.perf_counter()

    def attempt():
        ctx.fault.maybe_fail(stage, batch_key)
        return fn()

    try:
        res = with_retry(attempt, retries=ctx.retries, what=f"{stage}/{batch_key}")
    except Exception as exc:
        ctx.ck.fail(stage, batch_key, f"{type(exc).__name__}: {exc}")
        raise
    dur = time.perf_counter() - t0
    ctx.ck.succeed(stage, batch_key, input_hash, rows_in=res.get("rows_in"), rows_out=res.get("rows_out"),
                   rows_rejected=res.get("rejected", 0), duplicates=res.get("duplicates", 0), duration_s=dur)
    m["batches"] += 1
    m["duration_s"] += dur
    for k in ("rows_in", "rows_out", "rejected", "duplicates", "dq_failures"):
        m[k] += int(res.get(k, 0) or 0)
    return res


# --------------------------------------------------------------------------------------------- discovery
def discover_source(ctx: Ctx) -> None:
    s = ctx.settings
    if s.m5_dir is not None:
        missing = [f for f in M5_FILES if not (Path(s.m5_dir) / f).exists()]
        if missing:
            raise FileNotFoundError(f"ORBIT_M5_DIR={s.m5_dir} is missing files: {missing}")
        ctx.source_dir, ctx.data_kind = Path(s.m5_dir), "REAL_M5"
    else:
        d = s.raw_dir / "m5sim"
        if not all((d / f).exists() for f in M5_FILES):
            generate_m5_files(d, seed=s.seed)
        ctx.source_dir, ctx.data_kind = d, "SIMULATED_M5_SCHEMA"
    cal = pd.read_csv(ctx.source_dir / "calendar.csv")
    # Real M5's calendar runs past the last observed sales day (1,969 rows vs 1,941 sales columns: the competition's
    # evaluation period). Keep only days that have sales, otherwise those days would be simulated as zero demand.
    sales_days = {c for c in pd.read_csv(ctx.source_dir / "sales_train_evaluation.csv", nrows=0).columns if c.startswith("d_")}
    ctx.calendar = cal[cal["d"].isin(sales_days)].reset_index(drop=True)
    ctx.conn.execute("INSERT INTO meta_kv(key,value) VALUES ('data_kind',?) ON CONFLICT(key) DO UPDATE SET "
                     "value=excluded.value", (ctx.data_kind,))
    ctx.conn.commit()
    log.info("source discovered", extra={"dir": str(ctx.source_dir), "kind": ctx.data_kind})


def _subset_spec(ctx: Ctx) -> str:
    st = ctx.settings
    return f"stores={','.join(sorted(st.stores))};items_per_dept={st.items_per_dept}"


def _subset(ctx: Ctx):
    """Optional ORBIT_STORES / ORBIT_ITEMS_PER_DEPT subset. Returns (stores, items) or None for 'use everything'.
    Items are the first N ids (sorted) in each department, chosen from the sales file so prices and sales stay aligned."""
    st = ctx.settings
    if not st.stores and not st.items_per_dept:
        return None
    ids = pd.read_csv(ctx.source_dir / "sales_train_evaluation.csv", usecols=["item_id", "dept_id", "store_id"])
    stores = set(st.stores) if st.stores else set(ids.store_id.unique())
    unknown = stores - set(ids.store_id.unique())
    if unknown:
        raise ValueError(f"ORBIT_STORES contains unknown stores: {sorted(unknown)}")
    items = ids.drop_duplicates("item_id")[["item_id", "dept_id"]].sort_values("item_id")
    if st.items_per_dept:
        items = items.groupby("dept_id").head(st.items_per_dept)
    return stores, set(items.item_id)


def _months(cal: pd.DataFrame) -> dict[str, list[str]]:
    ym = cal["date"].str[:7]
    return {k: g["d"].tolist() for k, g in cal.groupby(ym, sort=True)}


# --------------------------------------------------------------------------------------------- bronze
def bronze_calendar_prices(ctx: Ctx) -> None:
    for name, stage in (("calendar.csv", "bronze_calendar"), ("sell_prices.csv", "bronze_prices")):
        src = ctx.source_dir / name
        h = _hash_file(src) + (":" + _subset_spec(ctx) if name == "sell_prices.csv" else "")

        def work(src=src, name=name, h=h, stage=stage):
            df = pd.read_csv(src)  # prices: chunked read would be used for the full-size M5 file
            sub = _subset(ctx) if name == "sell_prices.csv" else None
            if sub is not None:
                df = df[df["store_id"].isin(sub[0]) & df["item_id"].isin(sub[1])]
            out = ctx.settings.bronze_dir / stage.split("_")[1] / (name + ".gz")
            size = _atomic_write_csv_gz(df, out)
            _manifest_add(ctx.settings.bronze_dir / stage.split("_")[1], name,
                          dict(source=str(src), sha256=h, rows=len(df), bytes=size, ingested_at=now_iso(),
                               run_id=ctx.run_id, data_kind=ctx.data_kind))
            return dict(rows_in=len(df), rows_out=len(df))

        run_batch(ctx, stage, name, h, work)
        ctx.lineage(f"bronze/{stage.split('_')[1]}/{name}.gz", [str(src)], stage)


def bronze_sales(ctx: Ctx) -> None:
    src = ctx.source_dir / "sales_train_evaluation.csv"
    header = pd.read_csv(src, nrows=0).columns.tolist()
    id_cols = [c for c in header if not c.startswith("d_")]
    present = set(header)
    fingerprint = f"{src.stat().st_size}:{len(header)}:{_subset_spec(ctx)}"
    sub = _subset(ctx)
    months = _months(ctx.calendar)
    # fast path: whole-file fingerprint unchanged and every month already ingested -> no re-read
    if ctx.ck.is_done("bronze_sales", "_source", fingerprint):
        done = {r["batch_key"] for r in ctx.ck.summary() if r["stage"] == "bronze_sales" and r["status"] == "succeeded"}
        if all(ym in done for ym, d in months.items() if any(c in present for c in d)):
            ctx.m("bronze_sales")["skipped"] += len(done) - 1
            return
    for ym, dcols in months.items():
        cols = id_cols + [c for c in dcols if c in present]
        if len(cols) == len(id_cols):
            continue
        df = pd.read_csv(src, usecols=cols)[cols]
        if sub is not None:
            df = df[df["store_id"].isin(sub[0]) & df["item_id"].isin(sub[1])].reset_index(drop=True)
        h = _hash_df(df)

        def work(df=df, ym=ym, h=h):
            out = ctx.settings.bronze_dir / "sales" / f"ym={ym}.csv.gz"
            size = _atomic_write_csv_gz(df, out)
            _manifest_add(ctx.settings.bronze_dir / "sales", ym,
                          dict(source=str(src), sha256=h, rows=len(df), cols=len(df.columns), bytes=size,
                               ingested_at=now_iso(), run_id=ctx.run_id, data_kind=ctx.data_kind))
            return dict(rows_in=len(df), rows_out=len(df))

        run_batch(ctx, "bronze_sales", ym, h, work)
    ctx.ck.start("bronze_sales", "_source", fingerprint)
    ctx.ck.succeed("bronze_sales", "_source", fingerprint)
    ctx.lineage("bronze/sales/*", [str(src)], "bronze_sales")


# --------------------------------------------------------------------------------------------- silver
def silver_calendar_prices(ctx: Ctx) -> pd.DataFrame:
    cal = _read_csv_gz(ctx.settings.bronze_dir / "calendar" / "calendar.csv.gz")
    cal = cal[cal["d"].isin(set(ctx.calendar["d"]))].reset_index(drop=True)  # drop days after the last observed sales day
    res = [dq.check_schema(cal, {"date": "date", "wm_yr_wk": "int", "d": "str", "wday": "int", "month": "int"},
                           "silver", "calendar"),
           dq.check_unique(cal, ["d"], "silver", "calendar"),
           dq.check_dates_contiguous(cal["date"], "silver", "calendar")]
    ctx.record_dq(res)
    if not all(r.passed for r in res):
        raise DataQualityError(f"calendar failed validation: {[r.check for r in res if not r.passed]}")
    cal["d_idx"] = cal["d"].str[2:].astype(int) - 1
    cal["date_key"] = cal["date"].str.replace("-", "").astype(int)
    out = ctx.settings.silver_dir / "calendar" / "calendar.csv.gz"
    _atomic_write_csv_gz(cal, out)

    pr = _read_csv_gz(ctx.settings.bronze_dir / "prices" / "sell_prices.csv.gz")
    pres = [dq.check_schema(pr, {"store_id": "str", "item_id": "str", "wm_yr_wk": "int", "sell_price": "float"},
                            "silver", "prices"),
            dq.check_unique(pr, ["store_id", "item_id", "wm_yr_wk"], "silver", "prices"),
            dq.check_non_negative(pr, "sell_price", "silver", "prices")]
    ctx.record_dq(pres)
    if not all(r.passed for r in pres):
        raise DataQualityError(f"prices failed validation: {[r.check for r in pres if not r.passed]}")
    _atomic_write_csv_gz(pr, ctx.settings.silver_dir / "prices" / "sell_prices.csv.gz")
    ctx.lineage("silver/calendar", ["bronze/calendar/calendar.csv.gz"], "silver_calendar")
    ctx.lineage("silver/prices", ["bronze/prices/sell_prices.csv.gz"], "silver_prices")
    return cal


def silver_sales(ctx: Ctx, cal: pd.DataFrame) -> None:
    prices = _read_csv_gz(ctx.settings.silver_dir / "prices" / "sell_prices.csv.gz")
    manifest = json.loads((ctx.settings.bronze_dir / "sales" / "_manifest.json").read_text())
    cal_small = cal[["d", "date", "d_idx", "wm_yr_wk"]]
    for ym in sorted(manifest):
        bronze_hash = manifest[ym]["sha256"]

        def work(ym=ym):
            wide = _read_csv_gz(ctx.settings.bronze_dir / "sales" / f"ym={ym}.csv.gz")
            id_cols = [c for c in wide.columns if not c.startswith("d_")]
            long = wide.melt(id_vars=id_cols, var_name="d", value_name="units")
            rows_in = len(long)
            results = [dq.check_schema(long, {"item_id": "str", "store_id": "str", "units": "float"},
                                       "silver", "sales")]
            if not results[0].passed:
                ctx.record_dq(results)
                raise DataQualityError(f"sales {ym}: schema check failed {results[0].detail}")
            long = long.merge(cal_small, on="d", how="left")
            results.append(dq.check_nulls(long, ["item_id", "store_id", "date"], "silver", "sales"))
            # duplicate detection on the business key: keep first, count the rest
            dup_mask = long.duplicated(subset=["item_id", "store_id", "d"], keep="first")
            n_dup = int(dup_mask.sum())
            results.append(dq.DQResult("silver", "sales", "duplicate_rows_removed", n_dup == 0, "warn", rows_in, n_dup))
            long = long[~dup_mask]
            # invalid quantities are quarantined, not silently fixed
            units = pd.to_numeric(long["units"], errors="coerce")
            bad = units.isna() | (units < 0) | ((units % 1) != 0)
            results.append(dq.DQResult("silver", "sales", "invalid_quantity_quarantined", int(bad.sum()) == 0, "warn",
                                       len(long), int(bad.sum())))
            rejected = long[bad]
            if len(rejected):
                _atomic_write_csv_gz(rejected, ctx.settings.silver_dir / "_rejected" / "sales" / f"ym={ym}.csv.gz")
            long = long[~bad].copy()
            long["units"] = units[~bad].astype(int)
            # price join (week-level price; NaN before an item is first priced, like real M5)
            long = long.merge(prices, on=["store_id", "item_id", "wm_yr_wk"], how="left")
            results.append(dq.DQResult("silver", "sales", "sell_price_missing_share", True, "warn", len(long),
                                       int(long["sell_price"].isna().sum()),
                                       {"note": "expected before first sale of an item"}))
            # robust outlier flag per series within the month (flag only)
            long["is_outlier"] = (long.groupby(["item_id", "store_id"])["units"]
                                  .transform(lambda s: dq.flag_outliers(s)).astype(int))
            results.append(dq.DQResult("silver", "sales", "outliers_flagged", True, "warn", len(long),
                                       int(long["is_outlier"].sum())))
            keep = ["item_id", "store_id", "dept_id", "cat_id", "state_id", "date", "d_idx", "units", "sell_price",
                    "is_outlier"]
            long = long[keep].sort_values(["item_id", "store_id", "d_idx"])
            results.append(dq.check_unique(long, ["item_id", "store_id", "d_idx"], "silver", "sales"))
            failed = ctx.record_dq(results)
            _atomic_write_csv_gz(long, ctx.settings.silver_dir / "sales" / f"ym={ym}.csv.gz")
            _manifest_add(ctx.settings.silver_dir / "sales", ym, dict(sha256=bronze_hash, rows=len(long),  # noqa: B023 - closure runs within the loop iteration
                          rejected=int(bad.sum()), duplicates=n_dup, run_id=ctx.run_id,
                          profile=dq.profile(long[["units", "sell_price"]])))  # noqa: B023 - closure runs inside the loop iteration
            return dict(rows_in=rows_in, rows_out=len(long), rejected=int(bad.sum()), duplicates=n_dup,
                        dq_failures=failed)

        run_batch(ctx, "silver_sales", ym, bronze_hash, work)
    ctx.lineage("silver/sales/*", ["bronze/sales/*", "silver/calendar", "silver/prices"], "silver_sales")


def _load_silver_sales(ctx: Ctx) -> pd.DataFrame:
    parts = [pd.read_csv(p) for p in sorted((ctx.settings.silver_dir / "sales").glob("ym=*.csv.gz"))]
    return pd.concat(parts, ignore_index=True)


# --------------------------------------------------------------------------------------------- supply (synthetic)
def supply_stage(ctx: Ctx, cal: pd.DataFrame) -> None:
    """Generate SIMULATED inventory / PO / supplier data by running the baseline policy over history."""
    sm = json.loads((ctx.settings.silver_dir / "sales" / "_manifest.json").read_text())
    h = sha256_bytes(json.dumps([sm[k]["sha256"] for k in sorted(sm)] +
                                [ctx.settings.seed, BASELINE_TRIGGER, BASELINE_UPTO, SUPPLY_LOGIC_VERSION],
                                sort_keys=True).encode())

    def work():
        sales = _load_silver_sales(ctx)
        dates = pd.DatetimeIndex(pd.to_datetime(cal.sort_values("d_idx")["date"]))
        world = build_world(sales, dates, seed=ctx.settings.seed)
        res = run_engine(world, BaselinePolicy(BASELINE_TRIGGER, BASELINE_UPTO), 0, world.T, first_review_day=7)
        sd = ctx.settings.silver_dir / "supply"
        ser = world.series.reset_index().rename(columns={"index": "series"})
        # product / store / supplier / warehouse dimensions
        prod = (ser.drop_duplicates("item_id")[["item_id", "dept_id", "cat_id", "supplier_id", "unit_cost",
                                                  "ref_price", "pack_size", "moq_units", "vol"]]
                .rename(columns={"vol": "unit_volume"}))
        _atomic_write_csv_gz(prod, sd / "product_attrs.csv.gz")
        st = pd.DataFrame({"store_id": world.store_ids, "storage_capacity_volume": world.store_capacity})
        st["state_id"] = st.store_id.str[:2]
        st["warehouse_id"] = st.store_id.map(warehouse_for_store)
        _atomic_write_csv_gz(st, sd / "store_attrs.csv.gz")
        _atomic_write_csv_gz(supplier_versions(), sd / "suppliers.csv.gz")
        _atomic_write_csv_gz(pd.DataFrame(WAREHOUSES, columns=["warehouse_id", "name", "region",
                                                               "storage_capacity_units"]), sd / "warehouses.csv.gz")
        # purchase orders
        o = res.orders.copy()
        o["item_id"] = ser.loc[o.series, "item_id"].to_numpy()
        o["store_id"] = ser.loc[o.series, "store_id"].to_numpy()
        o["supplier_id"] = ser.loc[o.series, "supplier_id"].to_numpy()
        o["unit_cost"] = ser.loc[o.series, "unit_cost"].to_numpy()
        o = o.sort_values(["order_day", "series"]).reset_index(drop=True)
        o["po_id"] = [f"PO{n:07d}" for n in range(1, len(o) + 1)]
        # supplier version valid on the order date (SCD2 lookup)
        vers = supplier_versions()
        od = dates[o.order_day.to_numpy()]
        ver = np.ones(len(o), dtype=int)
        for _, v in vers.iterrows():
            m = (o.supplier_id == v.supplier_id).to_numpy() & (od >= pd.Timestamp(v.valid_from)) & \
                (od < pd.Timestamp(v.valid_to))
            ver[m] = v.version
        o["supplier_version"] = ver
        # position before the order (for the replenishment fact)
        o["inv_position_pre"] = (res.on_hand_end[o.series, o.order_day] + res.on_order_end[o.series, o.order_day]
                                 - o.qty_ordered).to_numpy()
        o["is_open"] = (o.arrival_day >= world.T).astype(int)
        _atomic_write_csv_gz(o.drop(columns=["series"]), sd / "purchase_orders.csv.gz")
        # daily inventory snapshot (long)
        S, T = res.on_hand_end.shape
        inv = pd.DataFrame({
            "item_id": np.repeat(ser.item_id.to_numpy(), T), "store_id": np.repeat(ser.store_id.to_numpy(), T),
            "d_idx": np.tile(np.arange(T), S), "on_hand_end": res.on_hand_end.ravel(),
            "on_order_end": res.on_order_end.ravel(), "units_received": res.received.ravel(),
            "units_demanded": res.demand.ravel(), "units_fulfilled": res.fulfilled.ravel(),
            "lost_sales_units": res.lost.ravel()})
        bad_inv = dq.check_no_negative_inventory(inv["on_hand_end"].to_numpy(), "silver", "inventory")
        ctx.record_dq([bad_inv, dq.check_non_negative(inv, "lost_sales_units", "silver", "inventory"),
                       dq.check_date_order(o, "order_day", "arrival_day", "silver", "purchase_orders")])
        if not bad_inv.passed:
            raise DataQualityError("negative inventory generated by simulation")
        _atomic_write_csv_gz(inv, sd / "inventory_daily.csv.gz")
        _manifest_add(sd, "supply", dict(sha256=h, orders=len(o), inventory_rows=len(inv), run_id=ctx.run_id,
                                         kind="SIMULATED", baseline=[BASELINE_TRIGGER, BASELINE_UPTO]))
        return dict(rows_in=int(world.D.size), rows_out=len(inv) + len(o))

    run_batch(ctx, "synth_supply", "all", h, work)
    ctx.lineage("silver/supply/*", ["silver/sales/*"], "synth_supply")


# --------------------------------------------------------------------------------------------- gold
def gold_dims(ctx: Ctx, cal: pd.DataFrame) -> None:
    c, sd = ctx.conn, ctx.settings.silver_dir / "supply"

    def work():
        with transaction(c):
            for r in cal.itertuples(index=False):
                import datetime as _dt
                dt = _dt.date.fromisoformat(r.date)
                c.execute(
                    """INSERT INTO dim_date(date_key,date,d_idx,wm_yr_wk,weekday,wday,month,quarter,year,is_weekend,
                        event_name_1,event_type_1,event_name_2,event_type_2,snap_ca,snap_tx,snap_wi)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(date_key) DO UPDATE SET wm_yr_wk=excluded.wm_yr_wk, event_name_1=excluded.event_name_1,
                         event_type_1=excluded.event_type_1""",
                    (int(r.date_key), r.date, int(r.d_idx), int(r.wm_yr_wk), r.weekday, int(r.wday), int(r.month),
                     (int(r.month) - 1) // 3 + 1, int(r.year), int(dt.weekday() >= 5),
                     None if pd.isna(r.event_name_1) else r.event_name_1,
                     None if pd.isna(r.event_type_1) else r.event_type_1,
                     None if pd.isna(r.event_name_2) else r.event_name_2,
                     None if pd.isna(r.event_type_2) else r.event_type_2,
                     int(r.snap_CA), int(r.snap_TX), int(r.snap_WI)))
            for w in pd.read_csv(sd / "warehouses.csv.gz").itertuples(index=False):
                c.execute("""INSERT INTO dim_warehouse(warehouse_id,name,region,storage_capacity_units) VALUES (?,?,?,?)
                             ON CONFLICT(warehouse_id) DO UPDATE SET name=excluded.name,region=excluded.region,
                             storage_capacity_units=excluded.storage_capacity_units""",
                          (w.warehouse_id, w.name, w.region, float(w.storage_capacity_units)))
            wkey = {r["warehouse_id"]: r["warehouse_key"] for r in c.execute("SELECT * FROM dim_warehouse")}
            for s in pd.read_csv(sd / "store_attrs.csv.gz").itertuples(index=False):
                c.execute("""INSERT INTO dim_store(store_id,state_id,warehouse_key,storage_capacity_volume) VALUES (?,?,?,?)
                             ON CONFLICT(store_id) DO UPDATE SET state_id=excluded.state_id,
                             warehouse_key=excluded.warehouse_key, storage_capacity_volume=excluded.storage_capacity_volume""",
                          (s.store_id, s.state_id, wkey[s.warehouse_id], float(s.storage_capacity_volume)))
            for v in pd.read_csv(sd / "suppliers.csv.gz").itertuples(index=False):
                c.execute("""INSERT INTO dim_supplier(supplier_id,name,version,valid_from,valid_to,is_current,
                               mean_lead_days,lead_sd_days,on_time_prob,weekly_capacity_units)
                             VALUES (?,?,?,?,?,?,?,?,?,?)
                             ON CONFLICT(supplier_id,version) DO UPDATE SET valid_from=excluded.valid_from,
                               valid_to=excluded.valid_to,is_current=excluded.is_current,
                               mean_lead_days=excluded.mean_lead_days,lead_sd_days=excluded.lead_sd_days,
                               on_time_prob=excluded.on_time_prob,weekly_capacity_units=excluded.weekly_capacity_units""",
                          (v.supplier_id, v.name, int(v.version), v.valid_from, v.valid_to, int(v.is_current),
                           float(v.mean_lead_days), float(v.lead_sd_days), float(v.on_time_prob),
                           float(v.weekly_capacity_units)))
            for p in pd.read_csv(sd / "product_attrs.csv.gz").itertuples(index=False):
                c.execute("""INSERT INTO dim_product(item_id,dept_id,cat_id,supplier_id,unit_cost,ref_price,pack_size,
                               moq_units,unit_volume) VALUES (?,?,?,?,?,?,?,?,?)
                             ON CONFLICT(item_id) DO UPDATE SET dept_id=excluded.dept_id,cat_id=excluded.cat_id,
                               supplier_id=excluded.supplier_id,unit_cost=excluded.unit_cost,ref_price=excluded.ref_price,
                               pack_size=excluded.pack_size,moq_units=excluded.moq_units,unit_volume=excluded.unit_volume""",
                          (p.item_id, p.dept_id, p.cat_id, p.supplier_id, float(p.unit_cost), float(p.ref_price),
                           int(p.pack_size), int(p.moq_units), float(p.unit_volume)))
        n = c.execute("SELECT (SELECT COUNT(*) FROM dim_date)+(SELECT COUNT(*) FROM dim_product)+"
                      "(SELECT COUNT(*) FROM dim_store)+(SELECT COUNT(*) FROM dim_supplier)+"
                      "(SELECT COUNT(*) FROM dim_warehouse)").fetchone()[0]
        return dict(rows_in=n, rows_out=n)

    sm = json.loads((ctx.settings.silver_dir / "supply" / "_manifest.json").read_text())["supply"]["sha256"]
    run_batch(ctx, "gold_dims", "all", sm + "|" + str(len(cal)), work)
    ctx.lineage("gold.dim_*", ["silver/calendar", "silver/supply/*"], "gold_dims")


def gold_sales(ctx: Ctx) -> None:
    c = ctx.conn
    manifest = json.loads((ctx.settings.silver_dir / "sales" / "_manifest.json").read_text())
    pk = {r["item_id"]: r["product_key"] for r in c.execute("SELECT item_id, product_key FROM dim_product")}
    sk = {r["store_id"]: r["store_key"] for r in c.execute("SELECT store_id, store_key FROM dim_store")}
    for ym in sorted(manifest):
        h = manifest[ym]["sha256"] + f":{manifest[ym]['rows']}"

        def work(ym=ym):
            df = pd.read_csv(ctx.settings.silver_dir / "sales" / f"ym={ym}.csv.gz")
            dk = df["date"].str.replace("-", "").astype(int)
            price = df["sell_price"]
            rows = list(zip(dk.tolist(), df.item_id.map(pk).tolist(), df.store_id.map(sk).tolist(),
                            df.units.tolist(), [None if pd.isna(p) else float(p) for p in price],
                            [None if pd.isna(p) else round(float(p) * u, 2) for p, u in zip(price, df.units)],
                            df.is_outlier.tolist()))
            lo, hi = int(dk.min()), int(dk.max())
            with transaction(c):  # delete-then-insert in ONE transaction => idempotent and atomic
                c.execute("DELETE FROM fact_sales WHERE date_key BETWEEN ? AND ?", (lo, hi))
                c.executemany("INSERT INTO fact_sales(date_key,product_key,store_key,units_sold,sell_price,"
                              "sales_amount,is_outlier) VALUES (?,?,?,?,?,?,?)", rows)
            return dict(rows_in=len(df), rows_out=len(rows))

        run_batch(ctx, "gold_fact_sales", ym, h, work)
    ctx.lineage("gold.fact_sales", ["silver/sales/*", "gold.dim_*"], "gold_fact_sales")


def gold_supply(ctx: Ctx) -> None:
    c, sd = ctx.conn, ctx.settings.silver_dir / "supply"
    h = json.loads((sd / "_manifest.json").read_text())["supply"]["sha256"]

    def work():
        pk = {r["item_id"]: r["product_key"] for r in c.execute("SELECT item_id, product_key FROM dim_product")}
        sk = {r["store_id"]: (r["store_key"], r["warehouse_key"]) for r in
              c.execute("SELECT store_id, store_key, warehouse_key FROM dim_store")}
        spk = {(r["supplier_id"], r["version"]): r["supplier_key"] for r in
               c.execute("SELECT supplier_id, version, supplier_key FROM dim_supplier")}
        dkey = {r["d_idx"]: r["date_key"] for r in c.execute("SELECT d_idx, date_key FROM dim_date")}
        inv = pd.read_csv(sd / "inventory_daily.csv.gz")
        po = pd.read_csv(sd / "purchase_orders.csv.gz")
        inv_rows = list(zip(inv.d_idx.map(dkey).tolist(), inv.item_id.map(pk).tolist(),
                            inv.store_id.map(lambda s: sk[s][0]).tolist(), inv.on_hand_end.tolist(),
                            inv.on_order_end.tolist(), inv.units_received.tolist(), inv.units_demanded.tolist(),
                            inv.units_fulfilled.tolist(), inv.lost_sales_units.tolist(),
                            (inv.lost_sales_units > 0).astype(int).tolist()))
        po_rows, repl_rows = [], []
        for r in po.itertuples(index=False):
            opened = bool(r.is_open)
            received_key = None if opened else dkey[int(r.arrival_day)]
            status = "open" if opened else ("received" if r.qty_received >= r.qty_ordered else "partial")
            pkey, (skey, wkey) = pk[r.item_id], sk[r.store_id]
            promised_key = dkey.get(int(r.order_day + r.promised_lead))
            po_rows.append((r.po_id, pkey, skey, spk[(r.supplier_id, int(r.supplier_version))], wkey,
                            dkey[int(r.order_day)], promised_key, received_key, float(r.qty_ordered),
                            None if opened else float(r.qty_received), float(r.unit_cost), status,
                            int(r.promised_lead), None if opened else int(r.actual_lead),
                            None if opened else int(r.delay_days),
                            None if opened else int(r.delay_days >= LATE_THRESHOLD_DAYS)))
            repl_rows.append((dkey[int(r.order_day)], pkey, skey, "baseline", float(r.inv_position_pre),
                              float(r.qty_ordered), r.po_id))
        with transaction(c):
            for t in ("fact_replenishment", "fact_purchase_orders", "fact_inventory"):
                c.execute(f"DELETE FROM {t}")  # noqa: S608 - table names are constants
            c.executemany("INSERT INTO fact_inventory VALUES (?,?,?,?,?,?,?,?,?,?)", inv_rows)
            c.executemany("INSERT INTO fact_purchase_orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", po_rows)
            c.executemany("INSERT INTO fact_replenishment(date_key,product_key,store_key,policy,inventory_position,"
                          "qty_ordered,po_id) VALUES (?,?,?,?,?,?,?)", repl_rows)
        return dict(rows_in=len(inv) + len(po), rows_out=len(inv_rows) + len(po_rows) + len(repl_rows))

    run_batch(ctx, "gold_supply_facts", "all", h, work)
    ctx.lineage("gold.fact_inventory|fact_purchase_orders|fact_replenishment", ["silver/supply/*"], "gold_supply_facts")


def gold_quality(ctx: Ctx) -> None:
    """SQL-level checks on the loaded warehouse (referential integrity, negative inventory, date consistency)."""
    c = ctx.conn
    q = lambda sql: c.execute(sql).fetchone()[0]  # noqa: E731
    res = []
    fk = c.execute("PRAGMA foreign_key_check").fetchall()
    res.append(dq.DQResult("gold", "warehouse", "foreign_key_check", len(fk) == 0, "error", 0, len(fk)))
    neg = q("SELECT COUNT(*) FROM fact_inventory WHERE on_hand_end < 0")
    res.append(dq.DQResult("gold", "fact_inventory", "no_negative_inventory", neg == 0, "error", 0, neg))
    bad_po = q("SELECT COUNT(*) FROM fact_purchase_orders WHERE received_date_key IS NOT NULL AND "
               "received_date_key < order_date_key")
    res.append(dq.DQResult("gold", "fact_purchase_orders", "received_after_ordered", bad_po == 0, "error", 0, bad_po))
    grain = q("SELECT COUNT(*) FROM (SELECT date_key,product_key,store_key FROM fact_sales GROUP BY 1,2,3 HAVING COUNT(*)>1)")
    res.append(dq.DQResult("gold", "fact_sales", "grain_unique", grain == 0, "error", 0, grain))
    ctx.record_dq(res)
    ctx.m("gold_quality")["dq_failures"] = sum(0 if r.passed else 1 for r in res)
    if not all(r.passed for r in res):
        raise DataQualityError(f"gold checks failed: {[r.check for r in res if not r.passed]}")
