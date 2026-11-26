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


def test_real_rs256_signature_and_claims(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from google.auth import crypt

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    date = datetime.now(timezone.utc)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "synthetic")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(date - timedelta(days=1))
            .not_valid_after(date + timedelta(days=1)).sign(key, hashes.SHA256()))
    signer = crypt.RSASigner.from_string(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ), key_id="qa")
    monkeypatch.setattr(firebase_auth, "certificates", lambda: {"qa": cert.public_bytes(serialization.Encoding.PEM)})
    now = int(time.time())
    valid = {"sub": "qa", "iss": f"https://securetoken.google.com/{PROJECT}", "aud": PROJECT,
             "auth_time": now - 2, "iat": now - 1, "exp": now + 300}
    token = firebase_auth.jwt.encode(signer, valid).decode()
    assert firebase_auth.verify_uid(token, PROJECT) == "qa"
    prefix, signature = token.rsplit(".", 1)
    altered = prefix + "." + ("A" if signature[0] != "A" else "B") + signature[1:]
    for candidate in (
        altered,
        firebase_auth.jwt.encode(signer, {**valid, "aud": "other-project"}).decode(),
        firebase_auth.jwt.encode(signer, {**valid, "exp": now - 1}).decode(),
    ):
        with pytest.raises(firebase_auth.InvalidFirebaseToken):
            firebase_auth.verify_uid(candidate, PROJECT)


def test_admin_scope_bootstrap_and_audit(workspace, monkeypatch):
    from sqlalchemy import select
    from app.models import MembershipAudit
    from app.services.memberships import bootstrap

    client, engine = workspace
    client.app.state.settings = replace(client.app.state.settings, firebase_project_id=PROJECT)
    monkeypatch.setattr(security, "verify_uid", lambda token, project: token.split(".")[1])

    def identity(uid):
        return {"Authorization": f"Bearer x.{uid}.x"}

    bootstrap(engine, PROJECT, "alpha", "owner", {"2", "3"})
    bootstrap(engine, PROJECT, "alpha", "other", {"2"})
    bootstrap(engine, PROJECT, "beta", "beta-owner", {"2"})
    owner = identity("owner")
    api = "/admin/users"
    data = {"uid": "operator", "role": "importer", "stores": ["002-EXEMPLO"], "active": True}
    assert client.get("/ready").status_code == 200
    assert client.get("/me", headers=identity("other")).status_code == 403
    assert client.put(api, headers=owner, json={**data, "uid": "owner"}).status_code == 403
    assert client.put(api, headers=owner, json={**data, "stores": ["4"]}).status_code == 403
    assert client.put(api, headers=owner, json={**data, "uid": "beta-owner"}).status_code == 409
    assert client.put(api, headers=owner, json=data).status_code == 200
    assert client.get("/me", headers=identity("operator")).json()["stores"] == ["2"]
    assert client.get(api, headers=identity("operator")).status_code == 403
    assert client.get("/occurrences?store=3", headers=identity("operator")).status_code == 403
    assert client.put(api, headers=owner, json={**data, "uid": "admin", "role": "admin"}).status_code == 200
    assert client.put(api, headers=identity("admin"), json={**data, "uid": "blocked", "role": "admin"}).status_code == 403
    assert client.put(api, headers=owner, json={**data, "active": False}).status_code == 200
    response = client.get("/me", headers=identity("operator"))
    assert response.status_code == 403 and response.headers["X-RM-Account-Denied"] == "1"
    assert {row["uid"] for row in client.get(api, headers=owner).json()} == {"owner", "admin", "operator"}
    with Session(engine) as session:
        changes = session.scalars(select(MembershipAudit).order_by(MembershipAudit.id)).all()
        assert [change.action for change in changes] == [
            "bootstrap", "bootstrap", "membership.create", "membership.create", "membership.update",
        ]
        assert changes[-1].previous["active"] and not changes[-1].current["active"]
