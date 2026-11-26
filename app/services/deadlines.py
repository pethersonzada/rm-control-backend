from datetime import datetime, timedelta

from app.config import Calendar


def deadline(record: dict, calendar: Calendar, today=None):
    source = record[calendar.start_field]
    if not source:
        return {}
    try:
        current = datetime.strptime(source.split()[0], "%d/%m/%Y").date()
    except ValueError:
        return {}
    remaining = calendar.days
    while remaining:
        current += timedelta(days=1)
        if current.weekday() < 5 and current not in calendar.holidays:
            remaining -= 1
    today = today or datetime.now(calendar.timezone).date()
    return {"due_at": current.isoformat(), "deadline_passed": today > current}
