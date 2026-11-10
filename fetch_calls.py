
import os
import pandas as pd
from datetime import datetime, timedelta

from exotel_client import ExotelClient


def get_date_input():

    print("\n========== EBC EXOTEL CALL DATA ==========")

    while True:
        start_input = input(
            "Enter Start Date (YYYY-MM-DD): "
        ).strip()

        try:
            start_date = datetime.strptime(
                start_input, "%Y-%m-%d"
            ).date()
            break
        except ValueError:
            print("Invalid date. Use YYYY-MM-DD.")

    while True:
        end_input = input(
            "Enter End Date (YYYY-MM-DD): "
        ).strip()

        try:
            end_date = datetime.strptime(
                end_input, "%Y-%m-%d"
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

    client = ExotelClient()

    all_calls = []
    current = start_date

    while current <= end_date:

        # Maximum one calendar month per request
        next_month = (
            current.replace(day=28) + timedelta(days=4)
        ).replace(day=1)

        chunk_end = min(
            next_month - timedelta(days=1),
            end_date
        )

        start = current.strftime("%Y-%m-%d 00:00:00")
        end = chunk_end.strftime("%Y-%m-%d 23:59:59")

        print(f"\nFetching: {start} to {end}")

        calls = client.fetch_calls(start, end)

        print("Records fetched:", len(calls))

        all_calls.extend(calls)

        current = chunk_end + timedelta(days=1)

    if not all_calls:
        print("\nNo call records found.")
        return

    df = pd.DataFrame(all_calls)

    if "Sid" in df.columns:
        df = df.drop_duplicates(subset=["Sid"])

    os.makedirs("data", exist_ok=True)

    filename = (
        f"calls_{start_date}_{end_date}.csv"
    )

    filepath = os.path.join("data", filename)

    df.to_csv(
        filepath,
        index=False,
        encoding="utf-8-sig"
    )

    print("\n========== EXTRACTION COMPLETED ==========")
    print("Start Date:", start_date)
    print("End Date:", end_date)
    print("Total Unique Calls:", len(df))
    print("Saved File:", filepath)


if __name__ == "__main__":
    fetch_all_historical_calls()
