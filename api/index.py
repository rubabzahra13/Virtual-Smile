"""
Vercel serverless entrypoint.

Routes all HTTP traffic to the FastAPI app defined in backend/main.py.
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT / "backend"

# backend/main.py imports local modules like `analysis` directly,
# so ensure backend is on sys.path in the serverless runtime.
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main import app  # noqa: E402,F401

