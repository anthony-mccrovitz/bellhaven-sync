"""Configuration. Everything comes from environment variables (or a .env file)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

CRM_TOKEN = os.environ.get("CRM_TOKEN", "")
CRM_BASE_URL = os.environ.get("CRM_BASE_URL", "https://analyst-assessment-production.up.railway.app").rstrip("/")
SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "https://analyst-assessment-production.up.railway.app").rstrip("/")
DB_PATH = Path(os.environ.get("SYNC_DB_PATH", ROOT / "data" / "sync.db"))

# The operator whose website we are syncing against. The parent account is looked up
# by name in the CRM at run time, so a renamed parent account doesn't break the run.
OPERATOR_NAME = "Bellhaven Senior Living"
PARENT_ACCOUNT_NAME = "Bellhaven Senior Living (Parent Account)"

# Behaviour toggles
PROPOSE_COSMETIC_RENAMES = False   # "Rehab and" vs "Rehabilitation &" etc. are left alone
PROPOSE_PHONE_UPDATES = False      # CRM phone != website phone is noisy; off by default
MOVE_CONTACTS_FROM_DUPLICATES = True   # re-point active contacts on a retired duplicate to the survivor
# Status for a Bellhaven child that was sold and keeps its parent for billing (divest_chow).
# "" keeps the SOP literally ("leave it exactly as is"); "Needs Review" also drops it off
# active-outreach lists. Chosen "" so billing records are never touched beyond the chow link.
SOLD_ACCOUNT_STATUS = ""
NOTE_TAG = "bellhaven-sync"

# Website care offering -> CRM care_type
CARE_TYPE_MAP = {
    "assisted living": "Assisted Living",
    "memory support": "Memory Care",
    "memory care": "Memory Care",
    "short-term rehabilitation & nursing": "Skilled Nursing",
    "skilled nursing": "Skilled Nursing",
    "independent living": "Independent Living",
}
