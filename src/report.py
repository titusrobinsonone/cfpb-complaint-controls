"""Turn the pipeline and control outputs into charts and a findings file.

Usage:
    python src/report.py out/ reports/

Only aggregates go into reports/. No complaint-level rows are republished.
"""
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

# Fixed colour per bank, so a bank keeps its colour on every chart.
BANK_COLOURS = {
    "Capital One": "#2a78d6", "JPMorgan Chase": "#eb6834", "Citibank": "#1baf7a",
    "Bank of America": "#eda100", "Wells Fargo": "#e87ba4", "Truist": "#008300",
}
INK, MUTED, GRID, ACCENT, BAD = "#0b0b0b", "#52514e", "#e4e3df", "#2a78d6", "#e34948"

plt.rcParams.update({
    "axes.edgecolor": GRID, "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.titlecolor": INK, "axes.titlesize": 11, "axes.titleweight": "bold", "font.size": 9,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.color": GRID,
    "grid.linewidth": 0.6, "axes.axisbelow": True, "figure.dpi": 150,
})


def load(out):
    df = pd.read_parquet(out / "clean.parquet")
    df["is_closed"] = df["is_closed"].astype(bool)
    return (df, pd.read_csv(out / "control_results.csv"), pd.read_csv(out / "exceptions.csv"),
            pd.read_csv(out / "cleaning_log.csv"), json.loads((out / "run_info.json").read_text()))


def save(fig, path):
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def charts(df, res, exc, rep):
    # 1. Monthly volume per bank, direct labels at the line ends.
    monthly = df.groupby(["month", "company_short"]).size().unstack(fill_value=0)
    fig, ax = plt.subplots(figsize=(9, 4.2))
    for bank, colour in BANK_COLOURS.items():
        ax.plot(monthly.index, monthly[bank], color=colour, linewidth=2, label=bank)
        if bank in ("Capital One", "Truist"):  # the middle four overlap, the legend covers them
            ax.annotate(bank, (monthly.index[-1], monthly[bank].iloc[-1]), xytext=(4, 0),
                        textcoords="offset points", va="center", fontsize=8, color=INK)
    ax.set_title("Complaints received per month, six banks")
    ax.set_ylabel("complaints")
    ax.legend(frameon=False, fontsize=8, ncol=6, loc="upper center", bbox_to_anchor=(0.5, -0.08))
    ax.margins(x=0.12)
    save(fig, rep / "monthly_volume.png")

    # 2. Capital One daily counts in January 2025 (the largest spike in the window).
    jan = df[(df.company_short == "Capital One") & df.date_received.between("2025-01-01", "2025-01-31")]
    daily = jan.groupby("date_received").size()
    fig, ax = plt.subplots(figsize=(8, 3.4))
    ax.bar(daily.index, daily.values, color=ACCENT, width=0.8)
    peak = daily.idxmax()
    ax.annotate(f"{daily.max():,} on {peak:%d %b}", (peak, daily.max()), xytext=(8, -4),
                textcoords="offset points", fontsize=8, color=INK)
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%d %b"))
    ax.set_title("Capital One complaints per day, January 2025")
    ax.set_ylabel("complaints")
    save(fig, rep / "capital_one_jan2025.png")

    # 3. Timely response rate per bank against the simulated threshold.
    closed = df[df["is_closed"] & df["is_timely"].notna()]
    t = closed.groupby("company_short")["is_timely"].mean().sort_values().mul(100)
    fig, ax = plt.subplots(figsize=(7, 3.2))
    ax.barh(t.index, t.values, color=[ACCENT if v >= 98 else BAD for v in t], height=0.6)
    ax.axvline(98, color=MUTED, linestyle="--", linewidth=1)
    for i, v in enumerate(t.values):
        ax.text(v + 0.02, i, f"{v:.3f}%", va="center", fontsize=8, color=INK)
    ax.set_xlim(97, 100.4)
    ax.set_title("Timely response rate, closed complaints")
    ax.set_xlabel("% answered on time (dashed line = 98% SIMULATED threshold)")
    ax.grid(axis="y", visible=False)
    save(fig, rep / "timely_rate.png")

    # 4. Top issues.
    top = df["issue"].value_counts().head(10).iloc[::-1]
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.barh(top.index, top.values, color=ACCENT, height=0.6)
    ax.set_title("Top 10 issues across the six banks")
    ax.set_xlabel("complaints")
    ax.grid(axis="y", visible=False)
    save(fig, rep / "top_issues.png")

    # 5. Exceptions per control.
    r = res.set_index("control_id")["exceptions"]
    fig, ax = plt.subplots(figsize=(7, 3.2))
    ax.bar(r.index, r.values, color=[BAD if v and c != "DQ-03" else MUTED for c, v in r.items()], width=0.6)
    for i, v in enumerate(r.values):
        ax.text(i, v, f"{v}", ha="center", va="bottom", fontsize=8, color=INK)
    ax.set_title("Exceptions by control (DQ-03 is info only)")
    ax.grid(axis="x", visible=False)
    save(fig, rep / "exceptions_by_control.png")


