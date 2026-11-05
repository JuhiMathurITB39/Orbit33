"""Data-quality checks. Pure functions on DataFrames returning DQResult; persisted by the pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class DQResult:
    layer: str
    table: str
    check: str
    passed: bool
    severity: str  # "error" blocks the batch, "warn" is recorded only
    n_checked: int = 0
    n_failed: int = 0
    detail: dict = field(default_factory=dict)

    def detail_json(self) -> str:
        return json.dumps(self.detail, default=str)


def check_schema(df: pd.DataFrame, expected: dict[str, str], layer: str, table: str) -> DQResult:
    """expected maps column -> kind in {'int','float','str','date'}; checks presence and coercibility."""
    missing = [c for c in expected if c not in df.columns]
    bad: dict[str, int] = {}
    for c, kind in expected.items():
        if c not in df.columns:
            continue
        s = df[c]
        if kind in ("int", "float"):
            coerced = pd.to_numeric(s, errors="coerce")
            n_bad = int((coerced.isna() & s.notna()).sum())
            if kind == "int":
                n_bad += int(((coerced % 1) != 0).fillna(False).sum())
        elif kind == "date":
            n_bad = int((pd.to_datetime(s, errors="coerce").isna() & s.notna()).sum())
        else:
            n_bad = 0
        if n_bad:
            bad[c] = n_bad
    ok = not missing and not bad
    return DQResult(layer, table, "schema_and_types", ok, "error", len(df), sum(bad.values()) + len(missing),
                    {"missing_columns": missing, "uncoercible": bad})


def check_nulls(df: pd.DataFrame, required: list[str], layer: str, table: str) -> DQResult:
    counts = {c: int(df[c].isna().sum()) for c in required if c in df.columns}
    n = sum(counts.values())
    return DQResult(layer, table, "required_not_null", n == 0, "error", len(df), n, {"null_counts": counts})


def check_unique(df: pd.DataFrame, keys: list[str], layer: str, table: str) -> DQResult:
    dup = int(df.duplicated(subset=keys, keep=False).sum())
    return DQResult(layer, table, f"unique({','.join(keys)})", dup == 0, "error", len(df), dup, {"keys": keys})


def check_non_negative(df: pd.DataFrame, col: str, layer: str, table: str) -> DQResult:
    vals = pd.to_numeric(df[col], errors="coerce")
    n = int((vals < 0).sum())
    return DQResult(layer, table, f"non_negative({col})", n == 0, "error", len(df), n, {"column": col})


def check_integer_quantity(df: pd.DataFrame, col: str, layer: str, table: str) -> DQResult:
    vals = pd.to_numeric(df[col], errors="coerce")
    n = int(((vals % 1) != 0).fillna(True).sum())
    return DQResult(layer, table, f"integer_quantity({col})", n == 0, "error", len(df), n, {"column": col})


def check_referential(child: pd.Series, parent: pd.Series, layer: str, table: str, name: str) -> DQResult:
    orphans = ~child.isin(set(parent))
    return DQResult(layer, table, f"ref_integrity({name})", not orphans.any(), "error", len(child),
                    int(orphans.sum()), {"example_orphans": child[orphans].drop_duplicates().head(5).tolist()})


def check_dates_contiguous(dates: pd.Series, layer: str, table: str) -> DQResult:
    d = pd.to_datetime(dates).sort_values().reset_index(drop=True)
    gaps = int((d.diff().dropna() != pd.Timedelta(days=1)).sum())
    dup = int(d.duplicated().sum())
    return DQResult(layer, table, "dates_contiguous_unique", gaps == 0 and dup == 0, "error", len(d), gaps + dup,
                    {"gaps": gaps, "duplicates": dup})


def check_date_order(df: pd.DataFrame, earlier: str, later: str, layer: str, table: str) -> DQResult:
    e, l_ = pd.to_numeric(df[earlier], errors="coerce"), pd.to_numeric(df[later], errors="coerce")
    bad = int((l_ < e).sum())
    return DQResult(layer, table, f"date_order({earlier}<={later})", bad == 0, "error", len(df), bad, {})


def check_no_negative_inventory(on_hand: np.ndarray, layer: str, table: str) -> DQResult:
    n = int((on_hand < 0).sum())
    return DQResult(layer, table, "no_negative_inventory", n == 0, "error", int(on_hand.size), n, {})


def flag_outliers(units: pd.Series, k: float = 8.0) -> pd.Series:
    """Robust outlier flag (not removal): value > median + k * 1.4826 * MAD (MAD floor of 1 unit)."""
    med = units.median()
    mad = max((units - med).abs().median() * 1.4826, 1.0)
    return units > med + k * mad


def profile(df: pd.DataFrame) -> dict:
    """Light-weight data profile: row count, per-column null share, numeric summary."""
    out = {"rows": int(len(df)), "columns": {}}
    for c in df.columns:
        s = df[c]
        info = {"dtype": str(s.dtype), "null_share": round(float(s.isna().mean()), 6) if len(s) else 0.0}
        if pd.api.types.is_numeric_dtype(s) and len(s):
            info.update(min=float(s.min()), max=float(s.max()), mean=round(float(s.mean()), 4))
        out["columns"][c] = info
    return out
