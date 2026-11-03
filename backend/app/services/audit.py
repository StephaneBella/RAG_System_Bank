import builtins
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session, aliased

from app.core.request_timing import elapsed_ms
from app.models.audit_log import AuditAction, AuditLog, AuditReason, AuditResult, ResourceType
from app.models.department import Department
from app.models.user import User
from app.schemas.audit import AuditLogResponse
from app.schemas.pagination import Page
from app.services.llm_gateway import GatewayReport, Outcome, Stage
from app.services.rag_context import ContextStats, ExclusionReason

# US 07 T5: an answered call, or one stopped on purpose before the provider, is a success.
LLM_CALL_RESULTS = {
    Outcome.ANSWERED: AuditResult.SUCCESS,
    Outcome.NO_AUTHORIZED_CONTEXT: AuditResult.SUCCESS,
    Outcome.PROMPT_TOO_LONG: AuditResult.SUCCESS,
    Outcome.FAILED: AuditResult.FAILURE,
}


def _count(value: object, name: str, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"gateway report {name} must be a non-negative int")
    return value


def _safe_error_type(name: object) -> str:
    """A built-in exception name, otherwise ComponentError: never a custom class name."""
    if type(name) is str:
        kind = getattr(builtins, name, None)
        if isinstance(kind, type) and issubclass(kind, BaseException):
            return name
    return "ComponentError"


def _llm_call_details(report: GatewayReport) -> dict[str, object]:
    """The closed list of logged gateway fields: enum values and integers, never text.

    The question, its length (decision H8 is open), the prompt, the answer, values,
    tokens and source names are never logged. Discarded extracts are counted by reason.
    """
    if not isinstance(report, GatewayReport):
        raise TypeError(f"llm_call must be a GatewayReport, not {type(report).__name__}")
    if not isinstance(report.outcome, Outcome):
        raise TypeError("gateway report outcome is not a gateway outcome")
    failed = report.outcome is Outcome.FAILED
    if report.failed_stage is not None and not isinstance(report.failed_stage, Stage):
        raise ValueError("gateway report failed_stage is not a gateway stage")
    if failed != (report.failed_stage is not None) or (
        not failed and report.error_type is not None
    ):
        raise ValueError("gateway report outcome, stage and error type are inconsistent")
    context = report.context
    if context is not None and not isinstance(context, ContextStats):
        raise ValueError("gateway report context must be context counters")
    excluded: dict[str, int | None] = {}
    if context is not None:
        for reason, count in context.excluded.items():
            if not isinstance(reason, ExclusionReason):
                raise TypeError("gateway report exclusion reasons must be exclusion reasons")
            excluded[reason.value] = _count(count, "exclusion count")
    return {
        "outcome": report.outcome.value,
        "failed_stage": report.failed_stage.value if report.failed_stage is not None else None,
        "error_type": _safe_error_type(report.error_type) if failed else None,
        "received": _count(context.received, "received") if context is not None else None,
        "retained": _count(context.retained, "retained") if context is not None else None,
        "excluded": dict(sorted(excluded.items())),
        "prompt_chars": _count(report.prompt_chars, "prompt_chars", optional=True),
        "answer_chars": _count(report.answer_chars, "answer_chars", optional=True),
        "tokens_restored": _count(report.tokens_restored, "tokens_restored"),
        "tokens_withheld": _count(report.tokens_withheld, "tokens_withheld"),
        "gateway_ms": _count(report.duration_ms, "duration_ms"),
    }


def log_audit_event(
    db: Session,
    *,
    user: User | None,
    action: AuditAction,
    resource_type: ResourceType,
    resource_id: int | None,
    result: AuditResult,
    ip_address: str | None = None,
    resource_department_id: int | None = None,
    reason: AuditReason | None = None,
    document_ids: Sequence[int] | None = None,
    llm_call: GatewayReport | None = None,
) -> None:
    """Persist an audit event without storing credentials or tokens.

    details is built from enums, integer ids and the closed list of gateway counters
    only, so it can never hold free text.
    """
    details: dict[str, object] = {}
    if reason is not None:
        details["reason"] = reason.value
    if document_ids is not None:
        details["document_ids"] = [int(document_id) for document_id in document_ids]
    if llm_call is not None:
        details["llm_call"] = _llm_call_details(llm_call)

    event = AuditLog(
        user_id=user.id if user is not None else None,
        action=action.value,
        resource_type=resource_type.value,
        resource_id=resource_id,
        result=result.value,
        timestamp=datetime.now(UTC).replace(tzinfo=None),
        ip_address=ip_address,
        user_department_id=user.department_id if user is not None else None,
        resource_department_id=resource_department_id,
        duration_ms=elapsed_ms(),
        details=details or None,
    )
    db.add(event)
    db.flush()


