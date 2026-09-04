from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from pricepulse.api.deps import ReadConn, WriteConn, require_api_key
from pricepulse.api.schemas import WatchIn, WatchOut

router = APIRouter(prefix="/v1/watches", dependencies=[Depends(require_api_key)])


@router.post("", response_model=WatchOut, status_code=status.HTTP_201_CREATED)
def create_watch(body: WatchIn, conn: WriteConn) -> dict:
    exists = conn.execute(
        text("SELECT 1 FROM product WHERE id = :id"), {"id": body.product_id}
    ).scalar()
    if not exists:
        raise HTTPException(404, "product not found")
    try:
        with conn.begin_nested():
            row = conn.execute(
                text(
                    """
                    INSERT INTO watch (product_id, email, min_discount_pct)
                    VALUES (:product_id, :email, :pct)
                    RETURNING id, product_id, email, min_discount_pct, created_at
                    """
                ),
                {"product_id": body.product_id, "email": body.email, "pct": body.min_discount_pct},
            ).one()
    except IntegrityError as exc:
        raise HTTPException(409, "watch already exists for this product and email") from exc
    return dict(row._mapping)


@router.get("", response_model=list[WatchOut])
def list_watches(conn: ReadConn, email: str = Query(..., max_length=254)) -> list[dict]:
    rows = conn.execute(
        text(
            "SELECT id, product_id, email, min_discount_pct, created_at FROM watch "
            "WHERE email = :email ORDER BY id"
        ),
        {"email": email},
    ).all()
    return [dict(r._mapping) for r in rows]


@router.delete("/{watch_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_watch(watch_id: int, conn: WriteConn) -> Response:
    deleted = conn.execute(text("DELETE FROM watch WHERE id = :id"), {"id": watch_id}).rowcount
    if not deleted:
        raise HTTPException(404, "watch not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
