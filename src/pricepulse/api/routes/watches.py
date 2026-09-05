from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from pricepulse.api import deps
from pricepulse.api.deps import ReadConn, WriteConn, require_api_key
from pricepulse.api.schemas import WatchIn, WatchOut, WatchPendingOut
from pricepulse.db import watches
from pricepulse.services.watches import (
    AlreadyWatchingError,
    ProductNotFoundError,
    TooManyPendingError,
    request_watch,
)
from pricepulse.storage.outbox import make_outbox

router = APIRouter(prefix="/v1/watches")


@router.post("", response_model=WatchPendingOut, status_code=status.HTTP_202_ACCEPTED)
def create_watch(body: WatchIn, conn: WriteConn) -> dict:
    """Public. Creates an unconfirmed watch and queues a confirmation email."""
    try:
        request_watch(
            conn,
            make_outbox(deps.get_settings()),
            body.product_id,
            body.email,
            body.min_discount_pct,
        )
    except ProductNotFoundError as exc:
        raise HTTPException(404, "product not found") from exc
    except TooManyPendingError as exc:
        raise HTTPException(429, "too many unconfirmed watches for this email") from exc
    except AlreadyWatchingError as exc:
        raise HTTPException(
            409, "already watching this product (check your inbox if you have not confirmed)"
        ) from exc
    return {
        "email": body.email,
        "product_id": body.product_id,
        "min_discount_pct": body.min_discount_pct,
    }


@router.get("", response_model=list[WatchOut], dependencies=[Depends(require_api_key)])
def list_watches(conn: ReadConn, email: str = Query(..., max_length=254)) -> list[dict]:
    return watches.list_for_email(conn, email)


@router.delete(
    "/{watch_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_api_key)],
)
def delete_watch(watch_id: int, conn: WriteConn) -> Response:
    if not watches.delete_by_id(conn, watch_id):
        raise HTTPException(404, "watch not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
