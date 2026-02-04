import hashlib
from datetime import datetime


def monthly_password_hash(suffix: str = "56666") -> str:
    month_str = datetime.now().strftime("%Y%m")
    raw = f"{month_str}{suffix}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

