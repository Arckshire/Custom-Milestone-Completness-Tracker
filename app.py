import io
import re

import numpy as np
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

ATTR_NAME_RE = re.compile(r"^(?:attr|adr|attribute) ?(\d+) name$")


# ---------------- helpers ----------------
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


def clean_val(v):
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return "(blank)"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    s = str(v).strip()
    return s if s else "(blank)"


def attr_long(df):
    """Long table of a sheet's attributes: one row per (shipment position, attribute name, value).
    Works across all slots (Attr1..AttrN), so the same attribute is found whichever slot it is in."""
    cols = {norm(c): c for c in df.columns}
    parts = []
    for n, c in cols.items():
        if not ATTR_NAME_RE.match(n):
            continue
        vcol = cols.get(n[:-4] + "value")
        if vcol is None:
            continue
        sub = pd.DataFrame(
            {"pos": np.arange(len(df)), "name": df[c].values, "value": df[vcol].values}
        )
        parts.append(sub[sub["name"].notna()])
    if not parts:
        return pd.DataFrame(columns=["pos", "key", "label", "value"])
    out = pd.concat(parts, ignore_index=True)
    out["label"] = out["name"].astype(str).str.strip()
    out = out[out["label"] != ""].copy()
    out["key"] = out["label"].str.lower()
    out["value"] = out["value"].map(clean_val)
    return out[["pos", "key", "label", "value"]]


@st.cache_data(show_spinner=False)
def load_file(name, data):
    bio = io.BytesIO(data)
    if name.lower().endswith(".csv"):
        sheets = {"": pd.read_csv(bio)}
    else:
        xls = pd.ExcelFile(bio)
        sheets = {sh: xls.parse(sh) for sh in xls.sheet_names}
    return {sh: (df, attr_long(df)) for sh, df in sheets.items() if not df.empty}


# ---------------- sidebar: milestones ----------------
st.sidebar.header("Milestones to count")
selected = [label for label in MILESTONES if st.sidebar.checkbox(label, value=True)]

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

problems = []
sheets_loaded = []  # (file name, sheet name, df, attr_long)
for f in files:
    try:
        for sh, (df, L) in load_file(f.name, f.getvalue()).items():
            sheets_loaded.append((f.name, sh, df, L))
    except Exception as e:
        problems.append(f"{f.name}: could not read ({e})")

# ---------------- sidebar: attribute filters ----------------
catalogue = {}  # key -> {"label": str, "values": set}
for _, _, _, L in sheets_loaded:
    for key, g in L.groupby("key"):
        entry = catalogue.setdefault(key, {"label": g["label"].iloc[0], "values": set()})
        entry["values"].update(g["value"].unique())
attr_keys = sorted(catalogue, key=lambda k: catalogue[k]["label"].lower())

st.sidebar.divider()
st.sidebar.header("Attribute filters")
active_filters = []  # (mode, key, values)
join_mode = "AND"

if not attr_keys:
    st.sidebar.caption("No attribute columns (Attr1 Name / Attr1 Value ...) found in the uploaded files.")
else:
    st.session_state.setdefault("n_filters", 0)

    def _add():
        st.session_state["n_filters"] += 1

    def _remove():
        st.session_state["n_filters"] = max(0, st.session_state["n_filters"] - 1)

    b1, b2 = st.sidebar.columns(2)
    b1.button("+ Add filter", on_click=_add, width="stretch")
    b2.button("Remove last", on_click=_remove, width="stretch")

    if st.session_state["n_filters"] > 1:
        join_mode = st.sidebar.radio(
            "Combine 'Is' filters with",
            ["AND", "OR"],
            horizontal=True,
            help="AND: a shipment must match every 'Is' filter. OR: matching any one is enough. "
            "'Is not' filters always exclude, whatever you pick here.",
        )

    for i in range(st.session_state["n_filters"]):
        with st.sidebar.container(border=True):
            mode = st.selectbox("Condition", ["Is", "Is not"], key=f"mode_{i}")
            key = st.selectbox(
                "Attribute",
                attr_keys,
                format_func=lambda k: catalogue[k]["label"],
                key=f"attr_{i}",
            )
            vals = st.multiselect(
                "Values",
                sorted(catalogue[key]["values"]),
                key=f"vals_{i}_{key}",
                placeholder="Type to search, pick one or more",
            )
            if vals:
                active_filters.append((mode, key, vals))

    if st.session_state["n_filters"] and not active_filters:
        st.sidebar.caption("Pick at least one value in a filter for it to apply.")


