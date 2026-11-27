"""Administrator-reviewed operational rules and explicit financial evidence."""
from datetime import date
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Calendar, store_key
from app.database import database
from app.models import FinancialReview, Occurrence, OperationalPolicy, OperationalPolicyAudit
from app.repositories import occurrences
from app.routers.operations import Principal
from app.routers.stores import Admin, owner
from app.schemas import Amount
from app.security import require_store

router=APIRouter()


class PolicyInput(BaseModel):
    enabled: bool
    days: int = Field(ge=0,le=365)
    start_field: Literal["included_at","received_at"]
    timezone: str = Field(max_length=80)
    holidays: list[date] = Field(default_factory=list,max_length=1000)
    reference: str = Field(min_length=1,max_length=500)
    revision: int = Field(ge=0)

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls,value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError,ValueError):
            raise ValueError("Fuso horário inválido.") from None
        return value

    @field_validator("reference")
    @classmethod
    def required_reference(cls,value):
        if not value.strip():
            raise ValueError("Informe a referência validada.")
        return value.strip()


def policy_snapshot(row):
    return {key:getattr(row,key) for key in PolicyInput.model_fields}


def effective_calendar(engine,organization,fallback=None):
    with Session(engine) as session:
        row=session.get(OperationalPolicy,organization)
        if row is None:
            return fallback
        if not row.enabled:
            return None
        return Calendar(row.days,row.start_field,ZoneInfo(row.timezone),frozenset(date.fromisoformat(d) for d in row.holidays))


@router.get("/admin/policy")
def get_policy(principal:Admin,engine=Depends(database)):
    if not principal.can_delegate_admin:
        raise HTTPException(403,"Somente o responsável pode configurar regras.")
    with Session(engine) as session:
        row=session.get(OperationalPolicy,principal.organization)
        return policy_snapshot(row) if row else {"enabled":False,"days":0,"start_field":"received_at",
                                                "timezone":"America/Recife","holidays":[],"reference":"","revision":0}


@router.put("/admin/policy")
def save_policy(data:PolicyInput,principal:Admin,engine=Depends(database)):
    with occurrences.transaction(engine) as session:
        actor=owner(session,principal)
        row=session.get(OperationalPolicy,principal.organization)
        previous=policy_snapshot(row) if row else None
        if data.revision!=(row.revision if row else 0):
            raise HTTPException(409,"Configuração alterada. Atualize a tela.")
        payload=data.model_dump(mode="json")
        payload["holidays"]=sorted(set(payload["holidays"]))
        payload["revision"]+=1
        if row is None:
            row=OperationalPolicy(organization=principal.organization,**payload)
            session.add(row)
        else:
            for key,value in payload.items():
                setattr(row,key,value)
        session.add(OperationalPolicyAudit(organization=principal.organization,actor=actor.subject,previous=previous,current=payload))
        return payload


class FinanceInput(BaseModel):
    approved: Amount | None = None
    recovered: Amount | None = None
    pending: Amount | None = None
    reference: str = Field(min_length=1,max_length=500)
    revision: int = Field(ge=0)

    @field_validator("reference")
    @classmethod
    def required_reference(cls,value):
        if not value.strip():
            raise ValueError("Informe a evidência conferida.")
        return value.strip()


def finance_snapshot(row):
    if row is None:
        return {"approved":None,"recovered":None,"pending":None,"reference":"","revision":0}
    return FinanceInput.model_validate({k:getattr(row,k) for k in FinanceInput.model_fields}).model_dump(mode="json")


@router.get("/occurrences/{code}/finance")
def get_finance(code:Annotated[str,Path(pattern=r"^[0-9]{1,40}$")],
                store:Annotated[str,Query(min_length=1,max_length=200)],principal:Principal,engine=Depends(database)):
    require_store(principal,store)
    with Session(engine) as session:
        row=session.scalar(occurrences.scoped(principal).where(Occurrence.store==store_key(store),
            Occurrence.code==code))
        if row is None:
            raise HTTPException(404,"Chamado não encontrado.")
        return finance_snapshot(session.get(FinancialReview,row.id))


@router.put("/occurrences/{code}/finance")
def save_finance(code:Annotated[str,Path(pattern=r"^[0-9]{1,40}$")],
                 store:Annotated[str,Query(min_length=1,max_length=200)],data:FinanceInput,
                 principal:Admin,engine=Depends(database)):
    require_store(principal,store)
    with occurrences.transaction(engine) as session:
        owner(session,principal)
        row=occurrences.locked(session,principal,store_key(store),code)
        if row is None:
            raise HTTPException(404,"Chamado não encontrado.")
        review=session.get(FinancialReview,row.id)
        previous=finance_snapshot(review)
        if data.revision!=previous["revision"]:
            raise HTTPException(409,"Valores alterados. Atualize a tela.")
        payload=data.model_dump()
        payload["revision"]+=1
        if review is None:
            review=FinancialReview(occurrence_id=row.id,**payload)
            session.add(review)
        else:
            for key,value in payload.items():
                setattr(review,key,value)
        current=finance_snapshot(review)
        occurrences.audit(session,principal,row,"finance.review",row.status,{"previous":previous,"current":current})
        return current
