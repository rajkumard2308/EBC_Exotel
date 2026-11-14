import requests
import streamlit as st
from urllib.parse import urljoin


def _secret(name):
    value = st.secrets.get(name, "")
    if not value:
        raise RuntimeError(
            f"Missing Streamlit secret: {name}. "
            "Add it to .streamlit/secrets.toml locally and Streamlit Cloud Secrets."
        )
    return str(value).strip()


class ExotelClient:
    """Small Exotel Calls API client using Streamlit secrets only."""

    def __init__(self):
        api_key = _secret("EXOTEL_API_KEY")
        api_token = _secret("EXOTEL_API_TOKEN")
        account_sid = _secret("EXOTEL_ACCOUNT_SID")
        subdomain = _secret("EXOTEL_SUBDOMAIN").rstrip("/")

        # The supplied subdomain should be the API host, for example:
        # https://api.exotel.com
        self.base_url = subdomain
        self.calls_url = (
            f"{self.base_url}/v1/Accounts/{account_sid}/Calls.json"
        )

        self.session = requests.Session()
        self.session.auth = (api_key, api_token)
        self.session.headers.update({"Accept": "application/json"})

    def fetch_calls(self, start_date, end_date):
        params = {
            "DateCreated": f"gte:{start_date};lte:{end_date}",
            "PageSize": 100,
            "SortBy": "DateCreated:asc",
            "details": "true",
            "NumbersMetadata": "true",
        }

        url = self.calls_url
        all_calls = []

        while url:
            response = self.session.get(url, params=params, timeout=60)

            if not response.ok:
                raise RuntimeError(
                    f"Exotel API returned HTTP {response.status_code}: "
                    f"{response.text[:1500]}"
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise RuntimeError(
                    f"Exotel returned a non-JSON response: {response.text[:1000]}"
                ) from exc

            if not isinstance(payload, dict):
                raise RuntimeError(
                    f"Unexpected Exotel response type: {type(payload).__name__}"
                )

            calls = payload.get("Calls") or []
            if not isinstance(calls, list):
                raise RuntimeError("Unexpected Exotel Calls response format.")

            all_calls.extend(calls)

            metadata = payload.get("Metadata") or {}
            if not isinstance(metadata, dict):
                metadata = {}

            next_uri = metadata.get("NextPageUri")
            url = urljoin(self.base_url + "/", next_uri) if next_uri else None
            # Query parameters are already included in Exotel's next-page URI.
            params = None

        return all_calls
