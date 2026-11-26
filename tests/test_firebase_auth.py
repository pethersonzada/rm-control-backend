from dataclasses import replace
import time

import pytest
from sqlalchemy.orm import Session

from app import firebase_auth, security
from app.models import FirebaseMembership
from conftest import headers

PROJECT = "rm-control-webapp"


def test_token_claims(monkeypatch):
    now = int(time.time())
    claims = {"sub": "synthetic-user", "aud": PROJECT, "iss": f"https://securetoken.google.com/{PROJECT}",
              "auth_time": now-20, "iat": now-10, "exp": now+3600}
    monkeypatch.setattr(firebase_auth, "certificates", lambda: {})
    monkeypatch.setattr(firebase_auth.jwt, "decode_header", lambda _: {"alg": "RS256", "kid": "synthetic"})
    monkeypatch.setattr(firebase_auth.jwt, "decode", lambda *args, **kwargs: dict(claims))
    assert firebase_auth.verify_uid("a.b.c", PROJECT) == "synthetic-user"
    for key, bad in [("aud", "another-project"), ("iss", "wrong"), ("sub", ""), ("exp", now-1), ("auth_time", now+10)]:
        original = claims[key]
        claims[key] = bad
        with pytest.raises(firebase_auth.InvalidFirebaseToken):
            firebase_auth.verify_uid("a.b.c", PROJECT)
        claims[key] = original
    monkeypatch.setattr(firebase_auth.jwt, "decode_header", lambda _: {"alg": "none", "kid": "synthetic"})
    with pytest.raises(firebase_auth.InvalidFirebaseToken):
        firebase_auth.verify_uid("a.b.c", PROJECT)


def test_membership_scope(workspace, monkeypatch):
    client, engine = workspace
    client.app.state.settings = replace(client.app.state.settings, firebase_project_id=PROJECT)
    monkeypatch.setattr(security, "verify_uid", lambda *_: "synthetic-user")
    firebase = {"Authorization": "Bearer a.b.c"}
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers=headers()).status_code == 401
    assert client.get("/me", headers=firebase).status_code == 403
    with Session(engine) as session:
        session.add(FirebaseMembership(project_id=PROJECT, uid="synthetic-user", subject="firebase-viewer",
                                       organization="alpha", role="viewer", stores=["2"], active=True))
        session.commit()
    assert client.get("/me", headers=firebase).json()["stores"] == ["2"]
    assert client.get("/occurrences?store=3", headers=firebase).status_code == 403
    assert client.post("/imports/confirm", headers=firebase, json=[]).status_code == 403
    with Session(engine) as session:
        user = session.query(FirebaseMembership).one()
        user.active = False
        session.commit()
    assert client.get("/me", headers=firebase).status_code == 403
