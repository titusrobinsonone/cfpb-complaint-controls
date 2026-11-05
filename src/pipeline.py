"""Load the CFPB complaint file, keep the six banks and the review window,
clean it, and build a small star schema for Power BI.

Usage:
    python src/pipeline.py ../data/complaints.csv out/

The bulk download is the whole database (about 19 million rows, 5.7 GB),
so the first cut is done in DuckDB SQL straight off the CSV. Only the
in-scope rows come into pandas. Every row that is dropped or excluded is
counted in out/cleaning_log.csv, and the log has to add up or the run stops.
"""
import json
import sys
from pathlib import Path

import duckdb
import pandas as pd

WINDOW = ("2024-11-01", "2026-10-31")

# Column names as they appear in the CFPB file. The bulk download leaves out
# the narrative, consent and disputed columns, the search-page export has them.
REQUIRED = {
    "Date received": "date_received",
    "Product": "product",
    "Sub-product": "sub_product",
    "Issue": "issue",
    "Sub-issue": "sub_issue",
    "Company public response": "company_public_response",
    "Company": "company",
    "State": "state",
    "ZIP code": "zip_code",
    "Tags": "tags",
    "Submitted via": "submitted_via",
    "Date sent to company": "date_sent",
    "Company response to consumer": "company_response",
    "Timely response?": "timely",
    "Complaint ID": "complaint_id",
}
OPTIONAL = {
    "Consumer consent provided?": "consent",
    "Consumer disputed?": "disputed",
}

# The six banks in scope, with short names for charts.
COMPANY_SHORT = {
    "BANK OF AMERICA, NATIONAL ASSOCIATION": "Bank of America",
    "WELLS FARGO & COMPANY": "Wells Fargo",
    "TRUIST FINANCIAL CORPORATION": "Truist",
    "JPMORGAN CHASE & CO.": "JPMorgan Chase",
    "CAPITAL ONE FINANCIAL CORPORATION": "Capital One",
    "CITIBANK, N.A.": "Citibank",
}

US_STATES = set("""AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY
NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR GU VI AS MP AE AP AA FM MH PW UM""".split())

# Log steps. The reconcile check in clean() depends on these names.
READ = "rows in source file"
OTHER_CO = "rows for other companies (excluded, out of scope)"
BAD_DATE = "rows with unreadable received date (dropped)"
OUTSIDE = "rows received outside the review window (excluded)"
DUPES = "duplicate complaint IDs (dropped, first kept)"
FINAL = "rows after cleaning"


def read_raw(path):
    """Return (in-scope rows as strings, rows in file, latest received date in file)."""
    path = Path(path).as_posix()
    con = duckdb.connect()
    con.execute(f"create view raw as select * from read_csv('{path}', all_varchar=true, header=true)")
    cols = [r[0] for r in con.execute("select column_name from (describe raw)").fetchall()]
    missing = [c for c in REQUIRED if c not in cols]
    if missing:
        raise ValueError(f"file is missing columns: {missing}")

    keep = list(REQUIRED) + [c for c in OPTIONAL if c in cols]
    select = ", ".join(f'"{c}"' for c in keep)
    banks = ", ".join("'" + c.replace("'", "''") + "'" for c in COMPANY_SHORT)
    df = con.execute(f"select {select} from raw where trim(Company) in ({banks})").df()
    n_file, latest = con.execute(
        "select count(*), max(try_strptime(\"Date received\", '%Y-%m-%d'))::date from raw").fetchone()

    df = df.rename(columns={**REQUIRED, **OPTIONAL})
    for c in OPTIONAL.values():
        if c not in df.columns:
            df[c] = None
    return df, int(n_file), pd.Timestamp(latest)


def parse_dates(s):
    # ISO first (bulk file), then the US styles the search-page export uses.
    out = pd.to_datetime(s, format="%Y-%m-%d", errors="coerce")
    for fmt in ("%m/%d/%y", "%m/%d/%Y"):
        retry = out.isna() & s.notna()
        if retry.any():
            out[retry] = pd.to_datetime(s[retry], format=fmt, errors="coerce")
    return out


