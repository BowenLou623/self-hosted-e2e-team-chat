"""Simple local RAG question-answering for Phase 9."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from src.ai.conversation import ConversationStore
from src.ai.document_library import DocumentLibraryService
from src.ai.provider import AIProviderClient
from src.ai.settings import AISettings
from src.storage.sqlite_store import SQLiteStore


class RAGService:
    """Retrieves local chunks and asks the selected provider with citations."""

    def __init__(self, storage: SQLiteStore, settings: AISettings, profile: str = ""):
        self.storage = storage
        self.settings = settings
        self.profile = profile
        self.library = DocumentLibraryService(storage, settings)
        self.conversations = ConversationStore(storage, profile=profile)
        self.provider = AIProviderClient(settings)

    def ask(
        self,
        question: str,
        group_id: str = "",
        project_id: str = "",
        conversation_id: str = "",
        chat_context: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        question = (question or "").strip()
        if not group_id:
            raise ValueError("请选择项目后再提问")
        if not question:
            raise ValueError("问题不能为空")

        context = self.prepare_context(
            question=question,
            group_id=group_id,
            project_id=project_id,
            conversation_id=conversation_id,
            chat_context=chat_context,
            create_conversation=True,
        )
        conversation_id = context["conversation_id"]
        retrieval = context["retrieval"]
        recent_messages = context["recent_messages"]
        sources = context["sources"]

        user_message = self.conversations.add_message(
            conversation_id=conversation_id,
            role="user",
            content=question,
            metadata={"retrieval_query": context["retrieval_query"]},
        )

        if not sources:
            answer = "当前文档库里没有足够依据回答这个问题。请先构建文档库，或换一个更具体的问题。"
            assistant_message = self.conversations.add_message(
                conversation_id,
                "assistant",
                answer,
                metadata=self._assistant_metadata(retrieval, sources),
                sources=sources,
            )
            return self._response(conversation_id, answer, sources, retrieval, user_message, assistant_message)

        self.provider.validate()
        messages = self._build_messages(question, recent_messages, sources, chat_context=chat_context)
        answer = self.provider.chat(messages).strip()
        assistant_message = self.conversations.add_message(
            conversation_id,
            "assistant",
            answer,
            metadata=self._assistant_metadata(retrieval, sources),
            sources=sources,
        )
        return self._response(conversation_id, answer, sources, retrieval, user_message, assistant_message)

    def prepare_context(
        self,
        question: str,
        group_id: str = "",
        project_id: str = "",
        conversation_id: str = "",
        chat_context: Optional[List[Dict[str, Any]]] = None,
        create_conversation: bool = False,
    ) -> Dict[str, Any]:
        """Build retrieval and prompt context without calling the AI provider."""
        question = (question or "").strip()
        if not group_id:
            raise ValueError("请选择项目后再提问")
        if not question:
            raise ValueError("问题不能为空")

        conversation: Dict[str, Any] = {}
        if create_conversation:
            conversation = self.conversations.get_or_create(
                conversation_id=conversation_id,
                group_id=group_id,
                project_id=project_id,
                settings=self.settings,
                title=question[:80],
            )
            conversation_id = conversation["conversation_id"]
        elif conversation_id:
            conversation = self.conversations.get(conversation_id) or {}

        recent_questions = self.conversations.recent_user_questions(conversation_id, limit=2) if conversation_id else []
        chat_terms = self._chat_context_terms(chat_context or [])
        retrieval_query = "\n".join([question] + list(reversed(recent_questions)) + chat_terms)
        retrieval = self.library.retrieve_for_rag(
            retrieval_query,
            group_id=group_id,
            project_id=project_id,
            limit=int(self.settings.rag_max_chunks or 8),
            per_file_limit=3,
        )
        recent_messages = (
            self.conversations.recent_messages(
                conversation_id,
                turns=int(self.settings.conversation_recent_turns or 6),
            )
            if conversation_id
            else []
        )
        sources = self._source_refs(retrieval.get("results", []))
        messages = self._build_messages(question, recent_messages, sources, chat_context=chat_context or [])
        prompt = messages[-1]["content"] if messages else ""
        return {
            "conversation": conversation,
            "conversation_id": conversation_id,
            "question": question,
            "retrieval_query": retrieval_query,
            "retrieval": retrieval,
            "recent_messages": recent_messages,
            "chat_context": chat_context or [],
            "sources": sources,
            "messages": messages,
            "prompt_preview": prompt[:4000],
            "prompt_contains_sources": bool(sources and "来源片段:" in prompt and "[S1]" in prompt),
            "provider_called": False,
        }

    def _build_messages(
        self,
        question: str,
        recent_messages: List[Dict[str, Any]],
        sources: List[Dict[str, Any]],
        chat_context: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, str]]:
        system = (
            "你是一个谨慎的本地项目助手。只能基于用户提供的本地文档库上下文回答。"
            "不要声称读取了未提供的文件。不要执行命令、不要修改文件、不要联网搜索。"
            "默认使用中文回答。关键结论必须用 [S1] 这样的来源编号引用。"
            "如果上下文不足，请明确说明当前文档库里没有足够依据。"
        )
        user = self._user_prompt(question, recent_messages, sources, chat_context=chat_context or [])
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def _user_prompt(
        self,
        question: str,
        recent_messages: List[Dict[str, Any]],
        sources: List[Dict[str, Any]],
        chat_context: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        budget = max(2000, min(int(self.settings.rag_max_context_chars or 12000), 40000))
        header = [
            "本地项目 RAG 上下文",
            f"profile: {self.profile}",
            f"provider: {self.settings.provider_type}",
            f"model: {self.settings.model}",
            "retrieval_mode: fts_bm25",
            f"timestamp: {int(time.time())}",
            "",
            "来源片段:",
        ]
        parts = ["\n".join(header)]
        used = len(parts[0])
        for source in sources:
            text = str(source.get("text") or "")
            block = (
                f"\n[{source['source_index']}] {source['relative_path']}"
                f":{source['line_start']}-{source['line_end']}"
                f" size={source.get('size', 0)} bytes\n"
                f"{text}\n"
            )
            if used + len(block) > budget:
                break
            parts.append(block)
            used += len(block)

        conversation_block = self._conversation_block(recent_messages, max_chars=max(1000, budget // 4))
        if conversation_block:
            parts.append("\n最近对话上下文:\n" + conversation_block)

        chat_block = self._chat_context_block(chat_context or [], max_chars=max(1000, budget // 4))
        if chat_block:
            parts.append("\n当前聊天窗口上下文:\n" + chat_block)

        parts.append(
            "\n用户问题:\n"
            + question
            + "\n\n回答要求: 只根据来源片段回答；关键结论后标注 [S1] 形式引用；不要编造未提供的文件内容。"
        )
        return "\n".join(parts)

    def _chat_context_terms(self, messages: List[Dict[str, Any]], max_items: int = 6) -> List[str]:
        terms: List[str] = []
        for message in messages[-max_items:]:
            content = str(message.get("content") or "").strip()
            if content:
                terms.append(content[:240])
        return terms

    def _chat_context_block(self, messages: List[Dict[str, Any]], max_chars: int) -> str:
        lines: List[str] = []
        used = 0
        for message in messages:
            sender = str(message.get("sender") or message.get("sender_id") or "")
            role = "我" if message.get("is_self") else (sender or "对方")
            content = str(message.get("content") or "")
            line = f"{role}: {content[:1000]}"
            if used + len(line) > max_chars:
                break
            lines.append(line)
            used += len(line)
        return "\n".join(lines)

    def _conversation_block(self, messages: List[Dict[str, Any]], max_chars: int) -> str:
        lines: List[str] = []
        used = 0
        for message in messages:
            role = "用户" if message.get("role") == "user" else "助手"
            content = str(message.get("content") or "")
            line = f"{role}: {content[:1000]}"
            if used + len(line) > max_chars:
                break
            lines.append(line)
            used += len(line)
        return "\n".join(lines)

    def _source_refs(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        refs: List[Dict[str, Any]] = []
        for index, item in enumerate(results, start=1):
            snippet = str(item.get("snippet") or item.get("text") or "")
            refs.append({
                "source_index": f"S{index}",
                "file_id": item.get("file_id", ""),
                "source_id": item.get("source_id", ""),
                "chunk_id": item.get("chunk_id", ""),
                "relative_path": item.get("relative_path", ""),
                "absolute_path": item.get("absolute_path", ""),
                "line_start": int(item.get("line_start") or 1),
                "line_end": int(item.get("line_end") or 1),
                "snippet": snippet[:600],
                "score": float(item.get("score") or 0),
                "sha256": item.get("sha256", ""),
                "mtime_ns": int(item.get("mtime_ns") or 0),
                "size": int(item.get("size") or 0),
                "text": item.get("text", ""),
            })
        return refs

    def _assistant_metadata(self, retrieval: Dict[str, Any], sources: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "provider": self._provider_snapshot(),
            "retrieval": {
                "mode": retrieval.get("retrieval_mode", "fts_bm25"),
                "query": retrieval.get("query", ""),
                "candidate_count": retrieval.get("candidate_count", 0),
                "source_count": len(sources),
            },
            "source_refs": [{key: value for key, value in source.items() if key != "text"} for source in sources],
        }

    def _response(
        self,
        conversation_id: str,
        answer: str,
        sources: List[Dict[str, Any]],
        retrieval: Dict[str, Any],
        user_message: Dict[str, Any],
        assistant_message: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "answer": answer,
            "conversation_id": conversation_id,
            "user_message": user_message,
            "assistant_message": assistant_message,
            "sources": [{key: value for key, value in source.items() if key != "text"} for source in sources],
            "retrieval": {
                "mode": retrieval.get("retrieval_mode", "fts_bm25"),
                "query": retrieval.get("query", ""),
                "candidate_count": retrieval.get("candidate_count", 0),
                "source_count": len(sources),
            },
            "provider": self._provider_snapshot(),
            "privacy_policy": {
                "scope": "local_profile_project",
                "upload_policy": "only_retrieved_chunks_sent_to_selected_provider",
                "no_command_execution": True,
                "no_file_modification": True,
                "embedding_enabled": bool(self.settings.embedding_enabled),
            },
        }

    def _provider_snapshot(self) -> Dict[str, Any]:
        return {
            "provider_type": self.settings.provider_type,
            "base_url": self.settings.base_url,
            "model": self.settings.model,
            "has_api_key": bool(self.settings.api_key),
        }
