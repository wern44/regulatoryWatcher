"""Chat routes."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from regwatch.db.models import ChatSession, Regulation
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


@router.post("")
def chat_create(request: Request, title: str = Form(...)) -> RedirectResponse:
    with request.app.state.session_factory() as session:
        svc = ChatService(
            session, ollama=request.app.state.llm_client
        )
        new_session = svc.create_session(title=title, filters=RetrievalFilters())
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
    return templates.TemplateResponse(
        request,
        "chat/session.html",
        {"active": "chat", "session": cs, "messages": messages},
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
