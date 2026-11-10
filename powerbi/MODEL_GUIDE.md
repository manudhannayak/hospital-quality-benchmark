# Power BI build guide (about 45 minutes)

The pipeline writes star-schema extracts to `output/powerbi/`. These steps turn them into the dashboard.
Power BI Desktop is Windows-only; everything upstream runs anywhere.

## 1. Load the data
1. **Get Data > Text/CSV**. Load `dim_hospital.csv`, `dim_measure.csv`, `dim_release.csv` and `fact_measure.csv`.
2. In Power Query, set types:
   - `facility_id` = **Text** in every table. CMS IDs have leading zeros (030087), and a few end in letters (03012F).
   - `value`, `denominator` = Decimal number. `low_volume_flag`, `is_focus_system`, `in_market`, `higher_is_better`, `dq_pass` = Whole number.
   - `release_id` = Text (ISO date string, so it sorts correctly).
3. **Close & Apply.**

> To refresh automatically, point the source at the GitHub repo's raw CSV URLs, or at a OneDrive or SharePoint
> folder that the scheduled job writes to. Then set a scheduled refresh in the Power BI Service.

## 2. Relationships (Model view)
| From (many) | To (one) | Cardinality | Filter direction |
|---|---|---|---|
| fact_measure[facility_id] | dim_hospital[facility_id] | Many-to-one | Single |
| fact_measure[measure_id] | dim_measure[measure_id] | Many-to-one | Single |
| fact_measure[release_id] | dim_release[release_id] | Many-to-one | Single |

This is a classic star: one fact table and three dimensions, with no bidirectional filters.

## 3. Measures
Create a blank table named `_Measures` and paste each measure from `measures.dax`.
Format percentage-style measures to 1 decimal place and star measures to 2.

## 4. Report pages

**Page 1: Executive scorecard**
- Card row: `HonorHealth Avg`, `Market Avg`, `Gap Status`. Add a slicer on `dim_measure[measure_label]` (single select, dropdown).
- Clustered bar: Axis = `dim_hospital[health_system]`, Value = `Avg Value`, filtered to `in_market = 1`.
  Color HonorHealth purple (#6B2A6B) with conditional formatting on `is_focus_system`.
- Text box: paste `output/summary.txt`. Or use a Smart Narrative visual.

**Page 2: Hospital heatmap**
- Matrix: Rows = `dim_hospital[facility_name]` (filter `is_focus_system = 1`), Columns = `dim_measure[measure_label]`,
  Values = `Avg Value`.
- Conditional formatting > Background color > Field value > `Rank Color`.
- Tooltip page showing `Rank Label`, `fact_measure[denominator]` and `Low Volume Hospitals`.

**Page 3: Market map and rank**
- Table of all in-market hospitals with `Market Rank`, sorted ascending, with a `health_system` slicer.
- Optional: an Azure Maps visual on `city`.

**Page 4: Trend and alerts** (useful once there are 2+ releases)
- Line chart: X = `dim_release[release_id]`, Y = `Avg Value`, Legend = `health_system`.
- Table of `Change vs Prior`, filtered to absolute value of 3 or more.

**Page 5: Data quality**
- Load `output/dq_results.json` (Get Data > JSON > expand `results`) and show it as a table, with status conditional formatting.

## 5. Publish
Publish to the Power BI Service, then set a scheduled refresh for the day after the GitHub Action runs.
Add row-level security on `health_system` if it's shared outside the quality team.
