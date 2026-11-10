import os
import glob
import re
import html
import pandas as pd
import streamlit as st
import plotly.express as px
from main import fetch_calls_for_range
from datetime import date, timedelta


# =====================================================
# 1. PAGE CONFIGURATION
# =====================================================

st.set_page_config(
    page_title="EBC Exotel Call Analytics",
    page_icon="📞",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
/* Main content: reduce wide outer margins and vertical whitespace */
[data-testid="stMainBlockContainer"] {
    max-width: 100%;
    padding: 0.8rem 1.35rem 0.8rem 1.35rem;
}

[data-testid="stMainBlockContainer"] [data-testid="stVerticalBlock"] {
    gap: 0.55rem;
}

/* Sidebar: slightly narrower with compact controls */
section[data-testid="stSidebar"] {
    width: 265px !important;
    min-width: 265px !important;
}

section[data-testid="stSidebar"] [data-testid="stSidebarContent"] {
    padding: 0.85rem 0.8rem 1rem 0.8rem;
}

section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
    gap: 0.45rem;
}

section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3 {
    margin-top: 0.35rem;
    margin-bottom: 0.35rem;
}

section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] {
    margin-bottom: 0.15rem;
}

section[data-testid="stSidebar"] hr,
hr {
    margin: 0.5rem 0 !important;
}

/* Compact headings */
h1, h2, h3, h4 {
    margin-top: 0.35rem !important;
    margin-bottom: 0.45rem !important;
}

/* Compact metric cards */
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

/* Reduce extra space around Plotly charts */
div[data-testid="stPlotlyChart"] {
    margin-top: -0.2rem;
    margin-bottom: -0.35rem;
}
</style>
""", unsafe_allow_html=True)


# =====================================================
# 2. HELPER FUNCTIONS
# =====================================================

def find_column(df, possible_names):

    lookup = {
        str(col).strip().lower(): col
        for col in df.columns
    }

    for name in possible_names:
        if name.lower() in lookup:
            return lookup[name.lower()]

    return None


def format_duration(seconds):

    if pd.isna(seconds):
        return "—"

    seconds = int(seconds)

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours:
        return f"{hours}h {minutes}m {secs}s"

    if minutes:
        return f"{minutes}m {secs}s"

    return f"{secs}s"



def render_html_table(dataframe, link_columns=None, max_height=520):
    """Render a pandas DataFrame as HTML without Streamlit's PyArrow dataframe API."""
    link_columns = link_columns or {}
    headers = "".join(
        f"<th>{html.escape(str(link_columns.get(col, col)))}</th>"
        for col in dataframe.columns
    )

    rows = []
    for _, row in dataframe.iterrows():
        cells = []
        for col in dataframe.columns:
            value = row[col]

            if pd.isna(value):
                display_value = "—"
                cell_html = html.escape(display_value)
            else:
                display_value = str(value)
                if col in link_columns and display_value.strip().lower().startswith(("https://", "http://")):
                    safe_url = html.escape(display_value.strip(), quote=True)
                    cell_html = (
                        f'<a href="{safe_url}" target="_blank" '
                        f'rel="noopener noreferrer">Listen to Recording</a>'
                    )
                else:
                    cell_html = html.escape(display_value)

            cells.append(f"<td>{cell_html}</td>")

        rows.append("<tr>" + "".join(cells) + "</tr>")

    table_html = f"""
    <div style="width:100%; max-height:{int(max_height)}px; overflow:auto;
                border:1px solid rgba(128,128,128,.35); border-radius:8px;">
      <table style="width:100%; border-collapse:collapse; font-size:13px;">
        <thead><tr>{headers}</tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    <style>
      table th {{
        position:sticky; top:0; z-index:1;
        background:#252833; color:#ffffff; text-align:left;
        padding:10px; border-bottom:1px solid #555;
        white-space:nowrap;
      }}
      table td {{
        padding:9px 10px; border-bottom:1px solid rgba(128,128,128,.22);
        color:inherit; white-space:nowrap;
      }}
      table tr:hover td {{ background:rgba(128,128,128,.10); }}
      table a {{ color:#69b7ff; text-decoration:none; }}
      table a:hover {{ text-decoration:underline; }}
    </style>
    """
    st.markdown(table_html, unsafe_allow_html=True)

