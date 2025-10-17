from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from datetime import datetime
from typing import Optional
from app.database.database import get_db
from app.schemas.audit_logs import AuditLogResponse, TransactionLogResponse
from app.services.audit_log_service import AuditLogService, TransactionLogService

router = APIRouter(prefix="/api/audit-logs", tags=["Audit Logs"])


@router.get("", response_model=list[AuditLogResponse])
async def get_logs(
    db: AsyncSession = Depends(get_db),
    actor_id: Optional[UUID] = Query(None),
    resource_type: Optional[str] = Query(None),
    resource_id: Optional[UUID] = Query(None),
    action: Optional[str] = Query(None),
    start_time: Optional[str] = Query(None),
    end_time: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    start_dt = datetime.fromisoformat(start_time) if start_time else None
    end_dt = datetime.fromisoformat(end_time) if end_time else None
    logs = await AuditLogService.get_logs(
        db=db,
        actor_id=actor_id,
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        start_time=start_dt,
        end_time=end_dt,
        limit=limit,
        offset=offset,
    )
    return logs


@router.get("/{actor_id}/user", response_model=list[AuditLogResponse])
async def get_user_log_by_actor_id(actor_id: UUID, db: AsyncSession = Depends(get_db)):
    return await AuditLogService.get_user_log_by_actor_id(db, actor_id)


@router.get("/{log_id}", response_model=AuditLogResponse)
async def get_log_by_id(log_id: UUID, db: AsyncSession = Depends(get_db)):
    return await AuditLogService.get_log_by_id(db, log_id)


# Transaction logs routes
@router.get("/transaction-logs", response_model=list[TransactionLogResponse])
async def get_transaction_logs(
    db: AsyncSession = Depends(get_db),
    vendor_id: Optional[UUID] = Query(None),
    start_time: Optional[str] = Query(None),
    end_time: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    start_dt = datetime.fromisoformat(start_time) if start_time else None
    end_dt = datetime.fromisoformat(end_time) if end_time else None
    logs = await TransactionLogService.get_logs(
        db=db,
        vendor_id=vendor_id,
        start_time=start_dt,
        end_time=end_dt,
        limit=limit,
        offset=offset,
    )
    return logs


@router.get("/transaction-logs/{log_id}", response_model=TransactionLogResponse)
async def get_transaction_log_by_id(log_id: UUID, db: AsyncSession = Depends(get_db)):
    return await TransactionLogService.get_log_by_id(db, log_id)
