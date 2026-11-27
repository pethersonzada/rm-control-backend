import hashlib
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import store_key
from app.models import AccessRequest, FirebaseMembership, MembershipAudit, Store
from app.repositories.occurrences import transaction


def subject_for(project, uid):
    return "firebase-" + hashlib.sha256(f"{project}/{uid}".encode()).hexdigest()


def org_members(project, organization):
    return select(FirebaseMembership).where(
        FirebaseMembership.project_id == project, FirebaseMembership.organization == organization,
    )


def lock_organization(session, project, organization):
    if session.bind.dialect.name == "postgresql":
        key = int.from_bytes(hashlib.sha256(f"{project}/{organization}".encode()).digest()[:8], "big", signed=True)
        session.execute(text("select pg_advisory_xact_lock(:key)"), {"key": key})


def snapshot(member):
    return {"uid": member.uid, "role": member.role, "stores": member.stores, "active": member.active,
            "owner": bool(member.can_delegate_admin)}


def audit(session, member, actor, action, before):
    session.add(MembershipAudit(
        project_id=member.project_id, organization=member.organization, actor=actor, target_uid=member.uid,
        action=action, previous=before, current=snapshot(member),
    ))


def bootstrap(engine, project, organization, uid, stores):
    with transaction(engine) as session:
        lock_organization(session, project, organization)
        members = org_members(project, organization)
        known = session.scalar(members.where(FirebaseMembership.uid == uid))
        if known and known.role == "admin" and known.active:
            return
        if session.scalar(members.where(FirebaseMembership.role == "admin").limit(1)) is not None:
            return
        member = FirebaseMembership(
            project_id=project, uid=uid, subject=subject_for(project, uid), organization=organization,
            role="admin", stores=sorted(stores), active=True, can_delegate_admin=True,
        )
        session.add(member)
        audit(session, member, "owner-bootstrap", "bootstrap", None)


def list_members(engine, principal):
    with Session(engine) as session:
        rows = session.scalars(org_members(principal.firebase_project, principal.organization).order_by(FirebaseMembership.id))
        return [snapshot(row) for row in rows if set(row.stores) <= principal.stores]


def save_member(engine, principal, data):
    project = principal.firebase_project
    stores = {store_key(value) for value in data.stores}
    if not stores <= principal.stores or data.uid == principal.firebase_uid:
        raise HTTPException(403, "Alteração fora das permissões administrativas.")
    with transaction(engine) as session:
        lock_organization(session, project, principal.organization)
        scoped = org_members(project, principal.organization)
        actor = session.scalar(scoped.where(FirebaseMembership.uid == principal.firebase_uid))
        if (not actor or not actor.active or actor.role != "admin"
                or not stores <= set(actor.stores)
                or (data.role == "admin" and not actor.can_delegate_admin)):
            raise HTTPException(403, "Alteração fora das permissões administrativas.")
        inactive = set(session.scalars(select(Store.code).where(Store.organization == principal.organization, Store.active.is_(False))))
        if stores & inactive:
            raise HTTPException(403, "Loja desativada.")
        member = session.scalar(scoped.where(FirebaseMembership.uid == data.uid))
        before = snapshot(member) if member else None
        if member:
            if not set(member.stores) <= set(actor.stores) or member.can_delegate_admin or (
                member.role == "admin" and not actor.can_delegate_admin
            ):
                raise HTTPException(403, "Alteração fora das permissões administrativas.")
        else:
            member = FirebaseMembership(
                project_id=project, uid=data.uid, subject=subject_for(project, data.uid),
                organization=principal.organization, can_delegate_admin=False,
            )
            session.add(member)
        member.role, member.stores, member.active = data.role, sorted(stores), data.active
        if before != snapshot(member):
            audit(session, member, actor.subject, "membership.update" if before else "membership.create", before)
        request = session.scalar(select(AccessRequest).filter_by(project_id=project, organization=principal.organization, uid=data.uid))
        if request and data.active:
            request.status, request.reviewer, request.reviewed_at = "approved", actor.subject, datetime.now(timezone.utc)
        return snapshot(member)