def duration_to_seconds(value):

    if pd.isna(value):
        return 0

    try:
        if isinstance(value, (int, float)):
            return max(0, int(float(value)))

        value = str(value).strip()

        if not value:
            return 0

        if re.fullmatch(r"\d+(\.\d+)?", value):
            return max(0, int(float(value)))

        parts = value.split(":")

        if len(parts) == 3:
            return (
                int(parts[0]) * 3600
                + int(parts[1]) * 60
                + int(float(parts[2]))
            )

        if len(parts) == 2:
            return (
                int(parts[0]) * 60
                + int(float(parts[1]))
            )

    except (ValueError, TypeError):
        pass

    return 0


def classify_direction(value):

    value = str(value).strip().lower()

    if "inbound" in value or "incoming" in value:
        return "Incoming"

    if "outbound" in value or "outgoing" in value:
        return "Outgoing"

    return "Unknown"


def classify_status(value):

    value = str(value).strip().lower()

    status_mapping = {
        "completed": "Answered",
        "answered": "Answered",
        "connected": "Answered",

        "no-answer": "No Answer",
        "no_answer": "No Answer",
        "no answer": "No Answer",

        "busy": "Busy",

        "failed": "Failed",
        "failure": "Failed",

        "canceled": "Cancelled",
        "cancelled": "Cancelled",

        "ringing": "Ringing",
        "in-progress": "In Progress",
        "in progress": "In Progress"
    }

    return status_mapping.get(
        value,
        value.title() if value else "Unknown"
    )


# =====================================================
# 3. LOAD CALL DATA
# =====================================================

def load_calls():

    files = glob.glob(
        os.path.join("data", "calls*.csv")
    )

    if not files:
        return pd.DataFrame()

    frames = []

    for file in files:

        try:

            temp = pd.read_csv(file)

            temp["Source File"] = os.path.basename(file)

            frames.append(temp)

        except Exception as e:

            st.warning(
                f"Could not read {file}: {e}"
            )

    if not frames:
        return pd.DataFrame()

    df = pd.concat(
        frames,
        ignore_index=True
    )

    # Remove duplicate call records
    sid_col = find_column(
        df,
        ["Sid", "CallSid", "Call SID"]
    )

    if sid_col:

        df = df.drop_duplicates(
            subset=[sid_col],
            keep="last"
        )

    return df


# =====================================================
# 4. PREPARE CALL DATA
# =====================================================

def prepare_data(df):

    df = df.copy()

    direction_col = find_column(
        df,
        ["Direction", "CallDirection"]
    )

    status_col = find_column(
        df,
        ["Status", "CallStatus"]
    )

    duration_col = find_column(
        df,
        ["Duration", "CallDuration"]
    )

    date_col = find_column(
        df,
        [
            "DateCreated",
            "StartTime",
            "Date",
            "CreatedAt"
        ]
    )

    from_col = find_column(
        df,
        ["From", "Caller", "FromNumber"]
    )

    to_col = find_column(
        df,
        ["To", "Called", "ToNumber"]
    )

    sid_col = find_column(
        df,
        ["Sid", "CallSid", "Call SID"]
    )

    agent_col = find_column(
        df,
        [
            "Agent",
            "AgentName",
            "Agent Name",
            "UserName"
        ]
    )

    recording_col = find_column(
        df,
        [
            "RecordingUrl",
            "RecordingURL",
            "Recording",
            "Recording Url"
        ]
    )

    # Direction
    if direction_col:

        df["Call Type"] = (
            df[direction_col].apply(classify_direction)
        )

    else:

        df["Call Type"] = "Unknown"

    # Status
    if status_col:

        df["Call Status"] = (
            df[status_col].apply(classify_status)
        )

    else:

        df["Call Status"] = "Unknown"

    # Duration
    if duration_col:

        df["Duration Seconds"] = (
            df[duration_col].apply(duration_to_seconds)
        )

    else:

        df["Duration Seconds"] = 0

    df["Duration"] = (
        df["Duration Seconds"].apply(format_duration)
    )

    # Date
    if date_col:

        parsed_dates = pd.to_datetime(
            df[date_col],
            errors="coerce",
            utc=True
        )

        df["Call Date"] = (
            parsed_dates
            .dt.tz_convert("Asia/Kolkata")
            .dt.tz_localize(None)
        )

    else:

        df["Call Date"] = pd.NaT

    # Customer number
    df["Customer Number"] = (
        df[from_col].fillna("").astype(str)
        if from_col
        else ""
    )

    # Destination number
    df["Destination Number"] = (
        df[to_col].fillna("").astype(str)
        if to_col
        else ""
    )

    # Call ID
    df["Call ID"] = (
        df[sid_col].fillna("").astype(str)
        if sid_col
        else ""
    )

    # Agent
    df["Agent"] = (
        df[agent_col].fillna("Unknown").astype(str)
        if agent_col
        else "Not Available"
    )

    # Recording
    df["Recording URL"] = (
        df[recording_col].fillna("").astype(str)
        if recording_col
        else ""
    )

    # Answered calls
    df["Connected"] = (
        df["Call Status"] == "Answered"
    )

    return df


