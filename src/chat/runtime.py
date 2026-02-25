"""Chat runtime built on local LangGraph/LangChain execution."""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from threading import RLock
from typing import Any, TypedDict

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, StateGraph

from chat.memory_store import ChatMemoryStore
from chat.state_store import ChatStateStore
from chat.tool_registry import ChatToolRegistry, ToolDescriptor
from infrastructure.runtime_errors import ChatRuntimeError

_JIRA_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]+-[A-Z]*\d+$")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class _ChatGraphState(TypedDict, total=False):
    content: str
    context: str
    needs_approval: bool
    response: str
    pending_tool: dict[str, Any]


class GraphChatEngine:
    """Local chat decision/execution graph."""

    def __init__(self, llm_generate: Callable[[str], str] | None) -> None:
        self._llm_generate = llm_generate
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", "You are a concise assistant for test automation workflows."),
                (
                    "human",
                    "Project memory: {context}\n"
                    "User request: {content}\n"
                    "Answer in a short actionable format.",
                ),
            ]
        )
        self._chain = prompt | RunnableLambda(self._run_llm)
        self._graph = self._build_graph()

    def _run_llm(self, value: Any) -> str:
        messages = value.to_messages()
        prompt = "\n".join(str(message.content) for message in messages)
        if self._llm_generate:
            reply = self._llm_generate(prompt)
            return str(reply).strip() or "Готово."
        if messages:
            return f"echo: {messages[-1].content}"
        return "Готово."

    def _build_graph(self):
        graph = StateGraph(_ChatGraphState)
        graph.add_node("classify", self._classify_node)
        graph.add_node("request_approval", self._request_approval_node)
        graph.add_node("generate", self._generate_node)
        graph.set_entry_point("classify")
        graph.add_conditional_edges(
            "classify",
            self._route_after_classify,
            {"request_approval": "request_approval", "generate": "generate"},
        )
        graph.add_edge("request_approval", END)
        graph.add_edge("generate", END)
        return graph.compile()

    @staticmethod
    def _classify_node(state: _ChatGraphState) -> dict[str, Any]:
        content = str(state.get("content", "")).lower()
        # Keep write-like requests guarded by explicit confirmation.
        needs_approval = any(token in content for token in ("write", "apply", "delete", "rewrite"))
        return {"needs_approval": needs_approval}

    @staticmethod
    def _route_after_classify(state: _ChatGraphState) -> str:
        return "request_approval" if state.get("needs_approval") else "generate"

    @staticmethod
    def _request_approval_node(state: _ChatGraphState) -> dict[str, Any]:
        content = str(state.get("content", "")).strip()
        return {
            "response": "Для этого действия нужно подтверждение перед выполнением инструмента записи.",
            "pending_tool": {
                "toolName": "compose_feature_patch",
                "title": "Подтвердить изменение feature-файла",
                "kind": "tool",
                "args": {"request": content},
                "risk": "high",
            },
        }

    def _generate_node(self, state: _ChatGraphState) -> dict[str, Any]:
        response = self._chain.invoke(
            {
                "context": str(state.get("context", "")),
                "content": str(state.get("content", "")),
            }
        )
        return {"response": str(response)}

    def invoke(self, *, content: str, context: str) -> dict[str, Any]:
        return self._graph.invoke({"content": content, "context": context})


class _AutotestIntent(TypedDict):
    enabled: bool
    target_path: str | None
    overwrite_existing: bool
    language: str | None