def log_gateway_call(
    db: Session, *, user: User, report: GatewayReport, ip_address: str | None
) -> None:
    """Persist one call to the LLM gateway (US 07 T5, MIN-LLM-09) as a single event.

    The gateway stays independent of HTTP and of the database: its caller (a future
    route) passes the report of ``ask``, or of the ``GatewayError`` it raised, with the
    authenticated user and the client address, then commits. The report must belong to
    that user: its ``caller_id`` has to be the user's id.
    """
    if not isinstance(user, User):
        raise TypeError(f"user must be a User, not {type(user).__name__}")
    if not isinstance(report, GatewayReport):
        raise TypeError(f"report must be a GatewayReport, not {type(report).__name__}")
    if type(report.caller_id) is not int or report.caller_id != user.id:
        raise ValueError("gateway report caller_id does not match the user")
    if not isinstance(report.outcome, Outcome):
        raise TypeError("gateway report outcome is not a gateway outcome")
    if ip_address is not None and type(ip_address) is not str:
        raise TypeError(f"ip_address must be a str, not {type(ip_address).__name__}")
    log_audit_event(
        db,
        user=user,
        action=AuditAction.LLM_CALL,
        resource_type=ResourceType.LLM_GATEWAY,
        resource_id=None,
        result=LLM_CALL_RESULTS[report.outcome],
        ip_address=ip_address,
        llm_call=report,
    )


PERIODS = {"today", "last7", "last30", "month"}

UserDepartment = aliased(Department)
ResourceDepartment = aliased(Department)


def _to_response(row: Row) -> AuditLogResponse:
    log, email, user_department_name, resource_department_name = row
    return AuditLogResponse(
        id=log.id,
        user_id=log.user_id,
        user_email=email,
        action=log.action,
        resource_type=log.resource_type,
        resource_id=log.resource_id,
        result=log.result,
        ip_address=log.ip_address,
        timestamp=log.timestamp,
        user_department_id=log.user_department_id,
        user_department_name=user_department_name,
        resource_department_id=log.resource_department_id,
        resource_department_name=resource_department_name,
        duration_ms=log.duration_ms,
        details=log.details,
    )


def _period_start(period: str | None) -> datetime | None:
    now = datetime.now(UTC).replace(tzinfo=None)
    if period == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "last7":
        return now - timedelta(days=7)
    if period == "last30":
        return now - timedelta(days=30)
    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return None


def _base_query(
    *,
    action: str | None,
    result: str | None,
    user_email: str | None,
    period: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
) -> Select:
    query = (
        select(AuditLog, User.email, UserDepartment.name, ResourceDepartment.name)
        .outerjoin(User, AuditLog.user_id == User.id)
        .outerjoin(UserDepartment, AuditLog.user_department_id == UserDepartment.id)
        .outerjoin(ResourceDepartment, AuditLog.resource_department_id == ResourceDepartment.id)
    )
    if action:
        query = query.where(AuditLog.action == action)
    if result:
        query = query.where(AuditLog.result == result)
    if user_email and user_email.strip():
        query = query.where(User.email.ilike(f"%{user_email.strip()}%"))
    start = _period_start(period)
    if start is not None:
        query = query.where(AuditLog.timestamp >= start)
    if date_from is not None:
        query = query.where(AuditLog.timestamp >= date_from)
    if date_to is not None:
        query = query.where(AuditLog.timestamp <= date_to)
    return query


def list_audit_logs(
    db: Session,
    *,
    page: int = 1,
    page_size: int = 20,
    action: str | None = None,
    result: str | None = None,
    user_email: str | None = None,
    period: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> Page[AuditLogResponse]:
    filters = {
        "action": action,
        "result": result,
        "user_email": user_email,
        "period": period,
        "date_from": date_from,
        "date_to": date_to,
    }
    count_query = _base_query(**filters).with_only_columns(func.count(AuditLog.id))
    total = db.scalar(count_query) or 0

    rows = db.execute(
        _base_query(**filters)
        .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    items = [_to_response(row) for row in rows]

    pages = (total + page_size - 1) // page_size if total else 0
    return Page[AuditLogResponse](
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
        has_next=page < pages,
        has_prev=page > 1 and pages > 0,
    )


def export_audit_logs(
    db: Session,
    *,
    action: str | None = None,
    result: str | None = None,
    user_email: str | None = None,
    period: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[AuditLogResponse]:
    rows = db.execute(
        _base_query(
            action=action,
            result=result,
            user_email=user_email,
            period=period,
            date_from=date_from,
            date_to=date_to,
        ).order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
    ).all()
    return [_to_response(row) for row in rows]
