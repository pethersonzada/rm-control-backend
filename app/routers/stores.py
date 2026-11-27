from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Identity, store_key
from app.database import database
from app.models import AccessRequest, FirebaseMembership, Store, StoreAudit
from app.repositories.occurrences import transaction
from app.routers.admin import administrator
from app.security import current_identity
from app.services.memberships import audit, lock_organization, snapshot

router = APIRouter()
Principal = Annotated[Identity, Depends(current_identity)]
Admin = Annotated[Identity, Depends(administrator)]


def store_data(row):
    return {"code": row.code, "name": row.name, "active": row.active}


def owner(session, principal):
    lock_organization(session, principal.firebase_project, principal.organization)
    member = session.scalar(select(FirebaseMembership).filter_by(
        project_id=principal.firebase_project, uid=principal.firebase_uid,
        organization=principal.organization, active=True, role="admin", can_delegate_admin=True,
    ))
    if member is None:
        raise HTTPException(403, "Somente o administrador responsável pode gerenciar lojas e solicitações.")
    return member


@router.get("/stores")
def stores(principal: Principal, engine=Depends(database)):
    with Session(engine) as session:
        query = select(Store).where(Store.organization == principal.organization)
        if not principal.can_delegate_admin:
            query = query.where(Store.code.in_(principal.stores), Store.active.is_(True))
        rows = {row.code: store_data(row) for row in session.scalars(query)}
        for code in principal.stores:
            rows.setdefault(code, {"code": code, "name": f"Loja {code}", "active": True})
        return [rows[code] for code in sorted(rows)]


class StoreInput(BaseModel):
    code: str = Field(pattern=r"^[0-9]{1,12}$")
    name: str = Field(min_length=1, max_length=200)
    active: bool = True


@router.put("/admin/stores")
def save_store(data: StoreInput, principal: Admin, engine=Depends(database)):
    code, name = store_key(data.code), data.name.strip()
    if not name:
        raise HTTPException(422, "Nome obrigatório.")
    with transaction(engine) as session:
        actor = owner(session, principal)
        row = session.scalar(select(Store).filter_by(organization=principal.organization, code=code))
        previous = store_data(row) if row else None
        if row is None:
            row = Store(organization=principal.organization, code=code, name=name, active=data.active)
            session.add(row)
        else:
            row.name, row.active = name, data.active
        if code not in actor.stores:
            before = snapshot(actor)
            actor.stores = sorted(set(actor.stores) | {code})
            audit(session, actor, actor.subject, "owner.store.add", before)
        current = store_data(row)
        if previous != current:
            session.add(StoreAudit(organization=principal.organization, actor=actor.subject,
                                   store_code=code, previous=previous, current=current))
        return current


@router.post("/access/request")
def request_access(principal: Principal, engine=Depends(database)):
    if not principal.firebase_uid or not principal.firebase_project:
        raise HTTPException(403, "Acesso Firebase necessário.")
    project, uid = principal.firebase_project, principal.firebase_uid
    with transaction(engine) as session:
        organizations = set(session.scalars(select(FirebaseMembership.organization).where(
            FirebaseMembership.project_id == project, FirebaseMembership.active.is_(True),
            FirebaseMembership.can_delegate_admin.is_(True), FirebaseMembership.role == "admin",
        )))
        if len(organizations) != 1:
            raise HTTPException(409, "Solicite acesso diretamente ao responsável da organização.")
        organization = next(iter(organizations))
        lock_organization(session, project, organization)
        known = session.scalar(select(FirebaseMembership).filter_by(project_id=project, uid=uid))
        if known:
            return {"status": "approved" if known.active else "rejected"}
        row = session.scalar(select(AccessRequest).filter_by(project_id=project, uid=uid))
        if row is None:
            row = AccessRequest(project_id=project, organization=organization, uid=uid, status="pending")
            session.add(row)
        return {"status": row.status}


@router.get("/admin/access-requests")
def requests(principal: Admin, engine=Depends(database)):
    with transaction(engine) as session:
        owner(session, principal)
        rows = session.scalars(select(AccessRequest).filter_by(
            project_id=principal.firebase_project, organization=principal.organization, status="pending",
        ).order_by(AccessRequest.id).limit(500))
        return [{"uid": row.uid, "status": row.status, "created_at": row.created_at.isoformat()} for row in rows]


@router.post("/admin/access-requests/{uid}/reject")
def reject(uid: str, principal: Admin, engine=Depends(database)):
    with transaction(engine) as session:
        actor = owner(session, principal)
        row = session.scalar(select(AccessRequest).filter_by(
            project_id=principal.firebase_project, organization=principal.organization, uid=uid,
        ))
        if not row or row.status != "pending":
            raise HTTPException(404, "Solicitação pendente não encontrada.")
        row.status, row.reviewer, row.reviewed_at = "rejected", actor.subject, datetime.now(timezone.utc)
        return {"status": row.status}
