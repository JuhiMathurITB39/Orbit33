import numpy as np
import pandas as pd

from orbit.pipeline import quality as q


def test_schema_detects_missing_and_wrong_type():
    df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    assert q.check_schema(df, {"a": "int", "b": "str"}, "silver", "t").passed
    assert not q.check_schema(df, {"a": "int", "c": "int"}, "silver", "t").passed


def test_nulls_and_duplicates_detected():
    df = pd.DataFrame({"k": [1, 1, 2], "v": [1.0, None, 3.0]})
    assert not q.check_nulls(df, ["v"], "s", "t").passed
    assert not q.check_unique(df, ["k"], "s", "t").passed
    assert q.check_unique(df.drop_duplicates("k"), ["k"], "s", "t").passed


def test_negative_and_fractional_quantities_detected():
    assert not q.check_non_negative(pd.DataFrame({"u": [1, -1]}), "u", "s", "t").passed
    assert not q.check_integer_quantity(pd.DataFrame({"u": [1.0, 2.5]}), "u", "s", "t").passed
    assert q.check_integer_quantity(pd.DataFrame({"u": [1.0, 2.0]}), "u", "s", "t").passed


def test_referential_and_date_gaps():
    assert not q.check_referential(pd.Series([1, 2, 9]), pd.Series([1, 2, 3]), "g", "t", "fk").passed
    d = pd.Series(pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-04"]))
    assert not q.check_dates_contiguous(d, "s", "t").passed


def test_outlier_flagging_is_conservative():
    u = pd.Series([5, 6, 4, 5, 7, 6, 5, 500])
    f = q.flag_outliers(u)
    assert f.iloc[-1] and f.iloc[:-1].sum() == 0


def test_negative_inventory_check():
    assert not q.check_no_negative_inventory(np.array([[1.0, -1.0]]), "g", "t").passed
