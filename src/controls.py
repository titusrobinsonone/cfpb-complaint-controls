"""Run the control tests on the cleaned complaint data and write the
exceptions log.

Thresholds marked SIMULATED are my own choice for this exercise. They are
not regulatory limits and not any bank's internal policy. The data is real.

Usage:
    python src/controls.py out/
"""
import json
import sys
from pathlib import Path

import pandas as pd

# All thresholds live here so the docs and the code can't drift apart.
T = {
    "CTL-01": 0.98,      # SIMULATED: min timely response rate per bank-month
    "CTL-01_min_n": 30,  # SIMULATED: skip bank-months with fewer closed complaints
    "CTL-02": 60,        # CFPB expects most complaints closed within 60 days
    "CTL-03_days": 14,   # SIMULATED: a complaint is "late to the company" after this many days
    "CTL-03": 0.05,      # SIMULATED: max share of late complaints per bank-month
    "MON-01": 3.0,       # SIMULATED: spike = above trailing 6-month mean + 3 sd
    "MON-01_min_mean": 10,  # SIMULATED: skip series averaging fewer than 10 a month
}

CONTROLS = [
    # id, name, threshold shown in the report, simulated?, what it checks
    ("CTL-01", "Timely response rate by bank and month", ">= 98%", True,
     "Share of closed complaints with Timely response = Yes. Bank-months with fewer than 30 closed complaints are skipped."),
    ("CTL-02", "Complaints still in progress after 60 days", "> 60 days open", False,
     "Age of each 'In progress' complaint, counted to the extract date. The 60 days comes from CFPB guidance."),
    ("CTL-03", "Routing lag from CFPB to bank", "<= 5% routed after 14 days", True,
     "Days between Date received and Date sent to company, checked per bank and month."),
    ("DQ-01", "Duplicate complaint IDs", "0", False, "Duplicate IDs among in-scope rows."),
    ("DQ-02", "Sent to bank before it was received", "0", False, "Date sent earlier than date received."),
    ("DQ-03", "Missing or non-US state code", "info only", False,
     "Counted, not failed. Some consumers live abroad or leave the state blank."),
    ("DQ-04", "Closed complaint with no timeliness flag", "0", False, "Every closed complaint should have Timely response filled."),
    ("MON-01", "Monthly volume spike by bank and product", "mean + 3 sd", True,
     "Monthly count above the trailing 6-month mean plus 3 standard deviations. Series averaging under 10 a month are skipped."),
]

OPEN = "open"  # every exception starts open; the workflow is simulated


def top(s):
    """Most common value and its count, as text."""
    if s.dropna().empty:
        return "n/a"
    vc = s.value_counts()
    return f"{vc.index[0]} ({vc.iloc[0]} of {int(vc.sum())})"


def ctl01(df):
    closed = df[df["is_closed"] & df["is_timely"].notna()]
    g = closed.groupby(["company_short", "month"])["is_timely"].agg(["size", "mean"]).reset_index()
    tested = g[g["size"] >= T["CTL-01_min_n"]]
    rows = []
    for r in tested[tested["mean"] < T["CTL-01"]].itertuples():
        late = closed[(closed.company_short == r.company_short) & (closed.month == r.month) & (closed.is_timely == 0)]
        rows.append(dict(scope=r.company_short, period=f"{r.month:%Y-%m}", observed=round(float(r.mean), 4),
                         detail=f"{int(r.size)} closed, {len(late)} not timely",
                         root_cause=f"Most untimely complaints were {top(late['product'])}",
                         remediation_action="Review response workflow for this product and month"))
    return len(tested), rows


def ctl02(df, as_of):
    open_ = df[~df["is_closed"]].copy()
    open_["age"] = (as_of - open_["date_received"]).dt.days
    rows = [dict(scope=r.company_short, period=f"{r.date_received:%Y-%m}", observed=int(r.age),
                 detail=f"complaint {r.complaint_id}, {r.product}",
                 root_cause=f"Open {int(r.age)} days on issue: {r.issue}",
                 remediation_action="Confirm status with complaint owner and close or escalate")
            for r in open_[open_["age"] > T["CTL-02"]].itertuples()]
    return len(open_), rows


def ctl03(df):
    s = df[df["days_to_company"].notna()].copy()
    s["late"] = s["days_to_company"] > T["CTL-03_days"]
    g = s.groupby(["company_short", "month"])["late"].agg(["size", "sum", "mean"]).reset_index()
    rows = []
    for r in g[g["mean"] > T["CTL-03"]].itertuples():
        late = s[(s.company_short == r.company_short) & (s.month == r.month) & s.late]
        rows.append(dict(scope=r.company_short, period=f"{r.month:%Y-%m}", observed=round(float(r.mean), 4),
                         detail=f"{int(r.sum)} of {int(r.size)} routed after {T['CTL-03_days']} days",
                         root_cause=f"Median lag {late['days_to_company'].median():.0f} days; top product {top(late['product'])}",
                         remediation_action="Check intake path for this channel with CFPB portal team"))
    return len(g), rows


