"""Cloud records carry UTC offsets; the original local timestamp format stays."""
from datetime import datetime, timezone
from sql_backend import remote_enabled


def data_timestamp():
    return datetime.now(timezone.utc).isoformat() if remote_enabled() else datetime.now().isoformat()


def elapsed_seconds(value):
    parsed = datetime.fromisoformat(value)
    return (datetime.now(parsed.tzinfo) - parsed).total_seconds()
