import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Base paths
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"

DATA_DIR.mkdir(exist_ok=True)

# Flask config
SECRET_KEY = os.getenv("SECRET_KEY", "athena-dev-key-change-me")
SSO_SECRET = os.getenv("SSO_SECRET", "sso-shared-secret-change-in-production")

# PIN Authentication — same hash as Quanta
PIN_HASH = os.getenv("PIN_HASH", "")
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "true").lower() == "true"

# Obsidian vault path
OBSIDIAN_VAULT = Path(os.getenv("OBSIDIAN_VAULT", "/home/phil/Documents/philVault"))
TEMPLATES_DIR = OBSIDIAN_VAULT / "Projects" / "Quanta" / "Templates"

INTERNAL_TOKEN = os.environ.get('INTERNAL_TOKEN', 'athena-internal')
