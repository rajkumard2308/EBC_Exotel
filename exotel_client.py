import requests
from urllib.parse import urljoin

import streamlit as st

try:
    from config import API_KEY, API_TOKEN, CALLS_URL, BASE_URL
except ModuleNotFoundError:
    API_KEY = st.secrets["API_KEY"]
    API_TOKEN = st.secrets["API_TOKEN"]
    CALLS_URL = st.secrets["CALLS_URL"]
    BASE_URL = st.secrets["BASE_URL"]


class ExotelClient:

    def __init__(self):
        self.session = requests.Session()
        self.session.auth = (API_KEY, API_TOKEN)
        self.session.headers.update({
            "Accept": "application/json"
        })

    def fetch_calls(self, start_date, end_date):

        params = {
            "DateCreated": f"gte:{start_date};lte:{end_date}",
            "PageSize": 100,
            "SortBy": "DateCreated:asc",
            "details": "true"
        }

        url = CALLS_URL
        all_calls = []

        while url:

            response = self.session.get(
                url,
                params=params,
                timeout=60
            )

            print("HTTP Status:", response.status_code)

            if not response.ok:
                print("Exotel API Error:", response.text[:2000])
                response.raise_for_status()

            try:
                data = response.json()
            except ValueError:
                raise RuntimeError(
                    "Exotel returned a non-JSON response: "
                    + response.text[:1000]
                )

            if not isinstance(data, dict):
                raise RuntimeError(
                    f"Unexpected Exotel response: {data}"
                )

            # Debug information
            print("Response keys:", list(data.keys()))

            calls = data.get("Calls") or []

            if not isinstance(calls, list):
                raise RuntimeError(
                    "Unexpected Calls format: "
                    + str(type(calls))
                )

            all_calls.extend(calls)

            # Metadata can be null
            metadata = data.get("Metadata") or {}

            if not isinstance(metadata, dict):
                metadata = {}

            next_uri = metadata.get("NextPageUri")

            if next_uri:
                url = urljoin(BASE_URL, next_uri)
            else:
                url = None

                if len(calls) == 100:
                    print(
                        "WARNING: 100 records returned but "
                        "no pagination URL was provided. "
                        "Check the API response structure."
                    )

            params = None

        print("Total records retrieved:", len(all_calls))

        return all_calls