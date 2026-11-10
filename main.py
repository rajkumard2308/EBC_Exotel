"""
EBC Exotel Call Analytics - Main data fetch entry point.

Run this file to enter a date range, fetch calls from Exotel,
and save the results in the data/ folder for app.py.
"""

from fetch_calls import fetch_all_historical_calls


def main():
    try:
        fetch_all_historical_calls()
    except Exception as exc:
        print("\n========== EXOTEL FETCH FAILED ==========")
        print(f"{type(exc).__name__}: {exc}")
        print(
            "\nCheck that config.py contains the required Exotel settings "
            "and that your API credentials are valid."
        )
        raise


if __name__ == "__main__":
    main()
