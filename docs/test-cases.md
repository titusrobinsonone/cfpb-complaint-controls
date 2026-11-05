# Test cases

Each row matches one test in `tests/test_controls.py`. The input is a hand-made fixture in `tests/conftest.py` (93 rows, not real data) with one planted example of each problem. Run with `python -m pytest -v`.

| ID | Control / step | Input | Expected result |
|---|---|---|---|
| TC-01 | Cleaning: received date | Complaint 2008 with received date "not a date" | Dropped, logged as 1 row under "unreadable received date" |
| TC-02 | Cleaning: date formats | Complaint 2007 dated "02/21/25" (US short format) | Parsed as 2025-02-21 and present in the fact table |
| TC-03 | DQ-01 duplicates | Complaint ID 2000 appears twice | Fact IDs are unique; log shows 1 duplicate dropped |
| TC-04 | Reconciliation | Whole fixture | Rows read minus other companies, bad dates, out of window and duplicates equals rows after cleaning, which equals fact rows |
| TC-05 | Star schema | Fact and all dims | Every dim key is unique and every fact key exists in its dim |
| TC-06 | CTL-01 timely rate | Bank of America Feb 2025: 44 closed with a flag, 2 not timely (95.45%) | One exception, period 2025-02, threshold_type SIMULATED. Jan 2025 (100%) passes |
| TC-07 | CTL-02 aging | Complaint 2003 in progress since 2025-01-02, extract date 2025-04-01 | One exception for 2003, observed 89 days. Complaint 2004 (0 days) passes |
| TC-08 | CTL-03 routing lag | Wells Fargo Mar 2025: 1 complaint, 33 days to route | One exception, Wells Fargo 2025-03, observed late share 1.0 |
| TC-09 | DQ-02 date order | Complaint 2001 sent 14 days before it was received | Exception that names 2001 |
| TC-10 | DQ-03 state | Complaint 2005 with blank state | Result "info", count 1, not written to the exceptions log |
| TC-11 | DQ-04 timely flag | Complaint 2006 closed with blank timely flag | Exception that names 2006 |
| TC-12 | Exceptions log | All exceptions | Unique exception IDs, root cause filled, remediation_status "open" |
| TC-13 | Scope filter | Complaint 2009 (Equifax) and 2010 (received 2024-06-01) | Logged as 1 other-company row and 1 out-of-window row; neither in the fact table |
| TC-14 | Bulk file layout | Same rows without narrative, consent and disputed columns | Loads, row count matches the log, consent column empty |
| TC-15 | State recode | Complaint 2011 with "UNITED STATES MINOR OUTLYING ISLANDS" | Recoded to UM and marked valid |
| TC-16 | MON-01 spike | Truist mortgage: baseline of about 20 a month for 6 months, then 20, 21, 90 | Only 2025-03 flagged; 3 months tested. With no baseline, nothing tested |

Last run: 16 passed.
