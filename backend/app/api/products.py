"""Product catalog (spec Section 3.2). Changes are picked up by product search automatically:
only the edited product is re-embedded, on the next search."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.auth.deps import CurrentUser
from app.catalog.loader import ProductIn
from app.db.enums import StockStatus
from app.db.models import Product
from app.db.session import get_db
from app.rag.query import parse_price_constraints
from app.rag.retrieve import get_product_context

router = APIRouter(prefix="/api/products", tags=["products"])


class ProductOut(BaseModel):
    product_id: str
    sku: str
    name: str
    category: str
    price: Decimal
    currency: str
    description: str
    key_attributes: dict[str, Any]
    stock_status: str
    source_url: str | None
    last_updated: datetime


class ProductPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    category: str | None = Field(default=None, min_length=1, max_length=128)
    price: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    description: str | None = None
    key_attributes: dict[str, Any] | None = None
    stock_status: StockStatus | None = None
    source_url: str | None = None


def _get(db: Session, user_id: str, product_id: str) -> Product:
    p = db.get(Product, product_id)
    if p is None or p.user_id != user_id:
        raise HTTPException(status_code=404, detail="Product not found")
    return p


@router.get("", response_model=list[ProductOut])
def list_products(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> list[ProductOut]:
    query = select(Product).where(Product.user_id == user.user_id)
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.where(
            or_(Product.name.ilike(like), Product.sku.ilike(like), Product.category.ilike(like))
        )
    rows = db.scalars(query.order_by(Product.category, Product.name)).all()
    return [ProductOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/categories")
def categories(user: CurrentUser, db: Annotated[Session, Depends(get_db)]) -> list[str]:
    rows = db.scalars(
        select(Product.category).where(Product.user_id == user.user_id).group_by(Product.category)
    ).all()
    return sorted(rows)


@router.get("/search")
def search_products(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    q: Annotated[str, Query(min_length=1, max_length=500)],
    top_k: Annotated[int, Query(ge=1, le=20)] = 5,
) -> dict[str, Any]:
    """Try the product search the assistant uses. Shows scores, to tune the catalog and model."""
    filters = parse_price_constraints(q)
    has_filter = filters.max_price is not None or filters.min_price is not None
    hits = get_product_context(
        db, user.user_id, q, top_k=top_k, filters=filters if has_filter else None, min_score=0
    )
    db.commit()  # the search may have (re)built product vectors
    return {
        "query": q,
        "filters": {"min_price": filters.min_price, "max_price": filters.max_price},
        "results": [
            {
                "sku": h.product.sku,
                "name": h.product.name,
                "price": h.product.price,
                "stock_status": h.product.stock_status,
                "score": round(h.score, 3),
                "vector_score": round(h.vector_score, 3),
                "keyword_score": round(h.keyword_score, 3),
            }
            for h in hits
        ],
    }


@router.post("", response_model=ProductOut, status_code=201)
def create_product(body: ProductIn, user: CurrentUser, db: Annotated[Session, Depends(get_db)]) -> ProductOut:
    exists = db.scalar(
        select(func.count())
        .select_from(Product)
        .where(Product.user_id == user.user_id, Product.sku == body.sku)
    )
    if exists:
        raise HTTPException(status_code=409, detail=f"A product with SKU {body.sku} already exists")
    p = Product(user_id=user.user_id, **body.model_dump())
    db.add(p)
    db.commit()
    return ProductOut.model_validate(p, from_attributes=True)


@router.patch("/{product_id}", response_model=ProductOut)
def update_product(
    product_id: str, patch: ProductPatch, user: CurrentUser, db: Annotated[Session, Depends(get_db)]
) -> ProductOut:
    p = _get(db, user.user_id, product_id)
    changes = patch.model_dump(exclude_unset=True)
    if not changes:
        return ProductOut.model_validate(p, from_attributes=True)
    merged = {
        k: getattr(p, k)
        for k in (
            "sku",
            "name",
            "category",
            "price",
            "currency",
            "description",
            "key_attributes",
            "stock_status",
            "source_url",
        )
    }
    merged.update(changes)
    try:
        valid = ProductIn.model_validate(merged)  # same rules as a catalog import
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail=exc.errors(include_url=False, include_context=False)
        ) from None
    for key, value in valid.model_dump().items():
        if key in changes:
            setattr(p, key, value)
    db.commit()
    return ProductOut.model_validate(p, from_attributes=True)


@router.delete("/{product_id}", status_code=204)
def delete_product(product_id: str, user: CurrentUser, db: Annotated[Session, Depends(get_db)]) -> Response:
    p = _get(db, user.user_id, product_id)
    db.delete(p)  # its search vector is deleted with it (ON DELETE CASCADE)
    db.commit()
    return Response(status_code=204)