class ChatAgentRuntime:
    def __init__(
        self,
        *,
        memory_store: ChatMemoryStore,
        llm_client: Any | None = None,
        context_window: int = 200_000,
        orchestrator: Any | None = None,
        run_state_store: Any | None = None,
        execution_supervisor: Any | None = None,
        tool_host_client: Any | None = None,
    ) -> None:
        self.state_store = ChatStateStore(memory_store)
        self.memory_store = memory_store
        llm_generate = getattr(llm_client, "generate", None)
        self._engine = GraphChatEngine(llm_generate if callable(llm_generate) else None)
        self._context_window = context_window
        self._orchestrator = orchestrator
        self._run_state_store = run_state_store
        self._execution_supervisor = execution_supervisor
        self._tool_host_client = tool_host_client
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = RLock()
        self._tool_registry = ChatToolRegistry()
        self._register_tools()

    def _register_tools(self) -> None:
        self._tool_registry.register(
            ToolDescriptor(
                name="compose_feature_patch",
                description="Compose a safe placeholder patch response for feature updates.",
                handler=self._tool_compose_feature_patch,
                risk_level="write",
                requires_confirmation=True,
            )
        )
        self._tool_registry.register(
            ToolDescriptor(
                name="save_generated_feature",
                description="Save generated feature text to target path.",
                handler=self._tool_save_generated_feature,
                risk_level="write",
                requires_confirmation=True,
            )
        )

    @staticmethod
    def _tool_compose_feature_patch(*, request: str) -> dict[str, Any]:
        file_path = "features/generated.feature"
        return {
            "message": "Подготовлен черновик обновления feature-файла.",
            "diff": {
                "summary": {"files": 1, "additions": 8, "deletions": 0},
                "files": [
                    {
                        "file": file_path,
                        "before": "",
                        "after": f"Feature: Generated\n  Scenario: Draft\n    Given {request}",
                        "additions": 8,
                        "deletions": 0,
                    }
                ],
            },
        }

    def _tool_save_generated_feature(
        self,
        *,
        project_root: str,
        target_path: str,
        feature_text: str,
        overwrite_existing: bool = False,
    ) -> dict[str, Any]:
        if self._tool_host_client is not None:
            try:
                result = self._tool_host_client.save_generated_feature(
                    project_root=project_root,
                    target_path=target_path,
                    feature_text=feature_text,
                    overwrite_existing=bool(overwrite_existing),
                )
            except Exception as exc:
                raise ChatRuntimeError(f"Tool host request failed: {exc}", status_code=503) from exc
        elif self._orchestrator is None:
            raise ChatRuntimeError("Сохранение feature недоступно: orchestrator не настроен", status_code=503)
        else:
            result = self._orchestrator.apply_feature(
                project_root,
                target_path,
                feature_text,
                overwrite_existing=bool(overwrite_existing),
            )
        status = str(result.get("status", "created"))
        localized_status = {
            "created": "создан",
            "overwritten": "перезаписан",
            "skipped": "пропущен",
        }.get(status, status)
        message = (
            f"Feature-файл {localized_status}: {result.get('targetPath', target_path)}"
            if not result.get("message")
            else str(result.get("message"))
        )
        return {
            "message": message,
            "diff": {
                "summary": {"files": 1, "additions": 1, "deletions": 0},
                "files": [
                    {
                        "file": str(result.get("targetPath", target_path)),
                        "before": "",
                        "after": feature_text,
                        "additions": max(1, len(feature_text.splitlines())),
                        "deletions": 0,
                    }
                ],
            },
        }

    @staticmethod
    def _extract_target_path(content: str) -> str | None:
        # Expected fragments like "targetPath=path/to/file.feature" or "path: path/to/file.feature"
        patterns = [
            r"targetpath\s*[=:]\s*([^\s,;]+)",
            r"path\s*[=:]\s*([^\s,;]+\.feature)",
            r"([^\s,;]+\.feature)",
        ]
        for pattern in patterns:
            match = re.search(pattern, content, flags=re.IGNORECASE)
            if not match:
                continue
            raw_value = match.group(1).strip()
            if not raw_value:
                continue
            return raw_value
        return None

    @staticmethod
    def _extract_language(content: str) -> str | None:
        lowered = content.lower()
        if "language=en" in lowered or "gherkin en" in lowered or "на англий" in lowered:
            return "en"
        if "language=ru" in lowered or "gherkin ru" in lowered or "на русском" in lowered:
            return "ru"
        return None

    def _detect_autotest_intent(self, content: str) -> _AutotestIntent:
        lowered = content.lower()
        keywords = (
            "автотест",
            "test case",
            "тесткейс",
            "feature",
            "gherkin",
            "сгенерируй тест",
            "generate test",
            "generate feature",
        )
        enabled = any(token in lowered for token in keywords)
        return {
            "enabled": enabled,
            "target_path": self._extract_target_path(content),
            "overwrite_existing": "overwrite=true" in lowered or "перезапис" in lowered,
            "language": self._extract_language(content),
        }

    @staticmethod
    def _default_target_path(feature_payload: dict[str, Any] | None = None) -> str:
        fallback = "src/test/resources/features/generated.feature"
        if not isinstance(feature_payload, dict):
            return fallback
        pipeline = feature_payload.get("pipeline")
        if not isinstance(pipeline, list):
            return fallback
        for item in pipeline:
            if not isinstance(item, dict):
                continue
            details = item.get("details")
            if not isinstance(details, dict):
                continue
            jira_key = str(details.get("jiraKey", "")).strip().upper()
            if jira_key and _JIRA_KEY_RE.fullmatch(jira_key):
                return f"src/test/resources/features/{jira_key}.feature"
        return fallback

    async def _run_autotest_job(
        self,
        *,
        session_id: str,
        run_id: str,
        project_root: str,
        content: str,
        intent: _AutotestIntent,
        session: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str | None]:
        if not self._run_state_store or not self._execution_supervisor:
            raise ChatRuntimeError("Генерация автотеста недоступна: job control plane не настроен", status_code=503)

        job_id = str(uuid.uuid4())
        self.state_store.append_event(
            session_id,
            "autotest.intent_detected",
            {"sessionId": session_id, "runId": run_id},
        )
        self.state_store.update_session(
            session_id,
            activity="busy",
            current_action="Подготовка задачи генерации автотеста",
        )

        self._run_state_store.put_job(
            {
                "job_id": job_id,
                "status": "queued",
                "cancel_requested": False,
                "cancel_requested_at": None,
                "project_root": project_root,
                "test_case_text": content,
                "target_path": intent.get("target_path"),
                "create_file": False,
                "overwrite_existing": False,
                "language": intent.get("language"),
                "quality_policy": "strict",
                "zephyr_auth": session.get("zephyr_auth"),
                "jira_instance": session.get("jira_instance"),
                "profile": "quick",
                "source": "chat-runtime",
                "started_at": _utcnow(),
                "updated_at": _utcnow(),
                "attempts": [],
                "result": None,
            }
        )
        self._run_state_store.append_event(
            job_id,
            "job.queued",
            {"jobId": job_id, "source": "chat-runtime"},
        )
        self.state_store.append_event(
            session_id,
            "autotest.job_created",
            {"sessionId": session_id, "runId": run_id, "jobId": job_id},
        )
        self.state_store.update_session(
            session_id,
            activity="busy",
            current_action=f"Выполнение задачи автотеста {job_id[:8]}",
        )

        await self._execution_supervisor.execute_job(job_id)
        job = self._run_state_store.get_job(job_id) or {}
        status = str(job.get("status", "failed"))
        self.state_store.append_event(
            session_id,
            "autotest.job_progress",
            {"sessionId": session_id, "runId": run_id, "jobId": job_id, "status": status},
        )
        feature_payload = job.get("result")
        incident_uri = job.get("incident_uri")
        return feature_payload, incident_uri

    @staticmethod
    def _format_autotest_preview(feature_payload: dict[str, Any]) -> str:
        feature_text = str(feature_payload.get("featureText", "")).strip()
        steps_summary = feature_payload.get("stepsSummary") or {}
        quality = feature_payload.get("quality") or {}
        exact = int(steps_summary.get("exact", 0))
        fuzzy = int(steps_summary.get("fuzzy", 0))
        unmatched = int(steps_summary.get("unmatched", 0))
        quality_score = quality.get("score")
        quality_passed = quality.get("passed")
        quality_line = ""
        if quality_score is not None and quality_passed is not None:
            quality_line = f"\nQuality: score={quality_score}, gate={'pass' if quality_passed else 'fail'}."
        pipeline = feature_payload.get("pipeline") or []
        pipeline_summary = ", ".join(str(step.get("stage", "?")) for step in pipeline)
        preview = feature_text[:1800] if feature_text else "<empty>"
        return (
            "Autotest preview is ready.\n"
            f"Step summary: exact={exact}, fuzzy={fuzzy}, unmatched={unmatched}.\n"
            f"Pipeline: {pipeline_summary or 'n/a'}.{quality_line}\n\n"
            f"{preview}"
        )

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        with self._locks_guard:
            lock = self._locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[session_id] = lock
            return lock

    @staticmethod
    def _token_estimate(text: str) -> int:
        return max(1, len(text) // 4)

    def _require_session(self, session_id: str) -> dict[str, Any]:
        session = self.state_store.get_session(session_id)
        if not session:
            raise ChatRuntimeError(f"Сессия не найдена: {session_id}", status_code=404)
        return session

    def _build_context(self, session: dict[str, Any]) -> str:
        memory = session.get("memory_snapshot", {})
        goals = memory.get("goals", [])
        summary = memory.get("summary")
        return f"goals={goals}; summary={summary}"

    def _to_history_payload(self, session: dict[str, Any], *, limit: int) -> dict[str, Any]:
        history = self.state_store.history(session["session_id"], limit=limit)
        if not history:
            raise ChatRuntimeError(f"Сессия не найдена: {session['session_id']}", status_code=404)
        return {
            "sessionId": history["session_id"],
            "projectRoot": history["project_root"],
            "source": history.get("source", "ide-plugin"),
            "profile": history.get("profile", "quick"),
            "status": history.get("status", "active"),
            "messages": [
                {
                    "messageId": item["message_id"],
                    "role": item["role"],
                    "content": item["content"],
                    "runId": item.get("run_id"),
                    "metadata": item.get("metadata", {}),
                    "createdAt": item["created_at"],
                }
                for item in history.get("messages", [])
            ],
            "events": [
                {
                    "eventType": event["event_type"],
                    "payload": event["payload"],
                    "createdAt": event["created_at"],
                    "index": event["index"],
                }
                for event in history.get("events", [])
            ],
            "pendingPermissions": [
                {
                    "permissionId": item["tool_call_id"],
                    "title": item.get("title", item["tool_name"]),
                    "kind": item.get("kind", "tool"),
                    "callId": item["tool_call_id"],
                    "messageId": item.get("message_id"),
                    "metadata": {"risk": item.get("risk_level", "read"), **item.get("args", {})},
                    "createdAt": item["created_at"],
                }
                for item in history.get("pending_tool_calls", [])
            ],
            "memorySnapshot": history.get("memory_snapshot", {}),
            "updatedAt": history["updated_at"],
        }

    async def create_session(
        self,
        *,
        project_root: str,
        source: str,
        profile: str,
        reuse_existing: bool,
        zephyr_auth: dict[str, Any] | None = None,
        jira_instance: str | None = None,
    ) -> dict[str, Any]:
        payload, reused = self.state_store.create_session(
            project_root=project_root,
            source=source,
            profile=profile,
            reuse_existing=reuse_existing,
        )
        defaults = {
            "activity": "idle",
            "current_action": "Ожидание",
            "totals": {"tokens": {"input": 0, "output": 0, "reasoning": 0, "cacheRead": 0, "cacheWrite": 0}, "cost": 0.0},
            "limits": {"contextWindow": self._context_window, "used": 0, "percent": 0.0},
            "diff": {"summary": {"files": 0, "additions": 0, "deletions": 0}, "files": []},
            "always_approved_tools": [],
            "last_retry_message": None,
            "last_retry_attempt": None,
            "last_retry_at": None,
            "zephyr_auth": zephyr_auth,
            "jira_instance": jira_instance,
        }
        self.state_store.update_session(payload["session_id"], **defaults)
        session = self._require_session(payload["session_id"])
        return {
            "sessionId": session["session_id"],
            "createdAt": session["created_at"],
            "reused": reused,
            "projectRoot": session["project_root"],
            "source": session["source"],
            "profile": session["profile"],
            "memorySnapshot": session.get("memory_snapshot", {}),
        }

    async def has_session(self, session_id: str) -> bool:
        return self.state_store.get_session(session_id) is not None

    async def process_message(
        self,
        *,
        session_id: str,
        run_id: str,
        message_id: str,
        content: str,
    ) -> None:
        lock = self._session_lock(session_id)
        async with lock:
            session = self._require_session(session_id)
            intent = self._detect_autotest_intent(content)
            self.state_store.update_session(
                session_id,
                activity="busy",
                current_action="Обработка запроса",
            )
            self.state_store.append_message(
                session_id,
                role="user",
                content=content,
                run_id=run_id,
                message_id=message_id or str(uuid.uuid4()),
            )
            self.state_store.append_event(
                session_id,
                "message.received",
                {"sessionId": session_id, "runId": run_id},
            )

            pending_tool: dict[str, Any] | None = None
            assistant_text = ""
            output_text_for_tokens = ""
            can_run_autotest = (
                bool(intent.get("enabled"))
                and self._run_state_store is not None
                and self._execution_supervisor is not None
            )
            if can_run_autotest:
                try:
                    feature_payload, incident_uri = await self._run_autotest_job(
                        session_id=session_id,
                        run_id=run_id,
                        project_root=str(session.get("project_root", "")),
                        content=content,
                        intent=intent,
                        session=session,
                    )
                except ChatRuntimeError:
                    raise
                except Exception as exc:
                    raise ChatRuntimeError(f"Генерация автотеста завершилась ошибкой: {exc}", status_code=503) from exc

                if feature_payload:
                    assistant_text = self._format_autotest_preview(feature_payload)
                    target_path = intent.get("target_path") or self._default_target_path(feature_payload)
                    pending_tool = {
                        "toolName": "save_generated_feature",
                        "title": "Сохранить сгенерированный feature-файл",
                        "kind": "tool",
                        "args": {
                            "project_root": str(session.get("project_root", "")),
                            "target_path": target_path,
                            "feature_text": str(feature_payload.get("featureText", "")),
                            "overwrite_existing": bool(intent.get("overwrite_existing", False)),
                        },
                        "risk": "high",
                    }
                    assistant_text += (
                        "\n\nСохранение ожидает подтверждения."
                        f" Целевой путь: {target_path}"
                    )
                    self.state_store.append_event(
                        session_id,
                        "autotest.result_ready",
                        {"sessionId": session_id, "runId": run_id},
                    )
                else:
                    incident_suffix = f" Инцидент: {incident_uri}" if incident_uri else ""
                    assistant_text = f"Задача автотеста завершилась без feature-результата.{incident_suffix}"
            else:
                result = self._engine.invoke(content=content, context=self._build_context(session))
                pending_tool = result.get("pending_tool")
                assistant_text = str(result.get("response", "")).strip() or "Готово."
                output_text_for_tokens = assistant_text

            if not output_text_for_tokens:
                output_text_for_tokens = assistant_text

            if pending_tool:
                tool_call_id = str(uuid.uuid4())
                self.state_store.set_pending_tool_call(
                    session_id,
                    tool_call_id=tool_call_id,
                    tool_name=str(pending_tool.get("toolName", "tool")),
                    args=dict(pending_tool.get("args", {})),
                    risk_level=str(pending_tool.get("risk", "high")),
                    requires_confirmation=True,
                    title=str(pending_tool.get("title", "Подтвердите выполнение инструмента")),
                    kind=str(pending_tool.get("kind", "tool")),
                    message_id=message_id,
                )
                self.state_store.append_message(
                    session_id,
                    role="assistant",
                    content=assistant_text,
                    run_id=run_id,
                    metadata={"pendingPermissionId": tool_call_id},
                )
                self.state_store.append_event(
                    session_id,
                    "permission.requested",
                    {"sessionId": session_id, "permissionId": tool_call_id},
                )
                self.state_store.update_session(
                    session_id,
                    activity="waiting_permission",
                    current_action="Ожидание подтверждения",
                )
            else:
                self.state_store.append_message(
                    session_id,
                    role="assistant",
                    content=assistant_text,
                    run_id=run_id,
                    message_id=f"assistant-{message_id or uuid.uuid4()}",
                )
                self.state_store.append_event(
                    session_id,
                    "message.final",
                    {"sessionId": session_id, "runId": run_id},
                )
                self.state_store.update_session(
                    session_id,
                    activity="idle",
                    current_action="Ожидание",
                )

            updated = self._require_session(session_id)
            totals = dict(updated.get("totals", {}))
            token_totals = dict(totals.get("tokens", {}))
            token_totals["input"] = int(token_totals.get("input", 0)) + self._token_estimate(content)
            if not pending_tool:
                token_totals["output"] = int(token_totals.get("output", 0)) + self._token_estimate(output_text_for_tokens)
            totals["tokens"] = token_totals
            limits = dict(updated.get("limits", {}))
            used = int(limits.get("used", 0)) + self._token_estimate(content)
            limits["used"] = used
            context_window = int(limits.get("contextWindow", self._context_window))
            limits["percent"] = round(used / context_window, 4) if context_window else None
            self.state_store.update_session(session_id, totals=totals, limits=limits)

    async def process_tool_decision(
        self,
        *,
        session_id: str,
        run_id: str,
        permission_id: str,
        decision: str,
    ) -> None:
        _ = run_id
        lock = self._session_lock(session_id)
        async with lock:
            session = self._require_session(session_id)
            pending = self.state_store.get_pending_tool_call(session_id, permission_id)
            if not pending:
                raise ChatRuntimeError(
                    f"Permission not found: {permission_id}",
                    status_code=404,
                )

            if decision == "reject":
                self.state_store.pop_pending_tool_call(session_id, permission_id)
                self.state_store.append_message(
                    session_id,
                    role="assistant",
                    content="Выполнение инструмента отклонено.",
                    metadata={"permissionId": permission_id, "decision": decision},
                )
                self.state_store.append_event(
                    session_id,
                    "permission.rejected",
                    {"sessionId": session_id, "permissionId": permission_id},
                )
                self.state_store.update_session(session_id, activity="idle", current_action="Ожидание")
                return

            if decision not in {"approve_once", "approve_always"}:
                raise ChatRuntimeError(f"Неподдерживаемое решение: {decision}", status_code=422)

            if decision == "approve_always":
                always = list(session.get("always_approved_tools", []))
                tool_name = str(pending.get("tool_name", ""))
                if tool_name and tool_name not in always:
                    always.append(tool_name)
                self.state_store.update_session(session_id, always_approved_tools=always)

            tool_name = str(pending.get("tool_name", ""))
            try:
                descriptor = self._tool_registry.get(tool_name)
            except KeyError as exc:
                raise ChatRuntimeError(f"Инструмент не зарегистрирован: {tool_name}", status_code=422) from exc
            result = descriptor.handler(**pending.get("args", {}))
            self.state_store.pop_pending_tool_call(session_id, permission_id)
            self.state_store.append_event(
                session_id,
                "permission.approved",
                {"sessionId": session_id, "permissionId": permission_id, "decision": decision},
            )
            self.state_store.append_message(
                session_id,
                role="assistant",
                content=str(result.get("message", "Инструмент выполнен.")),
                metadata={"permissionId": permission_id, "tool": tool_name},
            )
            if isinstance(result.get("diff"), dict):
                self.state_store.update_session(session_id, diff=result["diff"])
            if tool_name == "save_generated_feature":
                self.state_store.append_event(
                    session_id,
                    "autotest.saved",
                    {"sessionId": session_id, "permissionId": permission_id},
                )
            self.state_store.update_session(session_id, activity="idle", current_action="Ожидание")

    async def get_history(self, *, session_id: str, limit: int = 200) -> dict[str, Any]:
        session = self._require_session(session_id)
        return self._to_history_payload(session, limit=limit)

    async def list_sessions(self, *, project_root: str, limit: int = 50) -> dict[str, Any]:
        rows = self.state_store.list_sessions(project_root, limit=limit)
        items = []
        for row in rows:
            messages = row.get("messages", [])
            last_preview = None
            for message in reversed(messages):
                if message.get("role") == "assistant":
                    last_preview = str(message.get("content", ""))[:160]
                    break
            items.append(
                {
                    "sessionId": row["session_id"],
                    "projectRoot": row["project_root"],
                    "source": row.get("source", "ide-plugin"),
                    "profile": row.get("profile", "quick"),
                    "status": row.get("status", "active"),
                    "activity": row.get("activity", "idle"),
                    "currentAction": row.get("current_action", "Ожидание"),
                    "createdAt": row["created_at"],
                    "updatedAt": row["updated_at"],
                    "lastMessagePreview": last_preview,
                    "pendingPermissionsCount": len(row.get("pending_tool_calls", [])),
                }
            )
        return {"items": items, "total": len(items)}

    async def get_status(self, *, session_id: str) -> dict[str, Any]:
        session = self._require_session(session_id)
        events = session.get("events", [])
        last_event = events[-1]["created_at"] if events else session.get("updated_at", _utcnow())
        return {
            "sessionId": session["session_id"],
            "activity": session.get("activity", "idle"),
            "currentAction": session.get("current_action", "Ожидание"),
            "lastEventAt": last_event,
            "updatedAt": session.get("updated_at", _utcnow()),
            "pendingPermissionsCount": len(session.get("pending_tool_calls", [])),
            "totals": session.get(
                "totals",
                {"tokens": {"input": 0, "output": 0, "reasoning": 0, "cacheRead": 0, "cacheWrite": 0}, "cost": 0.0},
            ),
            "limits": session.get(
                "limits",
                {"contextWindow": self._context_window, "used": 0, "percent": 0.0},
            ),
            "lastRetryMessage": session.get("last_retry_message"),
            "lastRetryAttempt": session.get("last_retry_attempt"),
            "lastRetryAt": session.get("last_retry_at"),
        }

    async def get_diff(self, *, session_id: str) -> dict[str, Any]:
        session = self._require_session(session_id)
        diff = session.get("diff") or {"summary": {"files": 0, "additions": 0, "deletions": 0}, "files": []}
        return {
            "sessionId": session["session_id"],
            "summary": diff.get("summary", {"files": 0, "additions": 0, "deletions": 0}),
            "files": diff.get("files", []),
            "updatedAt": session.get("updated_at", _utcnow()),
        }

    async def execute_command(self, *, session_id: str, command: str) -> dict[str, Any]:
        session = self._require_session(session_id)
        if command == "abort":
            self.state_store.update_session(session_id, activity="idle", current_action="Прервано")
            result: dict[str, Any] = {"ok": True, "message": "Текущее действие прервано"}
        elif command == "compact":
            history = self.state_store.history(session_id, limit=80)
            messages = history.get("messages", []) if history else []
            self.state_store.update_session(session_id, messages=messages)
            result = {"ok": True, "message": "История сжата"}
        elif command == "status":
            result = {"ok": True, "status": await self.get_status(session_id=session_id)}
        elif command == "diff":
            result = {"ok": True, "diff": await self.get_diff(session_id=session_id)}
        elif command == "help":
            result = {"ok": True, "commands": ["status", "diff", "compact", "abort", "help"]}
        else:
            raise ChatRuntimeError(f"Неподдерживаемая команда: {command}", status_code=422)

        self.state_store.append_event(
            session_id,
            "command.executed",
            {"sessionId": session_id, "command": command},
        )
        updated = self._require_session(session_id)
        return {
            "sessionId": session_id,
            "command": command,
            "accepted": True,
            "result": result,
            "updatedAt": updated.get("updated_at", _utcnow()),
        }

    async def stream_events(
        self,
        *,
        session_id: str,
        from_index: int = 0,
    ) -> AsyncIterator[bytes]:
        _ = self._require_session(session_id)
        index = max(0, from_index)
        loop = asyncio.get_running_loop()
        heartbeat_interval_s = 2.0
        last_emit_ts = loop.time()
        while True:
            events, next_index = self.state_store.list_events(session_id, since_index=index)
            if events:
                for event in events:
                    payload = {
                        "eventType": event["event_type"],
                        "payload": event["payload"],
                        "createdAt": event["created_at"],
                        "index": event["index"],
                    }
                    chunk = (
                        f"event: {event['event_type']}\n"
                        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    )
                    yield chunk.encode("utf-8")
                last_emit_ts = loop.time()
            else:
                now = loop.time()
                if now - last_emit_ts >= heartbeat_interval_s:
                    payload = {
                        "eventType": "heartbeat",
                        "payload": {"sessionId": session_id},
                        "createdAt": _utcnow(),
                        "index": next_index,
                    }
                    chunk = f"event: heartbeat\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    yield chunk.encode("utf-8")
                    last_emit_ts = now
            index = next_index
            await asyncio.sleep(0.15)

