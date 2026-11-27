"""Reference data owned and explicitly reviewed by the organization administrator."""
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import database
from app.models import CatalogAudit, CatalogProduct
from app.repositories.occurrences import transaction
from app.routers.stores import Admin, Principal, owner

router = APIRouter()


class Product(BaseModel):
    code: str = Field(pattern=r"^[0-9]{1,40}$")
    description: str = Field(min_length=1, max_length=500)
    category: str | None = Field(default=None, max_length=200)
    package: str | None = Field(default=None, max_length=80)
    reference: str = Field(min_length=1, max_length=200)
    active: bool = True

    @field_validator("description", "reference")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("Informe o campo.")
        return value.strip()


class Batch(BaseModel):
    products: list[Product] = Field(min_length=1, max_length=1000)
    preview: bool = True


def snapshot(row):
    return {key: getattr(row, key) for key in Product.model_fields}


@router.get("/catalog")
def products(principal: Principal, engine=Depends(database),
             search: Annotated[str, Query(max_length=100)] = "",
             code: Annotated[str | None, Query(pattern=r"^[0-9]{1,40}$")] = None,
             cursor: Annotated[int, Query(ge=0)] = 0,
             limit: Annotated[int, Query(ge=1, le=100)] = 50):
    with Session(engine) as session:
        query = select(CatalogProduct).where(CatalogProduct.organization == principal.organization,
                                             CatalogProduct.id > cursor)
        if not principal.can_delegate_admin:
            query = query.where(CatalogProduct.active.is_(True))
        if code:
            query = query.where(CatalogProduct.code == code)
        if search.strip():
            value = search.strip().lower()
            query = query.where(or_(CatalogProduct.code.contains(value, autoescape=True),
                                     func.lower(CatalogProduct.description).contains(value, autoescape=True)))
        rows = session.scalars(query.order_by(CatalogProduct.id).limit(limit + 1)).all()
        return {"products": [snapshot(row) for row in rows[:limit]],
                "next_cursor": rows[limit-1].id if len(rows) > limit else None}


def save(session, principal, data, preview):
    row = session.scalar(select(CatalogProduct).filter_by(organization=principal.organization, code=data.code))
    previous = snapshot(row) if row else None
    current = data.model_dump()
    state = "new" if row is None else "unchanged" if previous == current else "updated"
    if not preview and state != "unchanged":
        if row is None:
            row = CatalogProduct(organization=principal.organization, **current)
            session.add(row)
        else:
            for key, value in current.items():
                setattr(row, key, value)
        session.add(CatalogAudit(organization=principal.organization, actor=principal.subject, code=data.code,
                                 previous=previous, current=current))
    return state


@router.put("/admin/catalog")
def save_product(data: Product, principal: Admin, engine=Depends(database)):
    with transaction(engine) as session:
        owner(session, principal)
        save(session, principal, data, False)
        return data


@router.post("/admin/catalog/batch")
def batch(data: Batch, principal: Admin, engine=Depends(database)):
    if len({product.code for product in data.products}) != len(data.products):
        raise HTTPException(422, "PLUs duplicados no lote.")
    counts = {"new": 0, "updated": 0, "unchanged": 0}
    with transaction(engine) as session:
        owner(session, principal)
        for product in data.products:
            counts[save(session, principal, product, data.preview)] += 1
    return {"preview": data.preview, "counts": counts}
