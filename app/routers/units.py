from typing import Annotated
from fastapi import APIRouter,Depends,HTTPException,Query
from pydantic import BaseModel,Field,field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import database
from app.models import UnitReference,UnitReferenceAudit,CatalogProduct
from app.repositories.occurrences import transaction
from app.routers.stores import Admin,owner
from app.schemas import Amount

router=APIRouter()


class UnitInput(BaseModel):
    code: str = Field(pattern=r"^[0-9]{1,40}$")
    package: str = Field(min_length=1,max_length=80)
    base_unit: str = Field(min_length=1,max_length=80)
    factor: Amount = Field(gt=0)
    reference: str = Field(min_length=1,max_length=500)
    active: bool = True
    revision: int = Field(ge=0)

    @field_validator("package","base_unit","reference")
    @classmethod
    def nonblank(cls,value):
        if not value.strip():
            raise ValueError("Campo obrigatório.")
        return value.strip()


def snapshot(row):
    return UnitInput.model_validate({key:getattr(row,key) for key in UnitInput.model_fields}).model_dump(mode="json")


@router.get("/admin/units")
def units(principal:Admin,code:Annotated[str,Query(pattern=r"^[0-9]{1,40}$")],engine=Depends(database)):
    if not principal.can_delegate_admin:
        raise HTTPException(403,"Somente o responsável pode configurar referências.")
    with Session(engine) as session:
        rows=session.scalars(select(UnitReference).filter_by(organization=principal.organization,code=code).order_by(UnitReference.package).limit(201)).all()
        if len(rows)>200:
            raise HTTPException(422,"Limite de referências por PLU excedido.")
        return [snapshot(row) for row in rows]


@router.put("/admin/units")
def save_unit(data:UnitInput,principal:Admin,engine=Depends(database)):
    with transaction(engine) as session:
        actor=owner(session,principal)
        product=session.scalar(select(CatalogProduct).filter_by(organization=principal.organization,code=data.code,active=True))
        if product is None:
            raise HTTPException(422,"Cadastre e confira o PLU no catálogo primeiro.")
        scope=select(UnitReference).filter_by(organization=principal.organization,code=data.code)
        row=session.scalar(scope.where(UnitReference.package==data.package))
        previous=snapshot(row) if row else None
        if data.revision!=(row.revision if row else 0):
            raise HTTPException(409,"Referência alterada. Atualize a tela.")
        if row is None and len(session.scalars(scope.limit(200)).all())>=200:
            raise HTTPException(422,"Limite de 200 referências por PLU.")
        if data.active and session.scalar(scope.where(UnitReference.active.is_(True),UnitReference.package!=data.package,UnitReference.base_unit!=data.base_unit).limit(1)):
            raise HTTPException(422,"Use a mesma unidade de referência nas embalagens ativas deste PLU.")
        payload=data.model_dump()
        payload["revision"]+=1
        if row is None:
            row=UnitReference(organization=principal.organization,**payload)
            session.add(row)
        else:
            for key,value in payload.items():
                setattr(row,key,value)
        current=snapshot(row)
        session.add(UnitReferenceAudit(organization=principal.organization,actor=actor.subject,code=data.code,package=data.package,previous=previous,current=current))
        return current
