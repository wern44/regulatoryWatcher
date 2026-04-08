"""Chat service re-export so the web layer imports all services from one package."""
from regwatch.rag.chat_service import ChatService

__all__ = ["ChatService"]
