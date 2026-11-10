
import os
import pandas as pd
from datetime import date, datetime, timedelta

from exotel_client import ExotelClient


def fetch_calls_for_range(start_date, end_date):
    """
    Fetch calls directly from Exotel for the selected date range.
    Returns a DataFrame for the Streamlit dashboard.
    """

    if isinstance(start_date, datetime):
        start_date = start_date.date()

    if isinstance(end_date, datetime):
        end_date = end_date.date()

    if end_date < start_date:
        raise ValueError("End date cannot be before start date.")

    client = ExotelClient()
    all_calls = []
    current = start_date

    while current <= end_date:

        # Fetch one calendar month at a time
        next_month = (
            current.replace(day=28) + timedelta(days=4)
        ).replace(day=1)

        chunk_end = min(
            next_month - timedelta(days=1),
            end_date
        )

        start = current.strftime("%Y-%m-%d 00:00:00")
        end = chunk_end.strftime("%Y-%m-%d 23:59:59")

        print(f"Fetching Exotel calls: {start} to {end}")

        calls = client.fetch_calls(start, end)
        all_calls.extend(calls)

        current = chunk_end + timedelta(days=1)

    if not all_calls:
        return pd.DataFrame()

    df = pd.DataFrame(all_calls)

    if "Sid" in df.columns:
        df = df.drop_duplicates(subset=["Sid"], keep="last")

    return df


def get_date_input():
    print("\n========== EBC EXOTEL CALL DATA ==========")

    while True:
        try:
            start_date = datetime.strptime(
                input("Enter Start Date (YYYY-MM-DD): ").strip(),
                "%Y-%m-%d"
            ).date()
            break
        except ValueError:
            print("Invalid date. Use YYYY-MM-DD.")

    while True:
        try:
            end_date = datetime.strptime(
                input("Enter End Date (YYYY-MM-DD): ").strip(),
                "%Y-%m-%d"
            ).date()

            if end_date < start_date:
                print("End date cannot be before start date.")
                continue

            break
        except ValueError:
            print("Invalid date. Use YYYY-MM-DD.")

    return start_date, end_date


def fetch_all_historical_calls():
    start_date, end_date = get_date_input()

    df = fetch_calls_for_range(start_date, end_date)

    if df.empty:
        print("No call records found.")
        return

    os.makedirs("data", exist_ok=True)

    filepath = os.path.join(
        "data",
        f"calls_{start_date}_{end_date}.csv"
    )

    df.to_csv(filepath, index=False, encoding="utf-8-sig")

    print("Total Unique Calls:", len(df))
    print("Saved File:", filepath)


if __name__ == "__main__":
    fetch_all_historical_calls()
