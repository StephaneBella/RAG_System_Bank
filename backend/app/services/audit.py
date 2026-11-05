import builtins
import re
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
from app.services.llm_request import CallKind, CallOutcome, LLMRequestContext, ProviderCallReport
from app.services.rag_context import ContextStats, ExclusionReason

# US 07 T5: an answered call, or one stopped on purpose before the provider, is a success.
LLM_CALL_RESULTS = {
    Outcome.ANSWERED: AuditResult.SUCCESS,
    Outcome.NO_AUTHORIZED_CONTEXT: AuditResult.SUCCESS,
    Outcome.PROMPT_TOO_LONG: AuditResult.SUCCESS,
    Outcome.FAILED: AuditResult.FAILURE,
}

# RAG agent: one event per provider call. A prompt refused for its size never reached
# the provider; reaching the call limit of a request is reported as a failure.
PROVIDER_CALL_RESULTS = {
    CallOutcome.COMPLETED: AuditResult.SUCCESS,
    CallOutcome.PROMPT_TOO_LONG: AuditResult.SUCCESS,
    CallOutcome.CALL_LIMIT_EXCEEDED: AuditResult.FAILURE,
    CallOutcome.FAILED: AuditResult.FAILURE,
}
_REQUEST_ID = re.compile(r"[0-9a-f]{32}")


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


def _provider_count(value: object, name: str, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"provider call report {name} must be a non-negative int")
    return value


def _provider_call_details(report: ProviderCallReport) -> dict[str, object]:
    """Validate one provider call report and build its closed list of logged fields.

    Only enum values, non-negative integers, null and the request id drawn by the server
    are logged: never a prompt, an answer, a tool argument, a token, a user text or the
    session id given by the client. The caller identity is checked by the callers.

    ``prompt_chars`` is validated but always logged as null (decision H8): the prompt of
    an agent step or of a query rewrite is a constant text plus the question, so its
    size would give away the length of the question.
    """
    if not isinstance(report, ProviderCallReport):
        raise TypeError(f"provider_call must be a ProviderCallReport, not {type(report).__name__}")
    if type(report.request_id) is not str or not _REQUEST_ID.fullmatch(report.request_id):
        raise ValueError("provider call report request_id is not a server request id")
    if not isinstance(report.kind, CallKind):
        raise TypeError("provider call report kind is not a call kind")
    if not isinstance(report.outcome, CallOutcome):
        raise TypeError("provider call report outcome is not a call outcome")
    failed = report.outcome is CallOutcome.FAILED
    if failed != (report.error_type is not None):
        raise ValueError("provider call report outcome and error type are inconsistent")
    prompt_chars = _provider_count(report.prompt_chars, "prompt_chars", optional=True)
    details = {
        "request_id": report.request_id,
        "call_index": _provider_count(report.call_index, "call_index"),
        "call_kind": report.kind.value,
        "outcome": report.outcome.value,
        "error_type": _safe_error_type(report.error_type) if failed else None,
        "prompt_chars": None,  # H8: never logged, see the docstring
        "answer_chars": _provider_count(report.answer_chars, "answer_chars", optional=True),
        "tokens_sent": _provider_count(report.tokens_sent, "tokens_sent"),
        "tokens_restored": _provider_count(report.tokens_restored, "tokens_restored"),
        "tokens_withheld": _provider_count(report.tokens_withheld, "tokens_withheld"),
        "duration_ms": _provider_count(report.duration_ms, "duration_ms"),
    }
    tokens_in_answer = details["tokens_restored"] > 0 or details["tokens_withheld"] > 0
    if tokens_in_answer and details["answer_chars"] is None:
        raise ValueError("provider call report counts tokens in an answer it does not have")
    if details["tokens_restored"] > 0 and details["tokens_sent"] == 0:
        raise ValueError("provider call report restores tokens it never sent")
    answered = details["answer_chars"] is not None or tokens_in_answer
    if report.outcome is CallOutcome.CALL_LIMIT_EXCEEDED and (
        answered or prompt_chars is not None or details["tokens_sent"] > 0
    ):
        raise ValueError("a provider call refused by the call limit cannot have sent anything")
    if report.outcome is CallOutcome.PROMPT_TOO_LONG and (
        answered or prompt_chars is None or details["tokens_sent"] > 0
    ):
        raise ValueError("a provider call refused for its size cannot have an answer")
    if report.outcome is CallOutcome.COMPLETED and prompt_chars is None:
        raise ValueError("a completed provider call must have sent a prompt")
    return details


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
    _add_event(
        db,
        user=user,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        result=result,
        ip_address=ip_address,
        resource_department_id=resource_department_id,
        details=details,
    )


