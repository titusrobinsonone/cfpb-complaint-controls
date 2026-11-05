# Power BI build guide

How to build the executive risk dashboard from the CSVs in `out/`. The .pbix is not in the repo because it would hold row-level data. Allow about two hours.

## 1. Produce the files

```
python src/pipeline.py ../data/complaints.csv out
python src/controls.py out
```

You should now have in `out/`:

| File | What it is | Rows (real run) |
|---|---|---:|
| fact_complaint.csv | one row per complaint | 254,568 |
| dim_date.csv | one row per day, 2024-11-01 to 2026-10-31 | 730 |
| dim_company.csv | six banks | 6 |
| dim_product.csv | product and sub-product | 55 |
| dim_issue.csv | issue and sub-issue | 257 |
| dim_channel.csv | Web, Phone, Referral, Postal mail | 4 |
| dim_response.csv | company response and timely flag | 8 |
| dim_state.csv | state code and a valid flag | 61 |
| control_results.csv | one row per control | 8 |
| exceptions.csv | the exceptions log | 155 |
| param_as_of.csv | the extract date (2026-11-05) | 1 |

## 2. Load

1. Power BI Desktop > Get data > Text/CSV. Load each file above.
2. In Power Query check the types:
   - `fact_complaint`: `date_received` and `date_sent` as Date, `days_to_company` Whole number, `is_timely` Decimal number (it has blanks for open complaints), `is_closed` Whole number, all `_key` columns Whole number. Set `complaint_id` to Text.
   - `dim_date`: `date` and `month_start` as Date.
   - `param_as_of`: `as_of` as Date.
3. Close & Apply.

## 3. Model

Model view. Make these relationships, all one-to-many, single direction, dimension to fact:

| From (one) | To (many) |
|---|---|
| dim_date[date_key] | fact_complaint[date_key] |
| dim_company[company_key] | fact_complaint[company_key] |
| dim_product[product_key] | fact_complaint[product_key] |
| dim_issue[issue_key] | fact_complaint[issue_key] |
| dim_channel[channel_key] | fact_complaint[channel_key] |
| dim_response[response_key] | fact_complaint[response_key] |
| dim_state[state_key] | fact_complaint[state_key] |

Leave `control_results`, `exceptions` and `param_as_of` unrelated. The exceptions have mixed grain (one complaint, a bank-month, a bank/product-month), so joining them to the fact table would be wrong.

Then:
- Select `dim_date` > Table tools > Mark as date table > column `date`. `DATEADD` in the MoM measures needs this.
- Sort `dim_date[month_name]` by `dim_date[month]`.
- Hide the `_key` columns in the fact table so people slice by the dimension columns.

## 4. Measures

Home > Enter data > make an empty table called `_Measures`. Then add each measure from `measures.dax` (New measure, paste one at a time). Format:
- `Timely %`, `Late To Company %`, `Complaints MoM %`: Percentage, 2 decimals.
- Counts: Whole number with thousands separator.

What each one does, so you can explain it:

| Measure | Logic |
|---|---|
| Complaints | Counts fact rows. One row is one complaint. |
| Closed Complaints / Open Complaints | Filters `is_closed`. Open means CFPB still shows "In progress". |
| Timely % | Timely closed complaints divided by closed complaints that have a flag. Open complaints are left out because CFPB marks them "Yes" before the bank is done. |
| Timely Below Threshold | 1 when a slice has at least 30 closed complaints and is under 98%. Mirrors CTL-01. Use it in a table by bank and month. |
| Median Days To Company | Median of days from CFPB receipt to the bank. It is 0 on this data because about 91% go the same day, so also show Avg Days To Company and Late To Company %. |
| Late To Company % | Share of complaints that took more than 14 days (SIMULATED) to reach the bank. Mirrors CTL-03. |
| Complaints Prior Month | `DATEADD` shifts the filter back one month on the date table. |
| Complaints MoM Change / MoM % | This month minus last month, and that as a share of last month. |
| Exceptions / Open Exceptions | Counts rows in the exceptions log. All start "open" because the remediation workflow is simulated. |
| Open Over 60 Days | Open complaints whose age at the extract date is over 60 days. Mirrors CTL-02. |

## 5. Pages

**Page 1, Executive risk summary**
- Cards: Complaints, Timely %, Open Exceptions, Open Over 60 Days, Late To Company %.
- Line chart: `dim_date[month_start]` on the axis, Complaints as value, `dim_company[company_short]` as legend.
- Clustered bar: Timely % by `company_short`, add a constant line at 0.98 labelled "98% SIMULATED".
- Slicers: `company_short`, `dim_product[product]`, `dim_date[year_month]`.

**Page 2, Control results**
- Table from `control_results`: control_id, control, threshold, threshold_type, population, exceptions, result. Conditional format `result` (red for exceptions).
- Bar chart: Exceptions by `exceptions[control_id]`.
- Table from `exceptions`: exception_id, control_id, scope, period, observed, root_cause, remediation_action, remediation_status. Add a slicer on control_id.

**Page 3, Trend and drivers**
- Matrix: rows `company_short`, columns `year_month`, values Complaints MoM %. Conditional format the background.
- Bar: top 10 `dim_issue[issue]` by Complaints (Top N filter).
- Stacked bar: Complaints by `dim_channel[submitted_via]` and company.

Put a text box on every page: "Thresholds marked SIMULATED are my own for this exercise. Data: CFPB Consumer Complaint Database."

## 6. Check your numbers

With no slicers set, the visuals should match the Python run:

| Measure | Expected |
|---|---:|
| Complaints | 254,568 |
| Closed Complaints | 250,521 |
| Open Complaints | 4,047 |
| Timely % | 99.90% |
| Median Days To Company | 0 |
| Avg Days To Company | 1.24 |
| Late To Company | 6,667 |
| Late To Company % | 2.62% |
| Open Over 60 Days | 76 |
| Exceptions | 155 |
| Exceptions On Simulated Thresholds | 79 |
| Complaints MoM Change, Oct 2026 | -2,302 (-19.6%) |

If one is off, check the column types in step 2 first. A text `is_timely` or `days_to_company` is the usual cause.
