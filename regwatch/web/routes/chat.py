"""Chat routes."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from regwatch.db.models import (
    ChatMessage,
    ChatSession,
    LifecycleStage,
    Regulation,
    RegulationApplicability,
)
from regwatch.rag.chat_service import ChatService
from regwatch.rag.retrieval import RetrievalFilters

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/ask", response_class=HTMLResponse)
def chat_ask_page(request: Request) -> HTMLResponse:
    """Render the session-less ask page with a scope picker modal."""
    templates = request.app.state.templates
    with request.app.state.session_factory() as session:
        regs = (
            session.query(Regulation)
            .order_by(Regulation.reference_number)
            .all()
        )
        scope_tree = [
            (
                r,
                sorted(
                    r.versions,
                    key=lambda v: v.version_number,
                    reverse=True,
                ),
            )
            for r in regs
        ]
        return templates.TemplateResponse(
            request,
            "chat/ask.html",
            {"active": "chat", "scope_tree": scope_tree},
        )


@router.post("/ask", response_class=PlainTextResponse)
def chat_ask_adhoc(
    request: Request,
    query: Annotated[str, Form(...)],
    version_ids: Annotated[list[int] | None, Form()] = None,
) -> PlainTextResponse:
    """Session-less scoped Q&A: returns the generated answer as plain text."""
    filters = RetrievalFilters(version_ids=list(version_ids or []))
    with request.app.state.session_factory() as session:
        svc = ChatService(session, ollama=request.app.state.llm_client)
        answer = svc.ask_adhoc(query, filters=filters)
    return PlainTextResponse(answer)


@router.get("", response_class=HTMLResponse)
def chat_list(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    with request.app.state.session_factory() as session:
        sessions = (
            session.query(ChatSession)
            .order_by(ChatSession.created_at.desc())
            .all()
        )
    return templates.TemplateResponse(
        request, "chat/list.html", {"active": "chat", "sessions": sessions}
    )


@router.get("/new", response_class=HTMLResponse)
def chat_new(request: Request) -> HTMLResponse:
    """Render the new-session page with scope picker."""
    templates = request.app.state.templates
    cfg = request.app.state.config
    auth_types = [a.type for a in cfg.entity.authorizations]

    with request.app.state.session_factory() as session:
        # Build per-regulation applicability map: {reg_id: set of auth_types}
        all_applicabilities = session.query(RegulationApplicability).all()
        reg_entity_map: dict[int, set[str]] = {}
        for a in all_applicabilities:
            reg_entity_map.setdefault(a.regulation_id, set()).add(a.authorization_type)

        all_regs = (
            session.query(Regulation)
            .order_by(Regulation.reference_number)
            .all()
        )
        reg_options = []
        for r in all_regs:
            entities = reg_entity_map.get(r.regulation_id, set())
            reg_options.append({
                "regulation_id": r.regulation_id,
                "reference_number": r.reference_number,
                "title": r.title,
                "lifecycle_stage": r.lifecycle_stage.value,
                "entity_types": sorted(entities),
                "in_scope": bool(entities & set(auth_types)),
                "in_force": r.lifecycle_stage == LifecycleStage.IN_FORCE,
            })

    return templates.TemplateResponse(
        request,
        "chat/new.html",
        {
            "active": "chat",
            "reg_options": reg_options,
            "auth_types": auth_types,
        },
    )


@router.post("")
def chat_create(
    request: Request,
    title: str = Form(...),
    regulation_ids: Annotated[list[int] | None, Form()] = None,
) -> RedirectResponse:
    filters = RetrievalFilters()
    if regulation_ids:
        filters.regulation_ids = list(regulation_ids)

    with request.app.state.session_factory() as session:
        svc = ChatService(
            session, ollama=request.app.state.llm_client
        )
        new_session = svc.create_session(title=title, filters=filters)

        # Keep only the 10 most recent sessions.
        all_ids = [
            r[0] for r in session.query(ChatSession.session_id)
            .order_by(ChatSession.created_at.desc())
            .all()
        ]
        if len(all_ids) > 10:
            old_ids = all_ids[10:]
            session.query(ChatMessage).filter(
                ChatMessage.session_id.in_(old_ids)
            ).delete(synchronize_session=False)
            session.query(ChatSession).filter(
                ChatSession.session_id.in_(old_ids)
            ).delete(synchronize_session=False)

        session.commit()
        sid = new_session.session_id
    return RedirectResponse(url=f"/chat/{sid}", status_code=303)


@router.get("/{session_id}", response_class=HTMLResponse)
def chat_session(request: Request, session_id: int) -> HTMLResponse:
    templates = request.app.state.templates
    with request.app.state.session_factory() as session:
        cs = session.get(ChatSession, session_id)
        if cs is None:
            raise HTTPException(status_code=404)
        svc = ChatService(
            session, ollama=request.app.state.llm_client
        )
        messages = svc.list_messages(session_id)

        # Resolve regulation_ids in the session's filters to display names.
        scope_labels: list[str] = []
        reg_ids = (cs.filters or {}).get("regulation_ids", [])
        if reg_ids:
            regs = (
                session.query(Regulation)
                .filter(Regulation.regulation_id.in_(reg_ids))
                .order_by(Regulation.reference_number)
                .all()
            )
            scope_labels = [r.reference_number for r in regs]

    return templates.TemplateResponse(
        request,
        "chat/session.html",
        {
            "active": "chat",
            "session": cs,
            "messages": messages,
            "scope_labels": scope_labels,
        },
    )


@router.post("/{session_id}/ask")
def chat_ask(
    request: Request, session_id: int, question: str = Form(...)
) -> RedirectResponse:
    """Non-streaming ask endpoint. SSE streaming can layer on later."""
    with request.app.state.session_factory() as session:
        svc = ChatService(
            session, ollama=request.app.state.llm_client
        )
        svc.ask(session_id, question)
        session.commit()
    return RedirectResponse(url=f"/chat/{session_id}", status_code=303)
