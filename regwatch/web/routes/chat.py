"""Chat routes."""
from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from regwatch.db.models import ChatSession
from regwatch.rag.chat_service import ChatService
from regwatch.rag.retrieval import RetrievalFilters

router = APIRouter(prefix="/chat", tags=["chat"])


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