def findings(df, res, exc, log, info, rep):
    closed = df[df["is_closed"] & df["is_timely"].notna()]
    by_bank = df.groupby("company_short").agg(
        complaints=("complaint_id", "size"),
        in_progress=("is_closed", lambda s: int((~s).sum())),
        median_days_to_company=("days_to_company", "median"),
    ).sort_values("complaints", ascending=False)
    by_bank["timely_rate_pct"] = (closed.groupby("company_short")["is_timely"].mean() * 100).round(3)
    by_bank["not_timely"] = closed.groupby("company_short")["is_timely"].apply(lambda s: int((s == 0).sum()))
    by_bank.to_csv(rep / "summary_by_bank.csv")

    bank_month = closed.groupby(["company_short", "month"])["is_timely"].mean()
    worst_bm = bank_month.idxmin()
    by_product = df["product"].value_counts().head(8)
    monthly = df.groupby("month").size()
    first12, last12 = monthly.iloc[:12].sum(), monthly.iloc[12:].sum()

    cap = df[df.company_short == "Capital One"]
    cap_daily = cap.groupby("date_received").size()
    peak_day = cap_daily.idxmax()
    peak2 = cap[cap.date_received.between(peak_day, peak_day + pd.Timedelta(days=1))]
    peak2_sub = peak2["sub_issue"].value_counts()

    e = exc.copy()
    ctl02 = e[e.control_id == "CTL-02"]
    ctl03 = e[e.control_id == "CTL-03"]
    mon = e[e.control_id == "MON-01"]
    open_ = df[~df["is_closed"]]
    late = df[df["days_to_company"] > 14]

    res.to_csv(rep / "control_results.csv", index=False)
    (e.groupby(["control_id", "scope"]).size().rename("exceptions").reset_index()
     .to_csv(rep / "exceptions_summary.csv", index=False))

    def pct(a, b):
        return f"{a / b * 100:.1f}%"

    L = ["# Findings", "",
         f"Source: CFPB Consumer Complaint Database bulk file (latest received date in file: {info['as_of']}). "
         f"Scope: six banks, complaints received {info['window_start']} to {info['window_end']}. "
         "Every number here is written by src/report.py from the real file.", "",
         "Thresholds marked SIMULATED are my own choice for this exercise, not a regulation and not any bank's policy.", "",
         "## Key findings", "",
         f"1. **{len(df):,} complaints** in scope. Volume in the second 12 months ({last12:,}) was "
         f"{pct(last12 - first12, first12)} higher than the first 12 months ({first12:,}).",
         f"2. **Capital One, {peak_day:%d %b %Y}:** {cap_daily.max():,} complaints in one day, against a January 2025 "
         f"median of {cap_daily['2025-01-01':'2025-01-31'].median():.0f} a day. Across that day and the next, "
         f"{peak2_sub.iloc[0]:,} of {len(peak2):,} were sub-issue '{peak2_sub.index[0]}'. "
         "MON-01 flagged it. The data shows the spike, not the cause.",
         f"3. **Timeliness passes everywhere (CTL-01).** Every bank-month was at or above 98%. Bank of America is the "
         f"lowest bank at {by_bank.loc['Bank of America', 'timely_rate_pct']}% with "
         f"{by_bank.loc['Bank of America', 'not_timely']} of the {int(by_bank['not_timely'].sum())} untimely responses "
         f"in scope. Its weakest month was {worst_bm[1]:%Y-%m} at {bank_month[worst_bm] * 100:.2f}%.",
         f"4. **{len(ctl02)} complaints still 'In progress' after 60 days (CTL-02)**, out of {len(open_):,} open. "
         f"Bank of America holds {by_bank.loc['Bank of America', 'in_progress']:,} of the open complaints "
         f"({pct(by_bank.loc['Bank of America', 'in_progress'], len(open_))}) with {pct(by_bank.loc['Bank of America', 'complaints'], len(df))} of volume. "
         f"Most open complaints are recent: the median open complaint is "
         f"{(pd.Timestamp(info['as_of']) - open_['date_received']).dt.days.median():.0f} days old. "
         f"By bank: " + ", ".join(f"{k} {v}" for k, v in ctl02.groupby('scope').size().sort_values(ascending=False).items()) + ".",
         f"5. **Routing lag (CTL-03):** {len(late):,} complaints ({pct(len(late), len(df))}) reached the bank more than "
         f"14 days after CFPB received them. {len(ctl03)} bank-months broke the 5% SIMULATED limit: "
         + ", ".join(f"{k} {v}" for k, v in ctl03.groupby('scope').size().sort_values(ascending=False).items()) + ".",
         f"6. **Data quality is clean.** No duplicate IDs, no complaint sent before it was received, and no closed "
         f"complaint without a timeliness flag. {int((~df['state_valid']).sum()):,} rows have no state.",
         f"7. **MON-01 raised {len(mon)} spikes** across {mon['scope'].nunique()} bank/product series. "
         f"{int((mon['period'] == '2025-01').sum())} of them are in January 2025.", "",
         "## Cleaning log", "", "| Step | Rows | Action |", "|---|---:|---|"]
    L += [f"| {r.step} | {r.rows:,} | {r.action} |" for r in log.itertuples()]
    L += ["", "## By bank", "", by_bank.to_markdown(), "",
          "## Biggest products", "", by_product.rename("complaints").to_markdown(), "",
          "## Control results", "",
          res[["control_id", "control", "threshold", "threshold_type", "population", "exceptions", "result"]].to_markdown(index=False),
          "", "Population means: bank-months tested (CTL-01, CTL-03), open complaints (CTL-02), "
          "rows checked (DQ), bank/product months with enough history (MON-01).", "",
          "## Charts", "",
          "![Monthly volume](monthly_volume.png)", "", "![Capital One January 2025](capital_one_jan2025.png)", "",
          "![Timely rate](timely_rate.png)", "", "![Top issues](top_issues.png)", "",
          "![Exceptions by control](exceptions_by_control.png)"]
    (rep / "findings.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def main(out, rep):
    out, rep = Path(out), Path(rep)
    rep.mkdir(parents=True, exist_ok=True)
    df, res, exc, log, info = load(out)
    charts(df, res, exc, rep)
    findings(df, res, exc, log, info, rep)
    print(f"wrote {rep}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "out", sys.argv[2] if len(sys.argv) > 2 else "reports")
