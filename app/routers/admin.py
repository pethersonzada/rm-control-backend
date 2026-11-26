from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError

from app.config import Identity, store_key
from app.database import database
from app.security import current_identity
from app.services.memberships import list_members, save_member

router = APIRouter(prefix="/admin")


def administrator(principal: Annotated[Identity, Depends(current_identity)]):
    if principal.role != "admin" or not principal.firebase_uid:
        raise HTTPException(403, "Acesso administrativo restrito.")
    return principal


class MemberUpdate(BaseModel):
    uid: str = Field(pattern=r"^[!-~]{1,128}$")
    role: Literal["admin", "importer", "viewer"]
    stores: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(min_length=1, max_length=250)
    active: bool

    @field_validator("stores")
    @classmethod
    def normalize_stores(cls, values):
        stores = set(map(store_key, values))
        if "" in stores or "*" in stores:
            raise ValueError("Informe lojas explícitas.")
        return sorted(stores)


@router.get("/users")
def users(principal: Annotated[Identity, Depends(administrator)], engine=Depends(database)):
    return list_members(engine, principal)


@router.put("/users")
def write_user(data: MemberUpdate, principal: Annotated[Identity, Depends(administrator)], engine=Depends(database)):
    try:
        return save_member(engine, principal, data)
    except IntegrityError:
        raise HTTPException(409, "Já existe um vínculo para este UID.") from None
