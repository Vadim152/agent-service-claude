from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RuleCondition:
    jira_key_pattern: str | None = None
    language_in: list[str] = field(default_factory=list)
    quality_policy_in: list[str] = field(default_factory=list)
    text_regex: str | None = None


@dataclass(slots=True)
class RuleActions:
    quality_policy: str | None = None
    language: str | None = None
    target_path_template: str | None = None
    apply_templates: list[str] = field(default_factory=list)


@dataclass(slots=True)
class GenerationRule:
    id: str
    name: str
    enabled: bool = True
    priority: int = 100
    condition: RuleCondition = field(default_factory=RuleCondition)
    actions: RuleActions = field(default_factory=RuleActions)
    source: str = "api"
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(slots=True)
class StepTemplate:
    id: str
    name: str
    enabled: bool = True
    priority: int = 100
    trigger_regex: str | None = None
    steps: list[str] = field(default_factory=list)
    source: str = "api"
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(slots=True)
class GenerationContext:
    project_root: str
    text: str
    jira_key: str | None = None
    language: str | None = None
    quality_policy: str | None = None


@dataclass(slots=True)
class ResolvedGenerationPreferences:
    quality_policy: str | None = None
    language: str | None = None
    target_path: str | None = None
    applied_rule_ids: list[str] = field(default_factory=list)
    applied_template_ids: list[str] = field(default_factory=list)
    template_steps: list[str] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)
