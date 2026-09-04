from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Query

from pricepulse.api import queries
from pricepulse.api.deps import ReadConn
from pricepulse.api.schemas import DealsPage, HistoryPoint, ProductOut

router = APIRouter(prefix="/v1")


@router.get("/deals", response_model=DealsPage)
def deals(
    conn: ReadConn,
    source: str | None = None,
    min_discount: Decimal = Query(Decimal("0"), ge=0, le=100),
    flagged_only: bool = False,
    q: str | None = Query(None, max_length=100),
    limit: int = Query(50, ge=1, le=queries.MAX_LIMIT),
    cursor: str | None = None,
) -> dict:
    items, next_cursor = queries.list_deals(
        conn,
        queries.DealFilters(
            source=source,
            min_discount=min_discount,
            flagged_only=flagged_only,
            q=q,
            limit=limit,
            cursor=cursor,
        ),
    )
    return {"items": items, "next_cursor": next_cursor}


@router.get("/products/{product_id}", response_model=ProductOut)
def product(conn: ReadConn, product_id: int) -> dict:
    return queries.get_product(conn, product_id)


@router.get("/products/{product_id}/history", response_model=list[HistoryPoint])
def history(conn: ReadConn, product_id: int, days: int = Query(90, ge=1, le=730)) -> list[dict]:
    queries.get_product(conn, product_id)  # 404 if unknown
    return queries.get_history(conn, product_id, days)