# =====================================================
# 5. HEADER
# =====================================================

st.title("📞 EBC Exotel Call Analytics")

st.caption(
    "Everest Base Camp | Exotel Call Monitoring & Analytics"
)

st.divider()

# =====================================================
# 7. SIDEBAR FILTERS
# =====================================================

st.sidebar.title("🔎 Call Filters")

st.sidebar.divider()

# ---------------- DATE FILTER ----------------

st.sidebar.subheader("📅 Date Filters")

today = date.today()

date_option = st.sidebar.selectbox(
    "Select Date Range",
    [
        "Today",
        "Last 7 Days",
        "Last 30 Days",
        "Last 90 Days",
        "Custom Date Range"
    ],
    index=0
)

if date_option == "Today":
    start_date = today
    end_date = today

elif date_option == "Last 7 Days":
    start_date = today - timedelta(days=6)
    end_date = today

elif date_option == "Last 30 Days":
    start_date = today - timedelta(days=29)
    end_date = today

elif date_option == "Last 90 Days":
    start_date = today - timedelta(days=89)
    end_date = today

else:
    custom_range = st.sidebar.date_input(
        "Choose Date Range",
        value=(today, today),
        min_value=date(2020, 1, 1),
        max_value=today
    )

    if isinstance(custom_range, (tuple, list)):
        if len(custom_range) == 2:
            start_date, end_date = custom_range
        elif len(custom_range) == 1:
            start_date = end_date = custom_range[0]
        else:
            start_date = end_date = today
    else:
        start_date = end_date = custom_range

st.sidebar.caption(
    f"Selected: {start_date} to {end_date}"
)

# Fetch directly from Exotel for the selected dates
@st.cache_data(ttl=300, show_spinner=False)
def get_exotel_data(start, end):
    return fetch_calls_for_range(start, end)

try:
    with st.spinner(
        f"Fetching Exotel calls from {start_date} to {end_date}..."
    ):
        raw_df = get_exotel_data(start_date, end_date)

except Exception as exc:
    st.error(f"Exotel API fetch failed: {exc}")
    st.stop()

if raw_df.empty:
    st.warning(
        f"No Exotel call records found from "
        f"{start_date} to {end_date}."
    )
    st.stop()

st.success(f"Fetched {len(raw_df):,} call records from Exotel.")

df = prepare_data(raw_df)

if df["Call Date"].dropna().empty:
    st.error("No valid call dates found in the Exotel response.")
    st.stop()


# ---------------- CALL TYPE ----------------

st.sidebar.subheader("📞 Call Type")

direction_options = sorted(
    df["Call Type"].dropna().unique().tolist()
)

selected_directions = st.sidebar.multiselect(
    "Select Call Type",
    direction_options,
    default=direction_options
)


# ---------------- CALL STATUS ----------------

st.sidebar.subheader("📊 Call Status")

status_options = sorted(
    df["Call Status"].dropna().unique().tolist()
)

selected_status = st.sidebar.multiselect(
    "Select Call Status",
    status_options,
    default=status_options
)


# ---------------- DURATION FILTER ----------------

st.sidebar.subheader("⏱️ Call Duration")

max_duration = int(
    df["Duration Seconds"].max()
)

duration_range = st.sidebar.slider(
    "Duration (Seconds)",

    min_value=0,
    max_value=max(1, max_duration),

    value=(0, max(1, max_duration)),

    step=1
)

st.sidebar.caption(
    f"{format_duration(duration_range[0])} - "
    f"{format_duration(duration_range[1])}"
)


# ---------------- AGENT FILTER ----------------

st.sidebar.subheader("👤 Agent")

agent_options = sorted(
    df["Agent"].dropna().unique().tolist()
)

selected_agents = st.sidebar.multiselect(
    "Select Agent",
    agent_options,
    default=agent_options
)