def keep_mask(n_rows, L):
    """Boolean mask of shipments kept by the attribute filters."""
    is_masks, not_masks = [], []
    for mode, key, vals in active_filters:
        hit = np.zeros(n_rows, dtype=bool)
        pos = L.loc[(L["key"] == key) & (L["value"].isin(vals)), "pos"].to_numpy()
        hit[pos] = True
        # "Is not" keeps shipments that don't have the attribute at all
        (is_masks if mode == "Is" else not_masks).append(hit if mode == "Is" else ~hit)
    keep = np.ones(n_rows, dtype=bool)
    if is_masks:
        keep &= (np.logical_and if join_mode == "AND" else np.logical_or).reduce(is_masks)
    for m in not_masks:
        keep &= m
    return keep


# ---------------- compute ----------------
rows = []
all_tracked = []  # per-shipment ping data of tracked shipments, for pooled overall stats
shipments_before = 0
shipments_after = 0

for fname, sheet_name, df, L in sheets_loaded:
    shipments_before += len(df)
    if active_filters:
        df = df[keep_mask(len(df), L)]
    shipments_after += len(df)
    if df.empty:
        continue

    tracked_col = find_col(df, "tracked", contains=["track"])
    if tracked_col is None:
        problems.append(f"{fname} [{sheet_name}]: no 'tracked' column found")
        continue

    ms_cols = {}
    for label in selected:
        col = find_milestone_col(df, MILESTONES[label])
        if col is None:
            problems.append(f"{fname} [{sheet_name}]: column for '{label}' not found")
        ms_cols[label] = col
    if any(c is None for c in ms_cols.values()):
        continue

    carrier_col = find_col(df, "carrier name", contains=["carrier"])
    customer_col = (
        find_col(df, "customer name", contains=["customer"])
        or find_col(df, "tenant name", contains=["tenant"])
        or find_col(df, "shipper name", contains=["shipper"])
    )

    pings_col = find_col(df, "latency updates received", contains=["updates", "received"])
    interval_col = find_col(df, "ping interval min", contains=["ping", "interval"])
    if pings_col is None:
        problems.append(f"{fname} [{sheet_name}]: ping count column ('Latency Updates Received') not found - ping stats blank")
    if interval_col is None:
        problems.append(f"{fname} [{sheet_name}]: 'Ping Interval (min)' column not found - interval stats blank")

    d = pd.DataFrame(
        {
            "Customer": df[customer_col].map(clean_val).values if customer_col else "(not in file)",
            "Carrier": df[carrier_col].map(clean_val).values if carrier_col else "(not in file)",
            "tracked": to_bool(df[tracked_col]).values,
            "pings": pd.to_numeric(df[pings_col], errors="coerce").values if pings_col else np.nan,
            "interval": pd.to_numeric(df[interval_col], errors="coerce").values if interval_col else np.nan,
        }
    )
    present = pd.DataFrame({label: has_value(df[col]).values for label, col in ms_cols.items()})
    for label in selected:
        d[f"ms_{label}"] = present[label].values
    d["ms_received"] = present.sum(axis=1).values
    d["complete"] = present.all(axis=1).values & d["tracked"].values
    all_tracked.append(d[d["tracked"]][["pings", "interval"]])

    for (cust, carr), g in d.groupby(["Customer", "Carrier"], dropna=False):
        total = len(g)
        tracked = int(g["tracked"].sum())
        untracked = total - tracked
        comp = int(g["complete"].sum())
        gt = g[g["tracked"]]  # everything below is for tracked shipments only
        ms_got = int(gt["ms_received"].sum())
        ms_exp = tracked * len(selected)
        rows.append(
            {
                "File": fname,
                "Customer": cust,
                "Carrier": carr,
                "Total Shipments": total,
                "Tracked": tracked,
                "Untracked": untracked,
                "Tracked %": round(tracked / total * 100, 2) if total else 0.0,
                "Untracked %": round(untracked / total * 100, 2) if total else 0.0,
                "Milestone Complete (of tracked)": comp,
                "Milestone Completeness %": round(comp / tracked * 100, 2) if tracked else 0.0,
                "Milestones Received": ms_got,
                "Milestones Expected": ms_exp,
                "Milestone Achieved %": round(ms_got / ms_exp * 100, 2) if ms_exp else 0.0,
                **{
                    f"{label} %": round(gt[f"ms_{label}"].sum() / tracked * 100, 2) if tracked else 0.0
                    for label in selected
                },
                **{f"_{label} n": int(gt[f"ms_{label}"].sum()) for label in selected},
                "Avg Ping Interval (min)": round(gt["interval"].mean(), 2),
                "Median Ping Interval (min)": round(gt["interval"].median(), 2),
                "Avg Pings / Shipment": round(gt["pings"].mean(), 2),
                "Median Pings / Shipment": round(gt["pings"].median(), 2),
            }
        )

