"""Firebase ID token verification and server-owned authorization."""
import json
import re
import threading
import time
from http.client import HTTPException as HTTPTransportError
from urllib.request import urlopen

from google.auth import jwt
from google.auth.exceptions import GoogleAuthError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Identity, ROLES, store_key
from app.models import FirebaseMembership, Store

_CERT_URL = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
_lock = threading.Lock()
_certs = {}
_expires = 0.0


class InvalidFirebaseToken(Exception):
    pass


class FirebaseUnavailable(Exception):
    pass


def certificates():
    global _certs, _expires
    with _lock:
        if _certs and time.monotonic() < _expires:
            return _certs
        try:
            with urlopen(_CERT_URL, timeout=5) as response:
                data = json.loads(response.read(262144))
                if not isinstance(data, dict) or not data or not all(
                    isinstance(k, str) and isinstance(v, str) for k, v in data.items()
                ):
                    raise ValueError
                age = re.search(r"max-age=(\d+)", response.headers.get("Cache-Control", ""))
                ttl = min(int(age[1]), 86400) if age else 0
        except (OSError, ValueError, HTTPTransportError) as error:
            raise FirebaseUnavailable() from error
        _certs, _expires = data, time.monotonic() + ttl
        return _certs


def verify_uid(token: str, project: str) -> str:
    if len(token) > 8192 or not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", token):
        raise InvalidFirebaseToken()
    try:
        header = jwt.decode_header(token)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str) or not header["kid"]:
            raise InvalidFirebaseToken()
        claims = jwt.decode(token, certs=certificates(), audience=project)
        uid = claims.get("sub")
        now = time.time()
        if (
            claims.get("iss") != f"https://securetoken.google.com/{project}"
            or claims.get("aud") != project
            or not isinstance(uid, str) or not 1 <= len(uid) <= 128
            or any(type(claims.get(key)) is not int for key in ("exp", "iat", "auth_time"))
            or not 0 < claims["auth_time"] <= claims["iat"] <= now < claims["exp"]
        ):
            raise InvalidFirebaseToken()
        return uid
    except (ValueError, TypeError, KeyError, GoogleAuthError) as error:
        raise InvalidFirebaseToken() from error


def membership(engine, project, uid):
    if engine is None:
        raise FirebaseUnavailable()
    with Session(engine) as session:
        member = session.scalar(select(FirebaseMembership).where(
            FirebaseMembership.project_id == project,
            FirebaseMembership.uid == uid,
            FirebaseMembership.active.is_(True),
        ))
        if member is None or member.role not in ROLES:
            return None
        stores = member.stores
        if not isinstance(stores, list) or not stores or any(
            not isinstance(s, str) or s == "*" or not 1 <= len(store_key(s)) <= 200 for s in stores
        ):
            return None
        inactive = set(session.scalars(select(Store.code).where(Store.organization == member.organization, Store.active.is_(False))))
        allowed = frozenset(map(store_key, stores)) - inactive
        if not allowed and not member.can_delegate_admin:
            return None
        return Identity(
            member.subject, member.organization, member.role, allowed,
            project, uid, member.can_delegate_admin,
        )
