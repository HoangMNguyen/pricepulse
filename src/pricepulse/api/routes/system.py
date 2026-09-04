from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

import pricepulse
from pricepulse.api import queries
from pricepulse.api.deps import ReadConn
from pricepulse.api.schemas import Health, RunOut, SourceStats

router = APIRouter()


@router.get("/health", response_model=Health)
def health(conn: ReadConn) -> Health:
    try:
        conn.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(503, f"db unreachable: {type(exc).__name__}") from exc
    return Health(status="ok", db="ok", version=pricepulse.__version__)


@router.get("/v1/runs", response_model=list[RunOut])
def runs(conn: ReadConn, limit: int = Query(20, ge=1, le=200)) -> list[dict]:
    return queries.list_runs(conn, limit)


@router.get("/v1/stats", response_model=list[SourceStats])
def stats(conn: ReadConn) -> list[dict]:
    return queries.stats(conn)
