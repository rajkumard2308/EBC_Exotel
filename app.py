import re
import html
import io
import zipfile
import requests
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st
import plotly.express as px

from main import fetch_calls_for_range


# ============================================================
# PAGE CONFIGURATION
# ============================================================
st.set_page_config(
    page_title="EBC Exotel Call Analytics",
    page_icon="📞",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    [data-testid="stMainBlockContainer"] {
        max-width: 100%;
        padding: 0.8rem 1.35rem 0.8rem 1.35rem;
    }
    section[data-testid="stSidebar"] {
        width: 265px !important;
        min-width: 265px !important;
    }
    div[data-testid="stMetric"] {
        background: var(--secondary-background-color);
        padding: 10px 12px;
        border-radius: 10px;
        border: 1px solid var(--border-color);
    }
    div[data-testid="stMetricValue"] {
        font-size: 24px;
        font-weight: 700;
    }
    div[data-testid="stPlotlyChart"] {
        margin-top: -0.2rem;
        margin-bottom: -0.35rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# HELPERS
# ============================================================
def find_column(frame, candidates):
    lookup = {str(col).strip().lower(): col for col in frame.columns}
    for candidate in candidates:
        if candidate.lower() in lookup:
            return lookup[candidate.lower()]
    return None


def clean_text(value, default=""):
    if pd.isna(value):
        return default
    return str(value).strip()


def normalize_phone(value):
    if pd.isna(value):
        return ""
    digits = re.sub(r"\D", "", str(value))
    # Ignore empty values and obvious placeholders.
    if not digits or set(digits) <= {"0"}:
        return ""
    return digits


def duration_to_seconds(value):
    if pd.isna(value):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    value = str(value).strip()
    if not value:
        return 0
    try:
        if re.fullmatch(r"\d+(\.\d+)?", value):
            return max(0, int(float(value)))
        parts = value.split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(float(parts[2]))
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(float(parts[1]))
    except (TypeError, ValueError):
        pass
    return 0


def format_duration(seconds):
    seconds = int(seconds or 0)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def classify_direction(value):
    value = clean_text(value).lower().replace("_", "-")
    if any(word in value for word in ("inbound", "incoming", "in-bound")):
        return "Incoming"
    if any(word in value for word in ("outbound", "outgoing", "out-bound")):
        return "Outgoing"
    return "Unknown"


def classify_status(value):
    value = clean_text(value).lower().replace("_", "-")
    mapping = {
        "completed": "Answered",
        "answered": "Answered",
        "connected": "Answered",
        "success": "Answered",
        "no-answer": "No Answer",
        "no answer": "No Answer",
        "busy": "Busy",
        "failed": "Failed",
        "failure": "Failed",
        "canceled": "Cancelled",
        "cancelled": "Cancelled",
        "ringing": "Ringing",
        "in-progress": "In Progress",
        "in progress": "In Progress",
    }
    return mapping.get(value, value.title() if value else "Unknown")


def prepare_data(raw_frame):
    """Map Exotel's raw call fields into consistent dashboard columns."""
    if raw_frame is None or raw_frame.empty:
        return pd.DataFrame()

    frame = raw_frame.copy()

    direction_col = find_column(frame, ["Direction", "CallDirection"])
    status_col = find_column(frame, ["Status", "CallStatus"])
    duration_col = find_column(frame, ["Duration", "CallDuration", "ConversationDuration"])
    date_col = find_column(frame, ["DateCreated", "StartTime", "Date", "CreatedAt", "StartTime"])
    from_col = find_column(frame, ["From", "Caller", "FromNumber", "CustomerNumber", "Customer Number"])
    to_col = find_column(frame, ["To", "Called", "ToNumber", "DestinationNumber", "Destination Number"])
    sid_col = find_column(frame, ["Sid", "CallSid", "Call SID", "CallId", "Call ID"])
    agent_col = find_column(frame, ["Agent", "AgentName", "Agent Name", "UserName", "Legs"])
    recording_col = find_column(frame, ["RecordingUrl", "RecordingURL", "Recording", "Recording Url"])

    frame["Call Type"] = frame[direction_col].apply(classify_direction) if direction_col else "Unknown"
    frame["Call Status"] = frame[status_col].apply(classify_status) if status_col else "Unknown"
    frame["Duration Seconds"] = frame[duration_col].apply(duration_to_seconds) if duration_col else 0
    frame["Duration"] = frame["Duration Seconds"].apply(format_duration)

    if date_col:
        parsed = pd.to_datetime(frame[date_col], errors="coerce", utc=True)
        # Exotel timestamps are displayed in India local time.
        frame["Call Date"] = parsed.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    else:
        frame["Call Date"] = pd.NaT

    frame["Customer Number"] = frame[from_col].apply(clean_text) if from_col else ""
    frame["Destination Number"] = frame[to_col].apply(clean_text) if to_col else ""
    frame["Call ID"] = frame[sid_col].apply(clean_text) if sid_col else ""
    frame["Agent"] = frame[agent_col].apply(lambda x: clean_text(x, "Unknown")) if agent_col else "Not Available"
    frame["Recording URL"] = frame[recording_col].apply(clean_text) if recording_col else ""
    frame["Connected"] = frame["Call Status"].eq("Answered")

    # Remove duplicate Exotel call SIDs, while retaining records without a SID.
    if frame["Call ID"].astype(str).str.strip().ne("").any():
        has_id = frame["Call ID"].astype(str).str.strip().ne("")
        identified = frame.loc[has_id].drop_duplicates(subset=["Call ID"], keep="last")
        unidentified = frame.loc[~has_id]
        frame = pd.concat([identified, unidentified], ignore_index=True)

    return frame


def build_recordings_zip(frame):
    """Download every available recording URL into one in-memory ZIP archive."""
    api_key = str(st.secrets.get("EXOTEL_API_KEY", "")).strip()
    api_token = str(st.secrets.get("EXOTEL_API_TOKEN", "")).strip()
    if not api_key or not api_token:
        raise RuntimeError("EXOTEL_API_KEY / EXOTEL_API_TOKEN are missing from Streamlit secrets.")

    recording_rows = frame[
        frame["Recording URL"].fillna("").astype(str).str.startswith(("https://", "http://"))
    ].copy()
    if recording_rows.empty:
        return None, 0, 0, ["No recording URLs were found for the selected date range."]

    memory_file = io.BytesIO()
    downloaded = 0
    failed = 0
    report = []
    used_names = set()
    session = requests.Session()
    session.auth = (api_key, api_token)
    session.headers.update({"Accept": "*/*", "User-Agent": "EBC-Exotel-Analytics"})

    with zipfile.ZipFile(memory_file, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, row in recording_rows.iterrows():
            url = str(row["Recording URL"]).strip()
            call_id = re.sub(r"[^A-Za-z0-9_-]+", "_", str(row.get("Call ID", "call")))[:70]
            phone = re.sub(r"[^0-9]+", "", str(row.get("Customer Number", ""))) or "unknown_number"
            call_date = row.get("Call Date")
            date_part = call_date.strftime("%Y%m%d_%H%M%S") if pd.notna(call_date) else f"record_{index}"
            base_name = f"{date_part}_{phone}_{call_id or index}"
            file_name = f"{base_name}.mp3"
            suffix = 1
            while file_name.lower() in used_names:
                file_name = f"{base_name}_{suffix}.mp3"
                suffix += 1
            used_names.add(file_name.lower())

            try:
                response = session.get(url, timeout=(15, 90), allow_redirects=True)
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "").lower()
                content = response.content
                if not content or "text/html" in content_type or content.lstrip().lower().startswith((b"<!doctype html", b"<html")):
                    raise ValueError("The URL did not return an audio file (possibly an access/login page).")

                # Preserve a useful extension if Exotel provides one.
                if "wav" in content_type or url.lower().split("?")[0].endswith(".wav"):
                    file_name = file_name[:-4] + ".wav"
                elif "mpeg" in content_type or "mp3" in content_type or url.lower().split("?")[0].endswith(".mp3"):
                    pass
                elif "ogg" in content_type:
                    file_name = file_name[:-4] + ".ogg"
                elif "mp4" in content_type:
                    file_name = file_name[:-4] + ".mp4"

                archive.writestr(file_name, content)
                downloaded += 1
                report.append(f"Downloaded: {file_name}")
            except Exception as exc:
                failed += 1
                report.append(f"FAILED | {base_name} | {type(exc).__name__}: {exc}")

        archive.writestr("download_report.txt", "\n".join(report))

    memory_file.seek(0)
    return memory_file.getvalue(), downloaded, failed, report


def calculate_unique_calls(frame, selected_call_types):
    """
    Unique numbers:
      - Incoming selected: distinct Customer Number values from incoming calls.
      - Outgoing selected: distinct Destination Number values from outgoing calls.
      - Both selected: union of both number sets, counted once across directions.
    """
    unique_numbers = set()

    if "Incoming" in selected_call_types:
        values = frame.loc[frame["Call Type"].eq("Incoming"), "Customer Number"]
        unique_numbers.update(n for n in values.map(normalize_phone) if n)

    if "Outgoing" in selected_call_types:
        values = frame.loc[frame["Call Type"].eq("Outgoing"), "Destination Number"]
        unique_numbers.update(n for n in values.map(normalize_phone) if n)

    return len(unique_numbers)


def render_html_table(frame, recording_column=None, max_height=520):
    if frame.empty:
        st.info("No records to display.")
        return

    headers = "".join(f"<th>{html.escape(str(col))}</th>" for col in frame.columns)
    rows = []

    for _, row in frame.iterrows():
        cells = []
        for col in frame.columns:
            value = row[col]
            if pd.isna(value) or str(value).strip() == "":
                cell = "—"
            elif recording_column and col == recording_column and str(value).startswith(("https://", "http://")):
                url = html.escape(str(value), quote=True)
                cell = f'<a href="{url}" target="_blank" rel="noopener noreferrer">Listen to Recording</a>'
            else:
                cell = html.escape(str(value))
            cells.append(f"<td>{cell}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")

    table = f"""
    <div style="width:100%;max-height:{int(max_height)}px;overflow:auto;
                border:1px solid rgba(128,128,128,.35);border-radius:8px;">
      <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <thead><tr>{headers}</tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    <style>
      table th {{position:sticky;top:0;z-index:1;background:#252833;color:white;
                 text-align:left;padding:10px;border-bottom:1px solid #555;white-space:nowrap;}}
      table td {{padding:9px 10px;border-bottom:1px solid rgba(128,128,128,.22);
                 white-space:nowrap;}}
      table tr:hover td {{background:rgba(128,128,128,.10);}}
      table a {{color:#69b7ff;text-decoration:none;}}
    </style>
    """
    st.markdown(table, unsafe_allow_html=True)


# ============================================================
# HEADER
# ============================================================
st.title("📞 EBC Exotel Call Analytics")
st.caption("Everest Base Camp | Exotel Call Monitoring & Analytics")
st.divider()


# ============================================================
# DATE FILTER FIRST, THEN FETCH FROM LIVE EXOTEL API
# ============================================================
st.sidebar.title("🔎 Call Filters")
st.sidebar.subheader("📅 Date Filters")

today = date.today()
date_option = st.sidebar.selectbox(
    "Select Date Range",
    ["Today", "Last 7 Days", "Last 30 Days", "Last 90 Days", "Custom Date Range"],
    index=0,
)

if date_option == "Today":
    start_date = end_date = today
elif date_option == "Last 7 Days":
    start_date, end_date = today - timedelta(days=6), today
elif date_option == "Last 30 Days":
    start_date, end_date = today - timedelta(days=29), today
elif date_option == "Last 90 Days":
    start_date, end_date = today - timedelta(days=89), today
else:
    selected_range = st.sidebar.date_input(
        "Choose Date Range",
        value=(today, today),
        min_value=date(2020, 1, 1),
        max_value=today,
        format="YYYY/MM/DD",
    )
    if isinstance(selected_range, (tuple, list)):
        if len(selected_range) == 2:
            start_date, end_date = selected_range
        elif len(selected_range) == 1:
            start_date = end_date = selected_range[0]
        else:
            start_date = end_date = today
    else:
        start_date = end_date = selected_range

if end_date < start_date:
    st.sidebar.error("End date cannot be earlier than start date.")
    st.stop()

st.sidebar.caption(f"Selected: {start_date} to {end_date}")

# The API call is made after the date selection. The cache avoids repeat
# API requests during unrelated Streamlit reruns; changing dates changes the key.
@st.cache_data(ttl=300, show_spinner=False)
def get_live_calls(start, end):
    return fetch_calls_for_range(start, end)


try:
    with st.spinner(f"Fetching Exotel calls from {start_date} to {end_date}..."):
        raw_df = get_live_calls(start_date, end_date)
except Exception as exc:
    st.error(f"Could not fetch calls from Exotel: {type(exc).__name__}: {exc}")
    st.info("Check .streamlit/secrets.toml and the Exotel API credentials.")
    st.stop()

df = prepare_data(raw_df)

if df.empty:
    st.warning(f"No Exotel call records were returned for {start_date} to {end_date}.")
    st.caption("The dashboard queried Exotel directly; it does not depend on old CSV files.")
    st.stop()

# Keep calls inside the exact selected calendar dates.
df = df[df["Call Date"].notna()].copy()
df = df[
    (df["Call Date"].dt.date >= start_date)
    & (df["Call Date"].dt.date <= end_date)
].copy()

if df.empty:
    st.warning("Exotel returned records, but none had a valid call date inside the selected range.")
    st.stop()


# ============================================================
# SIDEBAR CALL FILTERS (OPTIONS FROM FETCHED DATA)
# ============================================================
st.sidebar.divider()
st.sidebar.subheader("📞 Call Type")
direction_options = [x for x in ["Incoming", "Outgoing", "Unknown"] if x in df["Call Type"].unique()]
selected_directions = st.sidebar.multiselect(
    "Select Call Type", direction_options, default=direction_options
)

st.sidebar.subheader("📊 Call Status")
status_options = sorted(df["Call Status"].dropna().unique().tolist())
selected_status = st.sidebar.multiselect("Select Call Status", status_options, default=status_options)

st.sidebar.subheader("⏱️ Call Duration")
max_duration = max(1, int(df["Duration Seconds"].max()))
duration_range = st.sidebar.slider(
    "Duration (Seconds)", 0, max_duration, (0, max_duration), step=1
)
st.sidebar.caption(f"{format_duration(duration_range[0])} - {format_duration(duration_range[1])}")

st.sidebar.subheader("👤 Agent")
agent_options = sorted(df["Agent"].dropna().unique().tolist())
selected_agents = st.sidebar.multiselect("Select Agent", agent_options, default=agent_options)

st.sidebar.subheader("🔍 Customer Search")
search_number = st.sidebar.text_input("Search Phone Number", placeholder="Enter customer/destination number")


# ============================================================
# APPLY FILTERS
# ============================================================
filtered = df.copy()
filtered = filtered[filtered["Call Type"].isin(selected_directions)]
filtered = filtered[filtered["Call Status"].isin(selected_status)]
filtered = filtered[filtered["Duration Seconds"].between(*duration_range)]
filtered = filtered[filtered["Agent"].isin(selected_agents)]

if search_number.strip():
    needle = search_number.strip()
    filtered = filtered[
        filtered["Customer Number"].str.contains(needle, case=False, na=False, regex=False)
        | filtered["Destination Number"].str.contains(needle, case=False, na=False, regex=False)
    ]


# ============================================================
# CALL OVERVIEW
# ============================================================
st.subheader("📊 Call Overview")

total_calls = len(filtered)
answered_calls = int(filtered["Connected"].sum())
no_answer_calls = int(filtered["Call Status"].eq("No Answer").sum())
busy_calls = int(filtered["Call Status"].eq("Busy").sum())
failed_calls = int(filtered["Call Status"].eq("Failed").sum())
cancelled_calls = int(filtered["Call Status"].eq("Cancelled").sum())
talk_seconds = int(filtered.loc[filtered["Connected"], "Duration Seconds"].sum())
average_talk = talk_seconds / answered_calls if answered_calls else 0
connected_rate = answered_calls / total_calls * 100 if total_calls else 0
unique_calls = calculate_unique_calls(filtered, selected_directions)

metrics = [
    ("Total Calls", f"{total_calls:,}"),
    ("Answered Calls", f"{answered_calls:,}"),
    ("No Answer", f"{no_answer_calls:,}"),
    ("Busy Calls", f"{busy_calls:,}"),
    ("Failed Calls", f"{failed_calls:,}"),
    ("Cancelled Calls", f"{cancelled_calls:,}"),
    ("Connected Rate", f"{connected_rate:.1f}%"),
    ("Average Talk Time", format_duration(average_talk)),
    ("Total Talk Time", format_duration(talk_seconds)),
    ("Unique Calls", f"{unique_calls:,}"),
]

for row_start in (0, 5):
    cols = st.columns(5)
    for col, (label, value) in zip(cols, metrics[row_start:row_start + 5]):
        col.metric(label, value)

st.divider()


# ============================================================
# CALL ANALYTICS
# ============================================================
st.subheader("📈 Call Analytics")
left, right = st.columns(2)

with left:
    st.markdown("#### Incoming vs Outgoing Calls")
    direction_counts = filtered["Call Type"].value_counts().rename_axis("Call Direction").reset_index(name="Number of Calls")
    if not direction_counts.empty:
        fig = px.bar(direction_counts, x="Call Direction", y="Number of Calls",
                     text="Number of Calls", title="Calls by Direction")
        fig.update_layout(template="plotly_dark", height=320)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No call data available.")

with right:
    st.markdown("#### Call Status Breakdown")
    status_counts = filtered["Call Status"].value_counts().rename_axis("Call Status").reset_index(name="Count")
    if not status_counts.empty:
        fig = px.bar(status_counts, x="Call Status", y="Count", text="Count",
                     title="Calls by Status")
        fig.update_layout(template="plotly_dark", height=320)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No call data available.")


# ============================================================
# DURATION DISTRIBUTION
# ============================================================
st.markdown("#### ⏱️ Call Duration Distribution")
if not filtered.empty:
    bins = [-1, 30, 60, 120, 300, 600, 1200, float("inf")]
    labels = ["0-30 sec", "31-60 sec", "1-2 min", "2-5 min", "5-10 min", "10-20 min", "20+ min"]
    duration_frame = filtered.copy()
    duration_frame["Duration Category"] = pd.cut(
        duration_frame["Duration Seconds"], bins=bins, labels=labels
    )
    duration_counts = duration_frame["Duration Category"].value_counts().reindex(labels, fill_value=0)
    duration_plot = duration_counts.rename_axis("Duration").reset_index(name="Count")
    fig = px.bar(duration_plot, x="Duration", y="Count", text="Count",
                 title="Call Duration Distribution")
    fig.update_layout(template="plotly_dark", height=320)
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("No duration data available.")


# ============================================================
# DAILY TREND
# ============================================================
st.markdown("#### 📅 Daily Call Trend")
if not filtered.empty:
    daily = filtered.assign(Day=filtered["Call Date"].dt.date).groupby("Day").size().reset_index(name="Calls")
    fig = px.line(daily, x="Day", y="Calls", markers=True, title="Calls per Day")
    fig.update_layout(template="plotly_dark", height=320)
    st.plotly_chart(fig, use_container_width=True)


# ============================================================
# AGENT PERFORMANCE
# ============================================================
st.divider()
st.subheader("👤 Agent Performance")
if not filtered.empty:
    agent = filtered.groupby("Agent").agg(
        **{
            "Total Calls": ("Call ID", "size"),
            "Answered Calls": ("Connected", "sum"),
            "Total Talk Seconds": ("Duration Seconds", lambda s: int(s[filtered.loc[s.index, "Connected"]].sum())),
        }
    ).reset_index()
    agent["Average Talk Time"] = agent.apply(
        lambda row: format_duration(
            row["Total Talk Seconds"] / row["Answered Calls"]
            if row["Answered Calls"] else 0
        ), axis=1
    )
    agent["Total Talk Time"] = agent["Total Talk Seconds"].apply(format_duration)
    agent = agent.drop(columns=["Total Talk Seconds"])
    render_html_table(agent, max_height=280)
else:
    st.info("No agent performance data available.")


# ============================================================
# DETAILED CALL RECORDS + CSV EXPORT
# ============================================================
st.divider()
st.subheader("📋 Detailed Call Records")
st.caption(f"Showing {len(filtered):,} calls after applying filters.")

if not filtered.empty:
    display_columns = [
        "Call ID", "Call Date", "Call Type", "Customer Number",
        "Destination Number", "Call Status", "Duration", "Agent", "Recording URL"
    ]
    display = filtered[display_columns].sort_values("Call Date", ascending=False).copy()
    display["Call Date"] = display["Call Date"].dt.strftime("%Y-%m-%d %H:%M:%S")
    render_html_table(display, recording_column="Recording URL", max_height=560)

    csv_data = filtered.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "⬇️ Download Filtered Calls CSV",
        data=csv_data,
        file_name=f"EBC_Exotel_Calls_{start_date}_{end_date}.csv",
        mime="text/csv",
    )

    st.markdown("#### 🎧 Download Call Recordings")
    st.caption(
        "Creates one ZIP containing every recording available in the selected API date range "
        "(not only the calls currently visible after sidebar filters)."
    )
    if st.button("📦 Prepare All Call Recordings ZIP", type="primary"):
        try:
            with st.spinner("Downloading call recordings and creating ZIP. This may take a few minutes..."):
                zip_bytes, downloaded_count, failed_count, zip_report = build_recordings_zip(df)
            if zip_bytes is None:
                st.warning("No recording URLs were available for this date range.")
            else:
                st.session_state["recordings_zip_bytes"] = zip_bytes
                st.session_state["recordings_zip_name"] = f"EBC_Call_Recordings_{start_date}_{end_date}.zip"
                st.session_state["recordings_zip_summary"] = (downloaded_count, failed_count)
        except Exception as exc:
            st.error(f"Could not create recordings ZIP: {type(exc).__name__}: {exc}")

    if st.session_state.get("recordings_zip_bytes"):
        downloaded_count, failed_count = st.session_state.get("recordings_zip_summary", (0, 0))
        st.success(f"ZIP ready: {downloaded_count} recording(s) downloaded; {failed_count} failed. See download_report.txt inside the ZIP.")
        st.download_button(
            "⬇️ Download All Call Recordings ZIP",
            data=st.session_state["recordings_zip_bytes"],
            file_name=st.session_state["recordings_zip_name"],
            mime="application/zip",
            key="download_all_recordings_zip",
        )
else:
    st.warning("No calls found for the selected filters.")

st.divider()
st.caption(
    f"EBC Exotel Analytics | API date range: {start_date} to {end_date} | "
    f"Fetched records: {len(df):,} | Filtered records: {len(filtered):,}"
)