for p in problems:
    st.error(p)

if active_filters:
    parts = []
    for mode, key, vals in active_filters:
        shown = ", ".join(vals[:5]) + (f" (+{len(vals) - 5} more)" if len(vals) > 5 else "")
        parts.append(f"{catalogue[key]['label']} {mode.lower()} [{shown}]")
    sep = f" {join_mode} " if join_mode == "OR" else " AND "
    st.info(
        f"Filters applied: {sep.join(parts)}. "
        f"Shipments kept: {shipments_after:,} of {shipments_before:,}."
    )

if not rows:
    st.warning("No shipments left after filtering." if active_filters else "Nothing to show.")
    st.stop()

res = pd.DataFrame(rows).sort_values(["File", "Total Shipments"], ascending=[True, False])

# ---------------- Overall summary ----------------
tot = res["Total Shipments"].sum()
trk = res["Tracked"].sum()
unt = res["Untracked"].sum()
cmp_ = res["Milestone Complete (of tracked)"].sum()
ms_got_all = res["Milestones Received"].sum()
ms_exp_all = res["Milestones Expected"].sum()

st.subheader("Overall")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total Shipments", f"{tot:,}")
c2.metric("Tracked", f"{trk:,}", f"{trk / tot * 100:.2f}%" if tot else None)
c3.metric("Untracked", f"{unt:,}", f"{unt / tot * 100:.2f}%" if tot else None, delta_color="off")
c4.metric(
    "Milestone Completeness",
    f"{cmp_ / trk * 100:.2f}%" if trk else "0.00%",
    f"{cmp_:,} of {trk:,} tracked",
    delta_color="off",
    help="Share of tracked shipments that have ALL the selected milestones.",
)
c5.metric(
    "Milestone Achieved",
    f"{ms_got_all / ms_exp_all * 100:.2f}%" if ms_exp_all else "0.00%",
    f"{ms_got_all:,} of {ms_exp_all:,} milestones",
    delta_color="off",
    help="Milestones received / milestones expected (tracked shipments x selected milestones).",
)
st.caption("Milestones counted: " + ", ".join(selected))

# Per-milestone achieved: share of tracked shipments that received each selected milestone
mcols = st.columns(len(selected))
for col, label in zip(mcols, selected):
    n = res[f"_{label} n"].sum()
    col.metric(
        f"{label} %",
        f"{n / trk * 100:.2f}%" if trk else "0.00%",
        f"{n:,} of {trk:,} tracked",
        delta_color="off",
    )

# Ping stats are pooled over all tracked shipments (not an average of per-row averages)
pool = pd.concat(all_tracked) if all_tracked else pd.DataFrame(columns=["pings", "interval"])


def fmt(x):
    return "n/a" if pd.isna(x) else f"{x:,.2f}"


p1, p2, p3, p4 = st.columns(4)
p1.metric("Avg Ping Interval (min)", fmt(pool["interval"].mean()))
p2.metric("Median Ping Interval (min)", fmt(pool["interval"].median()))
p3.metric("Avg Pings / Shipment", fmt(pool["pings"].mean()))
p4.metric("Median Pings / Shipment", fmt(pool["pings"].median()))
st.caption(
    "Ping stats cover tracked shipments only. Pings = 'Latency Updates Received'; "
    "ping interval = 'Ping Interval (min)' (shipments with a blank interval are excluded from interval stats)."
)

# ---------------- Per carrier table ----------------
st.subheader("By file / customer / carrier")
pct = st.column_config.NumberColumn(format="%.2f%%")
num = st.column_config.NumberColumn(format="%.2f")
show = res.drop(columns=[c for c in res.columns if c.startswith("_")])
st.dataframe(
    show,
    width="stretch",
    hide_index=True,
    column_config={
        "Tracked %": pct,
        "Untracked %": pct,
        "Milestone Completeness %": pct,
        "Milestone Achieved %": pct,
        **{f"{label} %": pct for label in selected},
        "Avg Ping Interval (min)": num,
        "Median Ping Interval (min)": num,
        "Avg Pings / Shipment": num,
        "Median Pings / Shipment": num,
    },
)

buf = io.BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as w:
    show.to_excel(w, index=False, sheet_name="Summary")
st.download_button(
    "Download table as Excel",
    buf.getvalue(),
    file_name="milestone_completeness_summary.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
