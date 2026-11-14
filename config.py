import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("EXOTEL_API_KEY")
API_TOKEN = os.getenv("EXOTEL_API_TOKEN")
ACCOUNT_SID = os.getenv("EXOTEL_ACCOUNT_SID")

BASE_URL = os.getenv(
    "EXOTEL_BASE_URL",
    "https://api.exotel.com"
)

CALLS_URL = (
    f"{BASE_URL}/v1/Accounts/{ACCOUNT_SID}/Calls.json"
)

if not all([API_KEY, API_TOKEN, ACCOUNT_SID]):
    raise ValueError("Missing Exotel API credentials")