def clean(df, n_file, window=WINDOW):
    log = [(READ, n_file, "count")]
    log.append((OTHER_CO, n_file - len(df), "excluded"))

    df = df.copy()
    for c in df.columns:
        df[c] = df[c].astype("string").str.strip()
        df.loc[df[c] == "", c] = pd.NA

    df["date_received"] = parse_dates(df["date_received"])
    df["date_sent"] = parse_dates(df["date_sent"])
    bad = df["date_received"].isna()
    log.append((BAD_DATE, int(bad.sum()), "dropped"))
    df = df[~bad]

    start, end = pd.Timestamp(window[0]), pd.Timestamp(window[1])
    # Keep monthly counts for the 6 months before the window so the spike
    # monitor (MON-01) has a baseline from the first month of the window.
    pre = df[(df["date_received"] >= start - pd.DateOffset(months=6)) & (df["date_received"] < start)]
    baseline = (pre.assign(company_short=pre["company"].map(COMPANY_SHORT),
                           month=pre["date_received"].dt.to_period("M").dt.to_timestamp())
                .groupby(["company_short", "product", "month"]).size().rename("n").reset_index())

    outside = (df["date_received"] < start) | (df["date_received"] > end)
    log.append((OUTSIDE, int(outside.sum()), "excluded"))
    df = df[~outside]

    dup = df.duplicated("complaint_id", keep="first")
    log.append((DUPES, int(dup.sum()), "dropped"))
    df = df[~dup].copy()

    df["company_short"] = df["company"].map(COMPANY_SHORT)
    # A few rows spell out the territory name instead of the 2-letter code.
    spelled = df["state"].eq("UNITED STATES MINOR OUTLYING ISLANDS")
    df.loc[spelled, "state"] = "UM"
    log.append(("state names spelled out, recoded to 2-letter code (kept, fixed)", int(spelled.sum()), "fixed"))
    df["state_valid"] = df["state"].isin(US_STATES)
    log.append(("rows with missing or non-US state code (kept, flagged)", int((~df["state_valid"]).sum()), "flagged"))
    log.append(("rows with no date sent to company (kept, flagged)", int(df["date_sent"].isna().sum()), "flagged"))

    df["days_to_company"] = (df["date_sent"] - df["date_received"]).dt.days
    log.append(("rows sent to company before they were received (kept, flagged)",
                int((df["days_to_company"] < 0).sum()), "flagged"))

    df["is_timely"] = df["timely"].map({"Yes": 1, "No": 0}).astype("Float64")
    df["is_closed"] = df["company_response"].ne("In progress").fillna(True)
    df["month"] = df["date_received"].dt.to_period("M").dt.to_timestamp()
    log.append((FINAL, len(df), "count"))

    log = pd.DataFrame(log, columns=["step", "rows", "action"])
    removed = log.loc[log["action"].isin(["excluded", "dropped"]), "rows"].sum()
    if n_file - removed != len(df):
        raise RuntimeError(f"cleaning log does not reconcile: {n_file} - {removed} != {len(df)}")
    return df.reset_index(drop=True), log, baseline


def star_schema(df):
    """Fact table plus small dimensions with integer keys, so the Power BI
    model stays light. Blank text is shown as '(blank)' in the dims."""
    df = df.copy()
    text = ["company", "company_short", "product", "sub_product", "issue", "sub_issue",
            "submitted_via", "company_response", "timely", "state"]
    for c in text:
        df[c] = df[c].fillna("(blank)")

    dims = {}

    def dim(name, cols):
        d = df[cols].drop_duplicates().sort_values(cols).reset_index(drop=True)
        d.insert(0, f"{name}_key", range(1, len(d) + 1))
        dims[name] = d

    dim("company", ["company", "company_short"])
    dim("product", ["product", "sub_product"])
    dim("issue", ["issue", "sub_issue"])
    dim("channel", ["submitted_via"])
    dim("response", ["company_response", "timely"])
    dim("state", ["state", "state_valid"])

    days = pd.date_range(df["date_received"].min(), df["date_received"].max(), freq="D")
    dims["date"] = pd.DataFrame({
        "date_key": days.strftime("%Y%m%d").astype(int),
        "date": days.date,
        "year": days.year,
        "quarter": "Q" + days.quarter.astype(str),
        "month": days.month,
        "month_name": days.strftime("%b"),
        "year_month": days.strftime("%Y-%m"),
        "month_start": days.to_period("M").to_timestamp().date,
    })

    fact = df
    for name in ["company", "product", "issue", "channel", "response", "state"]:
        d = dims[name]
        fact = fact.merge(d, on=[c for c in d.columns if c != f"{name}_key"], how="left")
    fact["date_key"] = fact["date_received"].dt.strftime("%Y%m%d").astype(int)
    fact["is_closed"] = fact["is_closed"].astype(int)
    fact = fact[["complaint_id", "date_key", "company_key", "product_key", "issue_key", "channel_key",
                 "response_key", "state_key", "days_to_company", "is_timely", "is_closed",
                 "date_received", "date_sent"]]
    fact["date_received"] = fact["date_received"].dt.date
    fact["date_sent"] = fact["date_sent"].dt.date
    return fact, dims


def main(src, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    raw, n_file, latest = read_raw(src)
    df, log, baseline = clean(raw, n_file)
    baseline.to_csv(out / "baseline_monthly.csv", index=False)
    fact, dims = star_schema(df)
    fact.to_csv(out / "fact_complaint.csv", index=False)
    for name, d in dims.items():
        d.to_csv(out / f"dim_{name}.csv", index=False)
    log.to_csv(out / "cleaning_log.csv", index=False)
    df.to_parquet(out / "clean.parquet", index=False)
    # The latest received date anywhere in the file stands in for the extract date.
    info = {"source": Path(src).name, "window_start": WINDOW[0], "window_end": WINDOW[1],
            "as_of": str(latest.date())}
    (out / "run_info.json").write_text(json.dumps(info, indent=2))
    pd.DataFrame({"as_of": [info["as_of"]]}).to_csv(out / "param_as_of.csv", index=False)
    print(log.to_string(index=False))
    return df


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "out")
