"""Runtime configuration. Secrets come from .env only — never hard-code keys."""
from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

APP_NAME = "PikaRedact"
LISTS_DIR = ROOT / "data" / "lists"
STATIC_DIR = ROOT / "static"

JOB_TTL_SECONDS = int(os.environ.get("JOB_TTL_SECONDS", "3600"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "50"))

# Detection thresholds
MIN_SCORE = float(os.environ.get("MIN_SCORE", "0.3"))        # below this, findings are dropped
AUTO_ACCEPT_SCORE = float(os.environ.get("AUTO_ACCEPT_SCORE", "0.4"))  # pre-ticked in the review UI
REVIEW_SCORE = float(os.environ.get("REVIEW_SCORE", "0.6"))  # below this, flagged "ควรตรวจ"

# LLM
LLM_CHUNK_CHARS = int(os.environ.get("LLM_CHUNK_CHARS", "3500"))
LLM_CONCURRENCY = int(os.environ.get("LLM_CONCURRENCY", "4"))
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "120"))
LLM_VERIFY_TLS = os.environ.get("LLM_VERIFY_TLS", "true").lower() != "false"


def llm_profiles() -> dict[str, dict]:
    """Every LLM_<NAME>_BASE_URL in the environment becomes a selectable profile."""
    profiles: dict[str, dict] = {}
    for key, val in os.environ.items():
        m = re.match(r"^LLM_(.+)_BASE_URL$", key, re.IGNORECASE)
        if not m or not val:
            continue
        prefix = m.group(1).upper()
        profiles[prefix.lower()] = {
            "name": prefix.lower(),
            "base_url": val.rstrip("/"),
            "api_key": os.environ.get(f"LLM_{prefix}_API_KEY", ""),
            "model": os.environ.get(f"LLM_{prefix}_MODEL", ""),
        }
    return dict(sorted(profiles.items()))


def default_llm_profile() -> str:
    name = os.environ.get("LLM_DEFAULT_PROFILE", "").lower()
    profiles = llm_profiles()
    if name in profiles:
        return name
    return next(iter(profiles), "")


def _find_thai_font() -> str | None:
    env = os.environ.get("PDF_THAI_FONT")
    candidates = [env] if env else []
    candidates += [
        r"C:\Windows\Fonts\tahoma.ttf",
        r"C:\Windows\Fonts\LeelawUI.ttf",
        r"C:\Windows\Fonts\THSarabunNew.ttf",
        "/usr/share/fonts/truetype/tlwg/Garuda.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansThai-Regular.ttf",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return None


PDF_THAI_FONT = _find_thai_font()
