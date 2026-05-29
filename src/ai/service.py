"""Business-level local AI project assistant service."""

from __future__ import annotations

from typing import Any, Dict, List

from src.ai.conversation import ConversationStore
from src.ai.document_library import DocumentLibraryService
from src.ai.lmstudio import LMStudioManager
from src.ai.project_assistant import ProjectAssistant
from src.ai.provider import AIProviderClient
from src.ai.rag import RAGService
from src.ai.settings import AISettings
from src.storage.sqlite_store import SQLiteStore


class AIService:
    """Small facade used by control CLI and future UI surfaces."""

    def __init__(self, storage: SQLiteStore, settings: AISettings, profile: str = ""):
        self.storage = storage
        self.settings = settings
        self.profile = profile
        self.provider = AIProviderClient(settings)
        self.assistant = ProjectAssistant(storage, settings)
        self.library = DocumentLibraryService(storage, settings)
        self.conversations = ConversationStore(storage, profile=profile)

    def test_connection(self) -> Dict[str, Any]:
        return self.provider.test_connection()

    def diagnose(self) -> Dict[str, Any]:
        if self.settings.provider_type == "lm_studio":
            return LMStudioManager(self.settings).diagnose(check_chat=False)
        self.provider.validate()
        return {
            "status": "configured",
            "provider_type": self.settings.provider_type,
            "base_url": self.settings.base_url,
            "model": self.settings.model,
            "hint": "",
        }

    def lmstudio_models(self) -> Dict[str, Any]:
        manager = LMStudioManager(self.settings)
        return {
            "status": "ok",
            "models": manager.local_models(),
            "lms_path": manager.lms_path,
        }

    def generate_project_description(
        self,
        group_id: str = "",
        project_id: str = "",
        include_file_snippets: bool = False,
        file_id: str = "",
    ) -> Dict[str, Any]:
        return self.assistant.summarize_project(
            group_id=group_id,
            project_id=project_id,
            include_file_snippets=include_file_snippets,
            file_id=file_id,
        )

    def summarize_current_project(
        self,
        group_id: str = "",
        project_id: str = "",
        include_file_snippets: bool = False,
        file_id: str = "",
    ) -> Dict[str, Any]:
        return self.generate_project_description(
            group_id=group_id,
            project_id=project_id,
            include_file_snippets=include_file_snippets,
            file_id=file_id,
        )

    def summarize_selected_file(self, file_id: str) -> Dict[str, Any]:
        return self.assistant.summarize_file(file_id)

    def search_related_files(
        self,
        query: str,
        group_id: str = "",
        extension: str = "",
        limit: int = 30,
    ) -> List[Dict[str, Any]]:
        return self.assistant.search_files(
            query=query,
            group_id=group_id,
            extension=extension,
            limit=limit,
        )

    def document_library_status(self, group_id: str = "", project_id: str = "") -> Dict[str, Any]:
        return self.library.status(group_id=group_id, project_id=project_id)

    def build_document_library(self, group_id: str = "", project_id: str = "") -> Dict[str, Any]:
        return self.library.build(group_id=group_id, project_id=project_id)

    def diagnose_document_library(
        self,
        group_id: str = "",
        project_id: str = "",
        query: str = "",
    ) -> Dict[str, Any]:
        report = self.library.diagnose(
            profile=self.profile,
            group_id=group_id,
            project_id=project_id,
            query=query,
        )
        normalized_query = report.get("query") or query or "README project"
        try:
            rag_context = RAGService(self.storage, self.settings, profile=self.profile).prepare_context(
                question=str(normalized_query),
                group_id=group_id,
                project_id=project_id,
                create_conversation=False,
            )
            report["rag_prompt"] = {
                "prompt_contains_sources": bool(rag_context.get("prompt_contains_sources")),
                "source_count": len(rag_context.get("sources", [])),
                "preview": rag_context.get("prompt_preview", ""),
            }
        except Exception as exc:
            report["rag_prompt"] = {
                "prompt_contains_sources": False,
                "source_count": 0,
                "preview": "",
                "error": str(exc),
            }
        report["provider_called"] = False
        return report

    def list_document_sources(
        self,
        group_id: str = "",
        project_id: str = "",
        status: str = "",
        query: str = "",
        limit: int = 100,
    ) -> Dict[str, Any]:
        return self.library.list_sources(
            group_id=group_id,
            project_id=project_id,
            status=status,
            query=query,
            limit=limit,
        )

    def delete_document_source(
        self,
        source_id: str = "",
        file_id: str = "",
        group_id: str = "",
        project_id: str = "",
    ) -> Dict[str, Any]:
        return self.library.delete_source(
            source_id=source_id,
            file_id=file_id,
            group_id=group_id,
            project_id=project_id,
        )

    def restore_document_source(
        self,
        source_id: str = "",
        file_id: str = "",
        group_id: str = "",
        project_id: str = "",
    ) -> Dict[str, Any]:
        return self.library.restore_source(
            source_id=source_id,
            file_id=file_id,
            group_id=group_id,
            project_id=project_id,
        )

    def search_document_library(
        self,
        query: str,
        group_id: str = "",
        project_id: str = "",
        limit: int = 20,
    ) -> Dict[str, Any]:
        return self.library.search(query=query, group_id=group_id, project_id=project_id, limit=limit)

    def ask_project_question(
        self,
        question: str,
        group_id: str = "",
        project_id: str = "",
        conversation_id: str = "",
        chat_context: List[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        return RAGService(self.storage, self.settings, profile=self.profile).ask(
            question=question,
            group_id=group_id,
            project_id=project_id,
            conversation_id=conversation_id,
            chat_context=chat_context,
        )

    def list_conversations(self, group_id: str = "", project_id: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        return self.conversations.list(group_id=group_id, project_id=project_id, limit=limit)

    def conversation_detail(self, conversation_id: str) -> Dict[str, Any]:
        conversation = self.conversations.get(conversation_id)
        if not conversation:
            raise ValueError("AI conversation not found")
        return {"conversation": conversation, "messages": self.conversations.messages(conversation_id)}

    def clear_conversation(self, conversation_id: str) -> Dict[str, Any]:
        return {"deleted_count": self.conversations.clear(conversation_id)}

    def delete_conversation(self, conversation_id: str) -> Dict[str, Any]:
        return {"deleted": self.conversations.delete(conversation_id)}
