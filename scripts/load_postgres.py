"""Load the built SQLite warehouse into a PostgreSQL 16 database (validation of sql/postgres/001_schema.sql).
Usage: python scripts/load_postgres.py --host /tmp --port 54329 --user postgres [--db postgres]
Requires the `psql` client. Prints row counts and a few benchmark queries (median of 7 runs)."""
import argparse
import csv
import io
import sqlite3
import statistics
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORDER = ["dim_date", "dim_warehouse", "dim_store", "dim_supplier", "dim_product", "fact_sales", "fact_inventory",
         "fact_purchase_orders", "fact_replenishment"]
BOOL = {"dim_date": {"is_weekend", "snap_ca", "snap_tx", "snap_wi"}, "dim_warehouse": {"is_simulated"}, "dim_supplier": {"is_current", "is_simulated"},
        "fact_sales": {"is_outlier"}, "fact_inventory": {"stockout_flag"}, "fact_purchase_orders": {"is_late"}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="localhost"); ap.add_argument("--port", default="5432")
    ap.add_argument("--user", default="postgres"); ap.add_argument("--db", default="postgres")
    ap.add_argument("--sqlite", default=str(ROOT / "data" / "orbit.db"))
    a = ap.parse_args()
    psql = ["psql", "-h", a.host, "-p", a.port, "-U", a.user, "-d", a.db, "-v", "ON_ERROR_STOP=1", "-q"]
    src = sqlite3.connect(a.sqlite)
    for t in ORDER:
        cur = src.execute(f"SELECT * FROM {t}")  # noqa: S608
        cols = [d[0] for d in cur.description]
        buf = io.StringIO(); w = csv.writer(buf)
        for r in cur:
            w.writerow(["" if v is None else v for v in r])
        # boolean columns arrive as 0/1, which PostgreSQL accepts for boolean in COPY
        p = subprocess.run(psql + ["-c", f"COPY orbit.{t} ({','.join(cols)}) FROM STDIN WITH (FORMAT csv, NULL '')"],
                           input=buf.getvalue(), text=True, capture_output=True)
        if p.returncode:
            raise SystemExit(f"load of {t} failed: {p.stderr[:400]}")
        n = subprocess.run(psql + ["-Atc", f"SELECT COUNT(*) FROM orbit.{t}"], capture_output=True, text=True).stdout.strip()  # noqa: S608
        print(f"{t:22s} sqlite={src.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:>8} postgres={n:>8}")  # noqa: S608
    q = {"monthly_sales_by_category": "SELECT d.year, d.month, p.cat_id, SUM(f.units_sold) FROM orbit.fact_sales f JOIN orbit.dim_product p USING(product_key) JOIN orbit.dim_date d USING(date_key) GROUP BY 1,2,3",
         "series_history": "SELECT date_key, units_sold FROM orbit.fact_sales WHERE product_key=1 AND store_key=1 ORDER BY date_key",
         "late_rate_by_supplier": "SELECT su.supplier_id, AVG(is_late::int) FROM orbit.fact_purchase_orders po JOIN orbit.dim_supplier su USING(supplier_key) GROUP BY 1"}
    for name, sql in q.items():
        ts = []
        for _ in range(7):
            t = time.perf_counter(); subprocess.run(psql + ["-Atc", sql], capture_output=True, check=True); ts.append((time.perf_counter() - t) * 1000)
        print(f"{name:28s} median {statistics.median(ts):6.1f} ms (includes psql process start-up)")


if __name__ == "__main__":
    main()