def dq(df, dupes):
    out = {}
    out["DQ-01"] = (len(df) + dupes, [dict(scope="in-scope rows", period="all", observed=dupes,
                                           detail="duplicate IDs removed in cleaning",
                                           root_cause="Same complaint ID repeated in the source file",
                                           remediation_action="Report to data owner")] if dupes else [])
    neg = df[df["days_to_company"] < 0]
    out["DQ-02"] = (int(df["days_to_company"].notna().sum()), [
        dict(scope=r.company_short, period=f"{r.date_received:%Y-%m}", observed=int(r.days_to_company),
             detail=f"complaint {r.complaint_id}", root_cause="Date sent earlier than date received",
             remediation_action="Report to data owner") for r in neg.itertuples()])
    bad = df[~df["state_valid"]]
    out["DQ-03"] = (len(df), [dict(scope="all banks", period="all", observed=len(bad),
                                   detail=f"blank: {int(bad['state'].isna().sum())}, other: {int(bad['state'].notna().sum())}",
                                   root_cause="State left blank by consumer or outside the US",
                                   remediation_action="None needed, shown as (blank) in the dashboard")] if len(bad) else [])
    closed = df[df["is_closed"]]
    miss = closed[closed["is_timely"].isna()]
    out["DQ-04"] = (len(closed), [dict(scope=r.company_short, period=f"{r.date_received:%Y-%m}", observed=1,
                                       detail=f"complaint {r.complaint_id}", root_cause="Timely flag blank on a closed complaint",
                                       remediation_action="Report to data owner") for r in miss.itertuples()])
    return out


def mon01(df, baseline=None):
    g = df.groupby(["company_short", "product", "month"]).size().rename("n").reset_index()
    first = g["month"].min()
    if baseline is not None and len(baseline):
        g = pd.concat([baseline, g], ignore_index=True)
    rows, tested = [], 0
    for (c, p), part in g.groupby(["company_short", "product"]):
        part = part.groupby("month")["n"].sum().asfreq("MS", fill_value=0)
        hist = part.shift(1).rolling(6, min_periods=6)
        mean, sd = hist.mean(), hist.std()
        ok = mean.notna() & (mean >= T["MON-01_min_mean"]) & (part.index >= first)
        tested += int(ok.sum())
        for m, n in part[ok & (part > mean + T["MON-01"] * sd)].items():
            month = df[(df.company_short == c) & (df["product"] == p) & (df.month == m)]
            rows.append(dict(scope=f"{c} / {p}", period=f"{m:%Y-%m}", observed=int(n),
                             detail=f"trailing mean {mean[m]:.0f}, sd {sd[m]:.0f}",
                             root_cause=f"Biggest issue that month: {top(month['issue'])}",
                             remediation_action="Check for a product, system or policy event behind the spike"))
    return tested, rows


def run(out):
    out = Path(out)
    df = pd.read_parquet(out / "clean.parquet")
    df["is_closed"] = df["is_closed"].astype(bool)
    log = pd.read_csv(out / "cleaning_log.csv").set_index("step")["rows"]
    dupes = int(log.get("duplicate complaint IDs (dropped, first kept)", 0))
    info = json.loads((out / "run_info.json").read_text())
    as_of = pd.Timestamp(info["as_of"])
    base_path = out / "baseline_monthly.csv"
    baseline = pd.read_csv(base_path, parse_dates=["month"]) if base_path.exists() else None

    found = {"CTL-01": ctl01(df), "CTL-02": ctl02(df, as_of), "CTL-03": ctl03(df),
             "MON-01": mon01(df, baseline), **dq(df, dupes)}

    results, exceptions = [], []
    for cid, name, thr, simulated, desc in CONTROLS:
        population, ex = found[cid]
        status = "info" if cid == "DQ-03" else ("pass" if not ex else "exceptions")
        results.append(dict(control_id=cid, control=name, threshold=thr,
                            threshold_type="SIMULATED" if simulated else "fixed",
                            population=population, exceptions=len(ex), result=status, description=desc))
        if cid == "DQ-03":
            continue  # counted for information, not logged as an exception
        for e in ex:
            exceptions.append(dict(control_id=cid, threshold=thr,
                                   threshold_type="SIMULATED" if simulated else "fixed",
                                   remediation_status=OPEN, **e))

    cols = ["exception_id", "control_id", "scope", "period", "observed", "threshold", "threshold_type",
            "detail", "root_cause", "remediation_action", "remediation_status"]
    exc = pd.DataFrame(exceptions)
    if exc.empty:
        exc = pd.DataFrame(columns=cols)
    else:
        exc.insert(0, "exception_id", [f"EX-{i:05d}" for i in range(1, len(exc) + 1)])
        exc = exc[cols]
    res = pd.DataFrame(results)
    res.to_csv(out / "control_results.csv", index=False)
    exc.to_csv(out / "exceptions.csv", index=False)
    print(res[["control_id", "population", "exceptions", "result"]].to_string(index=False))
    print(f"as of {as_of.date()} (latest received date in the source file)")
    return res, exc


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "out")
