"""Each test here matches a row in docs/test-cases.md."""
import pandas as pd
import pytest

import controls
import pipeline


@pytest.fixture
def out(raw_csv, tmp_path):
    o = tmp_path / "out"
    pipeline.main(raw_csv, o)
    return o


def logrows(out):
    return pd.read_csv(out / "cleaning_log.csv").set_index("step")["rows"]


def test_tc01_unreadable_dates_are_dropped_and_logged(out):
    assert logrows(out)[pipeline.BAD_DATE] == 1


def test_tc02_both_date_formats_parse(out):
    fact = pd.read_csv(out / "fact_complaint.csv")
    assert 20250221 in set(fact["date_key"])


def test_tc03_duplicates_removed_once(out):
    fact = pd.read_csv(out / "fact_complaint.csv")
    assert fact["complaint_id"].is_unique
    assert logrows(out)[pipeline.DUPES] == 1


def test_tc04_row_counts_reconcile(out):
    log = logrows(out)
    removed = log[pipeline.OTHER_CO] + log[pipeline.BAD_DATE] + log[pipeline.OUTSIDE] + log[pipeline.DUPES]
    assert log[pipeline.READ] - removed == log[pipeline.FINAL]
    assert len(pd.read_csv(out / "fact_complaint.csv")) == log[pipeline.FINAL]


def test_tc05_star_schema_keys_join(out):
    fact = pd.read_csv(out / "fact_complaint.csv")
    for d in ["company", "product", "issue", "channel", "response", "state"]:
        dim = pd.read_csv(out / f"dim_{d}.csv")
        assert dim[f"{d}_key"].is_unique
        assert fact[f"{d}_key"].isin(dim[f"{d}_key"]).all(), d
    dates = pd.read_csv(out / "dim_date.csv")
    assert fact["date_key"].isin(dates["date_key"]).all()


def test_tc06_ctl01_flags_month_below_threshold(out):
    res, exc = controls.run(out)
    e = exc[exc.control_id == "CTL-01"]
    assert list(e["period"]) == ["2025-02"]
    assert (e["threshold_type"] == "SIMULATED").all()


def test_tc07_ctl02_old_open_complaint(out):
    res, exc = controls.run(out)
    e = exc[exc.control_id == "CTL-02"]
    assert len(e) == 1 and "2003" in e.iloc[0]["detail"]
    assert e.iloc[0]["observed"] == 89


def test_tc08_ctl03_routing_lag(out):
    res, exc = controls.run(out)
    e = exc[exc.control_id == "CTL-03"]
    assert len(e) == 1
    assert e.iloc[0]["scope"] == "Wells Fargo" and e.iloc[0]["period"] == "2025-03"
    assert e.iloc[0]["observed"] == 1.0


def test_tc09_dq02_sent_before_received(out):
    res, exc = controls.run(out)
    assert "2001" in exc[exc.control_id == "DQ-02"].iloc[0]["detail"]


def test_tc10_dq03_counted_not_failed(out):
    res, exc = controls.run(out)
    r = res.set_index("control_id").loc["DQ-03"]
    assert r["result"] == "info" and r["exceptions"] == 1
    assert "DQ-03" not in set(exc["control_id"])


def test_tc11_dq04_closed_without_flag(out):
    res, exc = controls.run(out)
    assert "2006" in exc[exc.control_id == "DQ-04"].iloc[0]["detail"]


def test_tc12_every_exception_has_id_root_cause_and_open_status(out):
    res, exc = controls.run(out)
    assert exc["exception_id"].is_unique
    assert exc["root_cause"].notna().all()
    assert (exc["remediation_status"] == "open").all()


def test_tc13_other_companies_and_out_of_window_rows_excluded(out):
    log = logrows(out)
    assert log[pipeline.OTHER_CO] == 1
    assert log[pipeline.OUTSIDE] == 1
    fact = pd.read_csv(out / "fact_complaint.csv")
    assert not {2009, 2010} & set(fact["complaint_id"])


def test_tc14_bulk_file_layout_loads(bulk_csv, tmp_path):
    df = pipeline.main(bulk_csv, tmp_path / "bulk_out")
    assert len(df) == logrows(tmp_path / "bulk_out")[pipeline.FINAL]
    assert df["consent"].isna().all()


def test_tc15_spelled_out_territory_recoded(out):
    states = pd.read_csv(out / "dim_state.csv")
    assert "UNITED STATES MINOR OUTLYING ISLANDS" not in set(states["state"])
    assert states.set_index("state").loc["UM", "state_valid"]


def test_tc16_mon01_flags_spike_and_uses_baseline():
    months = pd.date_range("2025-01-01", periods=3, freq="MS")
    df = pd.DataFrame({"company_short": "Truist", "product": "Mortgage",
                       "month": months.repeat([20, 21, 90]), "issue": "Escrow"})
    base = pd.DataFrame({"company_short": "Truist", "product": "Mortgage",
                         "month": pd.date_range("2024-07-01", periods=6, freq="MS"),
                         "n": [19, 20, 21, 20, 19, 21]})
    tested, rows = controls.mon01(df, base)
    assert tested == 3  # all three in-window months have 6 months of history
    assert [r["period"] for r in rows] == ["2025-03"]
    tested, rows = controls.mon01(df, None)
    assert tested == 0 and rows == []  # no history, nothing to judge