# ---------------- PHONE NUMBER SEARCH ----------------

st.sidebar.subheader("🔍 Customer Search")

search_number = st.sidebar.text_input(
    "Search Phone Number",
    placeholder="Enter customer number"
)


# =====================================================
# 8. APPLY ALL FILTERS
# =====================================================

filtered = df.copy()

# Date filter
filtered = filtered[
    (filtered["Call Date"].dt.date >= start_date)
    & (filtered["Call Date"].dt.date <= end_date)
]

# Call type
filtered = filtered[
    filtered["Call Type"].isin(selected_directions)
]

# Call status
filtered = filtered[
    filtered["Call Status"].isin(selected_status)
]

# Duration
filtered = filtered[
    filtered["Duration Seconds"].between(
        duration_range[0],
        duration_range[1]
    )
]

# Agent
filtered = filtered[
    filtered["Agent"].isin(selected_agents)
]

# Customer number search
if search_number.strip():

    filtered = filtered[
        filtered["Customer Number"].str.contains(
            search_number.strip(),
            case=False,
            na=False,
            regex=False
        )
        |
        filtered["Destination Number"].str.contains(
            search_number.strip(),
            case=False,
            na=False,
            regex=False
        )
    ]


# =====================================================
# 9. CALL OVERVIEW METRICS
# =====================================================

st.subheader("📊 Call Overview")

total_calls = len(filtered)

answered_calls = int(
    filtered["Connected"].sum()
)

no_answer_calls = int(
    filtered["Call Status"].eq("No Answer").sum()
)

busy_calls = int(
    filtered["Call Status"].eq("Busy").sum()
)

failed_calls = int(
    filtered["Call Status"].eq("Failed").sum()
)

cancelled_calls = int(
    filtered["Call Status"].eq("Cancelled").sum()
)

total_talk_seconds = int(
    filtered.loc[
        filtered["Connected"],
        "Duration Seconds"
    ].sum()
)

average_duration = (
    total_talk_seconds / answered_calls
    if answered_calls
    else 0
)

connected_rate = (
    answered_calls / total_calls * 100
    if total_calls
    else 0
)


# Call Overview: 2 rows × 5 columns
overview_metrics = [
    ("Total Calls", f"{total_calls:,}"),
    ("Answered Calls", f"{answered_calls:,}"),
    ("No Answer", f"{no_answer_calls:,}"),
    ("Busy Calls", f"{busy_calls:,}"),
    ("Failed Calls", f"{failed_calls:,}"),
    ("Cancelled Calls", f"{cancelled_calls:,}"),
    ("Connected Rate", f"{connected_rate:.1f}%"),
    ("Average Talk Time", format_duration(average_duration)),
    ("Total Talk Time", format_duration(total_talk_seconds)),
    ("Total Incoming Calls", f"{(filtered['Call Type'] == 'Incoming').sum():,}"),
]

# First row: metrics 1–5
metric_cols = st.columns(5)
for col, (label, value) in zip(metric_cols, overview_metrics[:5]):
    with col:
        st.metric(label, value)

# Second row: metrics 6–10
metric_cols = st.columns(5)
for col, (label, value) in zip(metric_cols, overview_metrics[5:]):
    with col:
        st.metric(label, value)


st.divider()


# =====================================================
# 10. CALL TYPE ANALYSIS
# =====================================================

st.subheader("📈 Call Analytics")

col1, col2 = st.columns(2)


with col1:

    st.markdown("#### Incoming vs Outgoing Calls")

    direction_counts = (
        filtered["Call Type"]
        .value_counts()
    )

    if not direction_counts.empty:
        direction_df = direction_counts.rename_axis("Call Direction").reset_index(name="Number of Calls")
        fig = px.bar(
            direction_df,
            x="Call Direction",
            y="Number of Calls",
            title="Calls by Direction",
            text="Number of Calls"
        )
        fig.update_layout(
            template="plotly_dark",
            height=300,
            margin=dict(l=35, r=20, t=45, b=40)
        )

        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No call data available.")


with col2:

    st.markdown("#### Call Status Breakdown")

    status_counts = (
        filtered["Call Status"]
        .value_counts()
    )

    if not status_counts.empty:
        status_df = status_counts.reset_index()
        status_df.columns = ["Call Status", "Count"]

        fig = px.bar(
            status_df,
            x="Call Status",
            y="Count",
            title="Calls by Status",
            text="Count"
        )

        fig.update_layout(
            xaxis_title="Call Status",
            yaxis_title="Number of Calls",
            template="plotly_dark",
            height=300,
            margin=dict(l=35, r=20, t=45, b=40)
        )

        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No call data available.")

