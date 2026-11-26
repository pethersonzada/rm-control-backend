import json
import os
import re
from dataclasses import dataclass, field
from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROLES = {"viewer", "importer", "admin"}


def store_key(value: str) -> str:
    value = value.strip()
    match = re.match(r"^([0-9]+)(?:\s|-|$)", value)
    return str(int(match[1])) if match else " ".join(value.upper().split())


@dataclass(frozen=True)
class Identity:
    subject: str
    organization: str
    role: str
    stores: frozenset[str]


@dataclass(frozen=True)
class Calendar:
    days: int
    start_field: str
    timezone: ZoneInfo
    holidays: frozenset[date]


@dataclass(frozen=True)
class Settings:
    mode: str
    database_url: str | None
    identities: dict[str, Identity]
    origins: tuple[str, ...]
    calendars: dict[str, Calendar] = field(default_factory=dict)

    firebase_project_id: str | None = None
    allow_legacy_token: bool = False

    @property
    def enabled(self) -> bool:
        return self.mode in {"local", "operational"} and bool(self.database_url and (self.identities or self.firebase_project_id))


def load_settings() -> Settings:
    mode = "disabled" if os.getenv("VERCEL") else os.getenv("RM_MODE", "disabled")
    url = os.getenv("DATABASE_URL") if mode in {"local", "operational"} else None
    origins = tuple(
        value.strip() for value in os.getenv(
            "CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173" if mode == "local" else "",
        ).split(",") if value.strip()
    )
    identities = {}
    calendars = {}
    firebase_project_id = None
    allow_legacy_token = False
    try:
        if mode in {"local", "operational"}:
            firebase_project_id = os.getenv("RM_FIREBASE_PROJECT_ID") or None
            if firebase_project_id and not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", firebase_project_id):
                raise ValueError
            allow_legacy_token = os.getenv("RM_ALLOW_LEGACY_TOKEN") == "1"
        if mode == "local" and os.getenv("RM_HOSTED") == "1":
            raise ValueError
        if mode == "operational" and (
            not url or not url.startswith("postgresql+psycopg://")
            or any(not re.fullmatch(r"https://[a-zA-Z0-9.-]+(?::[0-9]+)?", origin) for origin in origins)
        ):
            raise ValueError
        if mode in {"local", "operational"}:
            raw = json.loads(os.getenv("RM_IDENTITIES_JSON", "{}"))
            subjects = set()
            if not isinstance(raw, dict) or len(raw) > 10000:
                raise ValueError
            for digest, entry in raw.items():
                if not re.fullmatch(r"[a-f0-9]{64}", digest) or not isinstance(entry, dict):
                    raise ValueError
                subject, organization, role = (entry[key] for key in ("subject", "organization", "role"))
                stores = entry["stores"]
                if (
                    not all(isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", value)
                            for value in (subject, organization))
                    or role not in ROLES or (organization, subject) in subjects
                    or not isinstance(stores, list) or not stores
                    or any(not isinstance(s, str) or not 1 <= len(store_key(s)) <= 200 or s == "*" for s in stores)
                ):
                    raise ValueError
                subjects.add((organization, subject))
                identities[digest] = Identity(subject, organization, role, frozenset(map(store_key, stores)))
            organizations = {identity.organization for identity in identities.values()}
            raw_calendars = json.loads(os.getenv("RM_CALENDARS_JSON", "{}"))
            if not isinstance(raw_calendars, dict):
                raise ValueError
            for organization, entry in raw_calendars.items():
                if organization not in organizations or not isinstance(entry, dict):
                    raise ValueError
                days, start_field = entry["days"], entry["start_field"]
                holidays = entry.get("holidays", [])
                if (type(days) is not int or not 0 <= days <= 365 or
                        start_field not in {"included_at", "received_at"} or
                        not isinstance(holidays, list) or len(holidays) > 1000):
                    raise ValueError
                calendars[organization] = Calendar(
                    days, start_field, ZoneInfo(entry["timezone"]), frozenset(map(date.fromisoformat, holidays)),
                )
    except (TypeError, KeyError, ValueError, ZoneInfoNotFoundError):
        return Settings("disabled", None, {}, ())
    return Settings(mode, url, identities, origins, calendars, firebase_project_id, allow_legacy_token)
