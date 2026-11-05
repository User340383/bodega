"""Opt-in synthetic history for trying the dashboard. Never runs at application startup.

Creates 14 logged dates ending yesterday through the real lifecycle and confirmation code:
Bread doubles (UP), Milk stays level (STABLE), Eggs fall (DOWN). Dates that already have
confirmed sales are skipped, so re-running does not duplicate records.
"""
from datetime import timedelta
from decimal import Decimal

from . import repository
from .clock import SystemClock
from .database import migrate
from .main import ConfirmLine, ConfirmRequest

LOGGED_DATES = 14


def seed_analysis_history():
    migrate()
    newest = SystemClock().today() - timedelta(days=1)
    # Sundays are skipped so the dashboard shows irregular logged dates.
    dates = []
    day = newest
    while len(dates) < LOGGED_DATES:
        if day.weekday() != 6:
            dates.append(day)
        day -= timedelta(days=1)
    dates.reverse()
    logged = {row["saleDate"] for row in repository.get_confirmed_sales_between(dates[0], newest)}

    created = 0
    for position, sale_date in enumerate(dates):
        if sale_date in logged:
            continue
        recent = position >= LOGGED_DATES - 7
        lines = [
            ("Bread", "4" if recent else "2", "1.50"),
            ("Milk", "2.5", "1.20"),
            ("Eggs", "1" if recent else "3", "2.25"),
        ]
        submission_id = repository.create_submission(sale_date)
        repository.update_status(submission_id, "CLASSIFYING")
        repository.update_status(submission_id, "AI_UNAVAILABLE")
        repository.save_sales_records(ConfirmRequest(submissionId=submission_id, lines=[
            ConfirmLine(itemName=name, quantity=Decimal(quantity), unitPrice=Decimal(price), source="MANUAL")
            for name, quantity, price in lines
        ]))
        created += 1
    print(f"Synthetic analysis history: {created} logged dates created, {LOGGED_DATES - created} already present.")


if __name__ == "__main__":
    seed_analysis_history()
