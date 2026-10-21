import re
import io

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Tracking & Milestone Completeness", layout="wide")
st.title("Shipment Tracking & Milestone Completeness")

MILESTONES = {
    "Pickup Arrival": "pickup arrival time utc",
    "Pickup Departure": "pickup departure time utc",
    "Drop-off Arrival": "drop off arrival time utc",
    "Drop-off Departure": "drop off departure time utc",
}


def norm(s):
    """Normalise a column name: lowercase, non-alphanumerics -> single space."""
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def find_col(df, target, contains=None):
    """Find a column by normalised name (exact first, then 'contains' keywords)."""
    cols = {norm(c): c for c in df.columns}
    if target in cols:
        return cols[target]
    if contains:
        for n, orig in cols.items():
            if all(k in n for k in contains):
                return orig
    return None


def find_milestone_col(df, norm_target):
    cols = {norm(c): c for c in df.columns}
    if norm_target in cols:
        return cols[norm_target]
    # tolerate "dropoff" vs "drop off", "destination" vs "drop off"
    alt = norm_target.replace("drop off", "dropoff")
    if alt in cols:
        return cols[alt]
    alt2 = norm_target.replace("drop off", "destination")
    if alt2 in cols:
        return cols[alt2]
    kind = "pickup" if "pickup" in norm_target else "drop"
    when = "arrival" if "arrival" in norm_target else "departure"
    for n, orig in cols.items():
        if kind in n and when in n and "utc" in n:
            return orig
    return None


def to_bool(series):
    def conv(v):
        if isinstance(v, bool):
            return v
        if pd.isna(v):
            return False
        if isinstance(v, (int, float)):
            return v == 1
        return str(v).strip().lower() in ("true", "t", "yes", "y", "1")

    return series.map(conv)


def has_value(series):
    def conv(v):
        if pd.isna(v):
            return False
        return str(v).strip().lower() not in ("", "nan", "nat", "none", "null")

    return series.map(conv)


def read_file(f):
    if f.name.lower().endswith(".csv"):
        return {"": pd.read_csv(f)}
    xls = pd.ExcelFile(f)
    return {sh: xls.parse(sh) for sh in xls.sheet_names}


# ---------------- UI ----------------
st.sidebar.header("Milestones to count")
selected = [
    label for label in MILESTONES if st.sidebar.checkbox(label, value=True)
]

files = st.file_uploader(
    "Upload Excel / CSV files (one or many carriers)",
    type=["xlsx", "xls", "csv"],
    accept_multiple_files=True,
)

if not files:
    st.info("Upload one or more files to begin.")
    st.stop()

if not selected:
    st.warning("Select at least one milestone in the sidebar.")
    st.stop()

rows = []
problems = []

for f in files:
    try:
        sheets = read_file(f)
    except Exception as e:
        problems.append(f"{f.name}: could not read ({e})")
        continue

    for sheet_name, df in sheets.items():
        if df.empty:
            continue

        tracked_col = find_col(df, "tracked", contains=["track"])
        if tracked_col is None:
            problems.append(f"{f.name} [{sheet_name}]: no 'tracked' column found")
            continue

        ms_cols = {}
        for label in selected:
            col = find_milestone_col(df, MILESTONES[label])
            if col is None:
                problems.append(f"{f.name} [{sheet_name}]: column for '{label}' not found")
            ms_cols[label] = col
        if any(c is None for c in ms_cols.values()):
            continue

        carrier_col = find_col(df, "carrier name", contains=["carrier"])
        customer_col = (
            find_col(df, "customer name", contains=["customer"])
            or find_col(df, "tenant name", contains=["tenant"])
            or find_col(df, "shipper name", contains=["shipper"])
        )

        d = pd.DataFrame(
            {
                "Customer": df[customer_col].astype(str).str.strip() if customer_col else "(not in file)",
                "Carrier": df[carrier_col].astype(str).str.strip() if carrier_col else "(not in file)",
                "tracked": to_bool(df[tracked_col]),
            }
        )
        complete = pd.Series(True, index=df.index)
        for label, col in ms_cols.items():
            complete &= has_value(df[col])
        d["complete"] = complete & d["tracked"]
        d["File"] = f.name

        for (cust, carr), g in d.groupby(["Customer", "Carrier"], dropna=False):
            total = len(g)
            tracked = int(g["tracked"].sum())
            untracked = total - tracked
            comp = int(g["complete"].sum())
            rows.append(
                {
                    "File": f.name,
                    "Customer": cust,
                    "Carrier": carr,
                    "Total Shipments": total,
                    "Tracked": tracked,
                    "Untracked": untracked,
                    "Tracked %": round(tracked / total * 100, 2) if total else 0.0,
                    "Untracked %": round(untracked / total * 100, 2) if total else 0.0,
                    "Milestone Complete (of tracked)": comp,
                    "Milestone Completeness %": round(comp / tracked * 100, 2) if tracked else 0.0,
                }
            )

for p in problems:
    st.error(p)

if not rows:
    st.stop()

res = pd.DataFrame(rows)

# ---------------- Overall summary ----------------
tot = res["Total Shipments"].sum()
trk = res["Tracked"].sum()
unt = res["Untracked"].sum()
cmp_ = res["Milestone Complete (of tracked)"].sum()

st.subheader("Overall")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Shipments", f"{tot:,}")
c2.metric("Tracked", f"{trk:,}", f"{trk / tot * 100:.2f}%" if tot else None)
c3.metric("Untracked", f"{unt:,}", f"{unt / tot * 100:.2f}%" if tot else None, delta_color="off")
c4.metric(
    "Milestone Completeness",
    f"{cmp_ / trk * 100:.2f}%" if trk else "0.00%",
    f"{cmp_:,} of {trk:,} tracked",
    delta_color="off",
)
st.caption("Milestones counted: " + ", ".join(selected))

# ---------------- Per carrier table ----------------
st.subheader("By file / customer / carrier")
st.dataframe(
    res,
    width="stretch",
    hide_index=True,
    column_config={
        "Tracked %": st.column_config.NumberColumn(format="%.2f%%"),
        "Untracked %": st.column_config.NumberColumn(format="%.2f%%"),
        "Milestone Completeness %": st.column_config.NumberColumn(format="%.2f%%"),
    },
)

buf = io.BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as w:
    res.to_excel(w, index=False, sheet_name="Summary")
st.download_button(
    "Download table as Excel",
    buf.getvalue(),
    file_name="milestone_completeness_summary.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
