from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from pricepulse.api import deps
from pricepulse.api.deps import ReadConn, WriteConn, require_api_key
from pricepulse.api.schemas import WatchIn, WatchOut, WatchPendingOut
from pricepulse.storage.outbox import make_outbox

router = APIRouter(prefix="/v1/watches")

MAX_UNCONFIRMED_PER_EMAIL = 5


@router.post("", response_model=WatchPendingOut, status_code=status.HTTP_202_ACCEPTED)
def create_watch(body: WatchIn, conn: WriteConn) -> dict:
    """Public. Creates an unconfirmed watch and queues a confirmation email."""
    product = conn.execute(
        text("SELECT name, url FROM product WHERE id = :id"), {"id": body.product_id}
    ).first()
    if product is None:
        raise HTTPException(404, "product not found")
    pending = conn.execute(
        text("SELECT count(*) FROM watch WHERE email = :email AND confirmed_at IS NULL"),
        {"email": body.email},
    ).scalar_one()
    if pending >= MAX_UNCONFIRMED_PER_EMAIL:
        raise HTTPException(429, "too many unconfirmed watches for this email")
    token = secrets.token_urlsafe(32)
    try:
        with conn.begin_nested():
            conn.execute(
                text(
                    """
                    INSERT INTO watch (product_id, email, min_discount_pct, token,
                                       confirmation_sent_at)
                    VALUES (:product_id, :email, :pct, :token, now())
                    """
                ),
                {
                    "product_id": body.product_id,
                    "email": body.email,
                    "pct": body.min_discount_pct,
                    "token": token,
                },
            )
    except IntegrityError as exc:
        raise HTTPException(
            409, "already watching this product (check your inbox if you have not confirmed)"
        ) from exc
    make_outbox(deps.get_settings()).put(
        {
            "kind": "watch_confirm",
            "email": body.email,
            "product_id": body.product_id,
            "product_name": product.name,
            "product_url": product.url,
            "min_discount_pct": str(body.min_discount_pct),
            "token": token,
        }
    )
    return {
        "email": body.email,
        "product_id": body.product_id,
        "min_discount_pct": body.min_discount_pct,
    }


@router.get("", response_model=list[WatchOut], dependencies=[Depends(require_api_key)])
def list_watches(conn: ReadConn, email: str = Query(..., max_length=254)) -> list[dict]:
    rows = conn.execute(
        text(
            "SELECT id, product_id, email, min_discount_pct, created_at, confirmed_at FROM watch "
            "WHERE email = :email ORDER BY id"
        ),
        {"email": email},
    ).all()
    return [dict(r._mapping) for r in rows]


@router.delete(
    "/{watch_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_api_key)],
)
def delete_watch(watch_id: int, conn: WriteConn) -> Response:
    deleted = conn.execute(text("DELETE FROM watch WHERE id = :id"), {"id": watch_id}).rowcount
    if not deleted:
        raise HTTPException(404, "watch not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
