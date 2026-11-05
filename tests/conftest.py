import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Search-page export header. The bulk file drops narrative, consent and disputed.
HEADER = ["Date received", "Product", "Sub-product", "Issue", "Sub-issue", "Consumer complaint narrative",
          "Company public response", "Company", "State", "ZIP code", "Tags", "Consumer consent provided?",
          "Submitted via", "Date sent to company", "Company response to consumer", "Timely response?",
          "Consumer disputed?", "Complaint ID"]
BULK_DROP = ["Consumer complaint narrative", "Consumer consent provided?", "Consumer disputed?"]

BOFA = "BANK OF AMERICA, NATIONAL ASSOCIATION"
WELLS = "WELLS FARGO & COMPANY"


def row(cid, received, sent, company=BOFA, product="Credit card",
        response="Closed with explanation", timely="Yes", state="NC"):
    return [received, product, "General-purpose credit card", "Billing dispute", "", "",
            "", company, state, "282XX", "", "Consent not provided", "Web", sent, response, timely, "N/A", str(cid)]


def fixture_rows():
    """A tiny hand-made file with one example of every problem the pipeline
    and controls are meant to catch. Not real data."""
    rows = []
    cid = 1000
    # 40 normal, timely complaints in Jan 2025
    for _ in range(40):
        rows.append(row(cid, "2025-01-10", "2025-01-10")); cid += 1
    # Feb 2025: 40 complaints, 2 untimely, so the month lands under 98% (CTL-01)
    for i in range(40):
        rows.append(row(cid, "2025-02-10", "2025-02-11", timely="No" if i < 2 else "Yes")); cid += 1
    rows.append(row(2000, "2025-02-12", "2025-02-12"))
    rows.append(row(2000, "2025-02-12", "2025-02-12"))                          # duplicate ID (DQ-01)
    rows.append(row(2001, "2025-02-15", "2025-02-01"))                          # sent before received (DQ-02)
    rows.append(row(2002, "2025-03-05", "2025-04-07", company=WELLS))           # 33 days to route (CTL-03)
    rows.append(row(2003, "2025-01-02", "2025-01-02", response="In progress", timely=""))  # open 89 days (CTL-02)
    rows.append(row(2004, "2025-04-01", "2025-04-01", response="In progress", timely=""))  # latest date, open 0 days
    rows.append(row(2005, "2025-02-20", "2025-02-20", state=""))                # missing state (DQ-03)
    rows.append(row(2006, "2025-02-20", "2025-02-20", timely=""))               # closed, no timely flag (DQ-04)
    rows.append(row(2007, "02/21/25", "02/21/25", company=WELLS))               # US date format
    rows.append(row(2008, "not a date", "2025-02-21"))                          # unreadable date, dropped
    rows.append(row(2009, "2025-03-01", "2025-03-01", company="EQUIFAX, INC.")) # other company, excluded
    rows.append(row(2010, "2024-06-01", "2024-06-01"))                          # before the window, excluded
    rows.append(row(2011, "2025-02-20", "2025-02-20", state="UNITED STATES MINOR OUTLYING ISLANDS"))
    return rows


@pytest.fixture
def raw_csv(tmp_path):
    p = tmp_path / "complaints.csv"
    pd.DataFrame(fixture_rows(), columns=HEADER).to_csv(p, index=False)
    return p


@pytest.fixture
def bulk_csv(tmp_path):
    """Same rows, laid out like the bulk download."""
    p = tmp_path / "bulk.csv"
    pd.DataFrame(fixture_rows(), columns=HEADER).drop(columns=BULK_DROP).to_csv(p, index=False)
    return p