def _add_event(
    db: Session,
    *,
    user: User | None,
    action: AuditAction,
    resource_type: ResourceType,
    resource_id: int | None,
    result: AuditResult,
    ip_address: str | None,
    resource_department_id: int | None,
    details: dict[str, object],
) -> None:
    """Add one event and flush, without committing. Private: ``details`` must already be
    built by a public function from validated, typed values only."""
    if "llm_call" in details and "provider_call" in details:
        raise ValueError("an event is either a gateway call or a provider call")
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


def _checked_provider_call(user: object, report: object, ip_address: object) -> dict[str, object]:
    """Identity, client address and content checks of one provider call; nothing written.

    The report must belong to the authenticated user: its ``caller_id`` has to be
    exactly the user's id (an int, never a bool).
    """
    if not isinstance(user, User):
        raise TypeError(f"user must be a User, not {type(user).__name__}")
    if not isinstance(report, ProviderCallReport):
        raise TypeError(f"report must be a ProviderCallReport, not {type(report).__name__}")
    if type(report.caller_id) is not int or type(user.id) is not int:
        raise ValueError("provider call report caller_id does not match the user")
    if report.caller_id != user.id:
        raise ValueError("provider call report caller_id does not match the user")
    if ip_address is not None and type(ip_address) is not str:
        raise TypeError(f"ip_address must be a str, not {type(ip_address).__name__}")
    return _provider_call_details(report)


def _add_provider_call(
    db: Session,
    *,
    user: User,
    report: ProviderCallReport,
    ip_address: str | None,
    details: dict[str, object],
) -> None:
    _add_event(
        db,
        user=user,
        action=AuditAction.LLM_CALL,
        resource_type=ResourceType.LLM_GATEWAY,
        resource_id=None,
        result=PROVIDER_CALL_RESULTS[report.outcome],
        ip_address=ip_address,
        resource_department_id=None,
        details={"provider_call": details},
    )


def log_provider_call(
    db: Session, *, user: User, report: ProviderCallReport, ip_address: str | None
) -> None:
    """Persist one provider call of a RAG request as one LLM_CALL event.

    Every check (identity, request id, enums, counters, consistency) runs before the
    write. The caller commits.
    """
    details = _checked_provider_call(user, report, ip_address)
    _add_provider_call(db, user=user, report=report, ip_address=ip_address, details=details)


def log_request_calls(
    db: Session, *, user: User, context: LLMRequestContext, ip_address: str | None
) -> int:
    """Persist every provider call of one request, or none of them.

    The context must be closed: while it is open, a call still running would leave its
    report out. All the reports of ``context`` are checked first: same user, the request
    id of the context, unique call indexes. If one of them is refused, no event is added.
    The caller (the future RAG route) calls it once the request is over, then commits.
    Returns the number of events added.
    """
    if not isinstance(context, LLMRequestContext):
        raise TypeError(f"context must be an LLMRequestContext, not {type(context).__name__}")
    if not isinstance(user, User):
        raise TypeError(f"user must be a User, not {type(user).__name__}")
    if type(context.caller_id) is not int or context.caller_id != user.id:
        raise ValueError("request context caller_id does not match the user")
    if context.closed is not True:
        raise ValueError("the request context must be closed before its calls are logged")
    reports = context.reports()
    checked = []
    indexes: set[int] = set()
    for report in reports:
        details = _checked_provider_call(user, report, ip_address)
        if report.request_id != context.request_id:
            raise ValueError("provider call report does not belong to this request")
        if report.call_index in indexes:
            raise ValueError("provider call reports must have unique call indexes")
        indexes.add(report.call_index)
        checked.append((report, details))
    for report, details in checked:
        _add_provider_call(db, user=user, report=report, ip_address=ip_address, details=details)
    return len(checked)


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