# =====================================================
# 11. CALL DURATION ANALYSIS
# =====================================================

st.markdown("#### ⏱️ Call Duration Distribution")

if not filtered.empty:

    duration_labels = [
        "0-30 sec",
        "30-60 sec",
        "1-2 min",
        "2-5 min",
        "5-10 min",
        "10-20 min",
        "20+ min"
    ]

    def duration_category(seconds):

        if seconds <= 30:
            return "0-30 sec"

        elif seconds <= 60:
            return "30-60 sec"

        elif seconds <= 120:
            return "1-2 min"

        elif seconds <= 300:
            return "2-5 min"

        elif seconds <= 600:
            return "5-10 min"

        elif seconds <= 1200:
            return "10-20 min"

        return "20+ min"

    filtered_duration = filtered.copy()

    filtered_duration["Duration Category"] = (
        filtered_duration["Duration Seconds"]
        .apply(duration_category)
    )

    duration_distribution = (
        filtered_duration["Duration Category"]
        .value_counts()
        .reindex(duration_labels, fill_value=0)
    )

    duration_df = duration_distribution.reset_index()
    duration_df.columns = ["Duration", "Count"]

    fig = px.bar(
        duration_df,
        x="Duration",
        y="Count",
        title="Call Duration Distribution",
        text="Count"
    )

    fig.update_layout(
        xaxis_title="Call Duration",
        yaxis_title="Number of Calls",
        template="plotly_dark",
        height=300,
        margin=dict(l=35, r=20, t=45, b=40)
    )

    st.plotly_chart(fig, use_container_width=True)

else:

    st.info("No duration data available.")


# =====================================================
# 12. AGENT PERFORMANCE
# =====================================================

st.divider()

st.subheader("👤 Agent Performance")

if not filtered.empty:

    agent_data = (
        filtered
        .groupby("Agent")
        .agg(
            Total_Calls=("Call ID", "size"),

            Answered_Calls=("Connected", "sum"),

            Total_Talk_Seconds=(
                "Duration Seconds",
                lambda x: x[
                    filtered.loc[x.index, "Connected"]
                ].sum()
            )
        )
        .reset_index()
    )

    agent_data["Average Talk Time"] = (
        agent_data["Total_Talk_Seconds"]
        / agent_data["Answered_Calls"].replace(0, pd.NA)
    )

    agent_data["Average Talk Time"] = (
        agent_data["Average Talk Time"]
        .apply(format_duration)
    )

    agent_data = agent_data.rename(
        columns={
            "Total_Calls": "Total Calls",
            "Answered_Calls": "Answered Calls",
            "Total_Talk_Seconds": "Total Talk Time (Seconds)"
        }
    )

    render_html_table(agent_data, max_height=260)

else:

    st.info("No agent performance data available.")


# =====================================================
# 13. DETAILED CALL RECORDS
# =====================================================

st.divider()

st.subheader("📋 Detailed Call Records")

st.caption(
    f"Showing {len(filtered):,} calls after applying filters."
)


if not filtered.empty:

    display_cols = [
        "Call ID",
        "Call Date",
        "Call Type",
        "Customer Number",
        "Destination Number",
        "Call Status",
        "Duration",
        "Agent",
        "Recording URL"
    ]

    display_cols = [
        col for col in display_cols
        if col in filtered.columns
    ]

    display_df = filtered[display_cols].sort_values(
        "Call Date",
        ascending=False
    )

    render_html_table(
        display_df,
        link_columns={"Recording URL": "Call Recording"},
        max_height=560
    )

    # CSV download
    csv_data = filtered.to_csv(
        index=False
    ).encode("utf-8-sig")

    st.download_button(
        label="⬇️ Download Filtered Calls CSV",

        data=csv_data,

        file_name="EBC_Exotel_Filtered_Calls.csv",

        mime="text/csv"
    )

else:

    st.warning(
        "No calls found for the selected filters."
    )


# =====================================================
# 14. FOOTER
# =====================================================

st.divider()

st.caption(
    f"EBC Exotel Analytics | "
    f"Total loaded records: {len(df):,} | "
    f"Filtered records: {len(filtered):,} | "
    f"Source files: {len(glob.glob('data/calls*.csv'))}"
)
