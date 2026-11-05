"""Business calendar shared by capture and API date validation."""
import os
from datetime import datetime
from zoneinfo import ZoneInfo


def today():
    return datetime.now(ZoneInfo(os.getenv('APP_TIMEZONE', 'America/Santo_Domingo'))).date()
