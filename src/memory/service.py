from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from memory.repository import MemoryRepository, _parse_iso8601

_ALLOWED_QUALITY = {"strict", "balanced", "lenient"}
_ALLOWED_LANG = {"ru", "en"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryService:
    def __init__(self, repository: MemoryRepository) -> None:
        self._repository = repository

    def load_project_memory(self, project_root: str) -> dict[str, Any]:
        return self._repository.load(project_root)

    def get_step_boosts(self, project_root: str) -> dict[str, float]:
        payload = self._repository.load(project_root)
        boosts = payload.get("stepBoosts", {})
        if not isinstance(boosts, dict):
            return {}
        feedback = payload.get("feedback", [])
        feedback_timestamps: dict[str, datetime] = {}
        if isinstance(feedback, list):
            for entry in feedback:
                if not isinstance(entry, dict):
                    continue
                step_id = str(entry.get("stepId", "")).strip()
                if not step_id:
                    continue
                created_at = _parse_iso8601(entry.get("createdAt"))
                if created_at is None:
                    continue
                previous = feedback_timestamps.get(step_id)
                if previous is None or created_at > previous:
                    feedback_timestamps[step_id] = created_at

        result: dict[str, float] = {}
        now = datetime.now(timezone.utc)
        half_life_days = 60.0
        for key, value in boosts.items():
            try:
                step_id = str(key)
                raw_boost = float(value)
            except (TypeError, ValueError):
                continue
            last_feedback_at = feedback_timestamps.get(step_id)
            if last_feedback_at is None:
                result[step_id] = raw_boost
                continue
            age_days = max(0.0, (now - last_feedback_at).total_seconds() / 86400.0)
            decay_factor = 0.5 ** (age_days / half_life_days)
            result[step_id] = round(raw_boost * decay_factor, 4)
        return result

    def record_feedback(
        self,
        *,
        project_root: str,
        step_id: str,
        accepted: bool,
        note: str | None = None,
        preference_key: str | None = None,
        preference_value: Any = None,
        scoring_version: str = "v2",
    ) -> dict[str, Any]:
        payload = self._repository.load(project_root)
        boosts = payload.setdefault("stepBoosts", {})
        current = float(boosts.get(step_id, 0.0))
        delta = 0.05 if accepted else -0.05
        boosts[step_id] = round(max(-0.5, min(0.5, current + delta)), 4)

        feedback = payload.setdefault("feedback", [])
        feedback.append(
            {
                "stepId": step_id,
                "accepted": accepted,
                "delta": delta,
                "note": note,
                "scoringVersion": scoring_version,
                "createdAt": _utcnow(),
            }
        )
        if len(feedback) > 300:
            payload["feedback"] = feedback[-300:]

        if preference_key:
            prefs = payload.setdefault("preferences", {})
            prefs[preference_key] = preference_value

        return self._repository.save(project_root, payload)

    def list_generation_rules(self, project_root: str) -> list[dict[str, Any]]:
        payload = self._repository.load(project_root)
        rules = payload.get("generationRules", [])
        if not isinstance(rules, list):
            return []
        return sorted([item for item in rules if isinstance(item, dict)], key=lambda r: int(r.get("priority", 100)))

    def add_generation_rule(self, project_root: str, data: dict[str, Any]) -> dict[str, Any]:
        payload = self._repository.load(project_root)
        rules = [item for item in payload.get("generationRules", []) if isinstance(item, dict)]
        now = _utcnow()
        rule = self._validate_rule(data, default_id=str(uuid.uuid4()), now=now)
        rules.append(rule)
        payload["generationRules"] = rules
        self._repository.save(project_root, payload)
        return rule

    def update_generation_rule(self, project_root: str, rule_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        payload = self._repository.load(project_root)
        rules = [item for item in payload.get("generationRules", []) if isinstance(item, dict)]
        for idx, rule in enumerate(rules):
            if str(rule.get("id")) != str(rule_id):
                continue
            merged = dict(rule)
            for key, value in patch.items():
                if key in {"condition", "actions"} and isinstance(value, dict):
                    merged[key] = {**(merged.get(key) or {}), **value}
                elif value is not None:
                    merged[key] = value
            validated = self._validate_rule(merged, default_id=str(rule_id), now=_utcnow(), created_at=rule.get("createdAt"))
            rules[idx] = validated
            payload["generationRules"] = rules
            self._repository.save(project_root, payload)
            return validated
        return None

    def delete_generation_rule(self, project_root: str, rule_id: str) -> bool:
        payload = self._repository.load(project_root)
        rules = [item for item in payload.get("generationRules", []) if isinstance(item, dict)]
        next_rules = [item for item in rules if str(item.get("id")) != str(rule_id)]
        if len(next_rules) == len(rules):
            return False
        payload["generationRules"] = next_rules
        self._repository.save(project_root, payload)
        return True

    def list_step_templates(self, project_root: str) -> list[dict[str, Any]]:
        payload = self._repository.load(project_root)
        templates = payload.get("stepTemplates", [])
        if not isinstance(templates, list):
            return []
        return sorted([item for item in templates if isinstance(item, dict)], key=lambda r: int(r.get("priority", 100)))

    def add_step_template(self, project_root: str, data: dict[str, Any]) -> dict[str, Any]:
        payload = self._repository.load(project_root)
        templates = [item for item in payload.get("stepTemplates", []) if isinstance(item, dict)]
        now = _utcnow()
        template = self._validate_template(data, default_id=str(uuid.uuid4()), now=now)
        templates.append(template)
        payload["stepTemplates"] = templates
        self._repository.save(project_root, payload)
        return template

    def update_step_template(self, project_root: str, template_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        payload = self._repository.load(project_root)
        templates = [item for item in payload.get("stepTemplates", []) if isinstance(item, dict)]
        for idx, template in enumerate(templates):
            if str(template.get("id")) != str(template_id):
                continue
            merged = dict(template)
            merged.update({key: value for key, value in patch.items() if value is not None})
            validated = self._validate_template(
                merged,
                default_id=str(template_id),
                now=_utcnow(),
                created_at=template.get("createdAt"),
            )
            templates[idx] = validated
            payload["stepTemplates"] = templates
            self._repository.save(project_root, payload)
            return validated
        return None

    def delete_step_template(self, project_root: str, template_id: str) -> bool:
        payload = self._repository.load(project_root)
        templates = [item for item in payload.get("stepTemplates", []) if isinstance(item, dict)]
        next_templates = [item for item in templates if str(item.get("id")) != str(template_id)]
        if len(next_templates) == len(templates):
            return False
        payload["stepTemplates"] = next_templates
        self._repository.save(project_root, payload)
        return True

    def resolve_generation_preferences(
        self,
        *,
        project_root: str,
        text: str,
        jira_key: str | None,
        language: str | None,
        quality_policy: str | None,
    ) -> dict[str, Any]:
        rules = self.list_generation_rules(project_root)
        templates = self.list_step_templates(project_root)
        context = {
            "text": text,
            "jiraKey": jira_key,
            "language": language,
            "qualityPolicy": quality_policy,
        }

        resolved = {
            "qualityPolicy": quality_policy,
            "language": language,
            "targetPath": None,
            "appliedRuleIds": [],
            "appliedTemplateIds": [],
            "templateSteps": [],
        }

        selected_template_ids: list[str] = []
        for rule in rules:
            if not bool(rule.get("enabled", True)):
                continue
            if not self._rule_matches(rule, context):
                continue
            resolved["appliedRuleIds"].append(str(rule.get("id")))
            actions = rule.get("actions", {}) if isinstance(rule.get("actions"), dict) else {}
            if actions.get("qualityPolicy") in _ALLOWED_QUALITY:
                resolved["qualityPolicy"] = actions.get("qualityPolicy")
            if actions.get("language") in _ALLOWED_LANG:
                resolved["language"] = actions.get("language")
            target_template = str(actions.get("targetPathTemplate") or "").strip()
            if target_template:
                resolved["targetPath"] = target_template.replace("{jiraKey}", str(jira_key or "generated"))
            apply_templates = actions.get("applyTemplates")
            if isinstance(apply_templates, list):
                selected_template_ids.extend(str(item) for item in apply_templates if item)

        active_templates: list[dict[str, Any]] = []
        for template in templates:
            if not bool(template.get("enabled", True)):
                continue
            template_id = str(template.get("id", ""))
            trigger_regex = str(template.get("triggerRegex") or "").strip()
            by_action = template_id in selected_template_ids
            by_regex = bool(trigger_regex and re.search(trigger_regex, text, flags=re.IGNORECASE))
            if by_action or by_regex:
                active_templates.append(template)

        active_templates.sort(key=lambda item: int(item.get("priority", 100)))
        seen_steps: set[str] = set()
        for template in active_templates:
            template_id = str(template.get("id"))
            if template_id and template_id not in resolved["appliedTemplateIds"]:
                resolved["appliedTemplateIds"].append(template_id)
            for raw_step in template.get("steps", []):
                step = str(raw_step).strip()
                if not step:
                    continue
                key = step.casefold()
                if key in seen_steps:
                    continue
                seen_steps.add(key)
                resolved["templateSteps"].append(step)

        return resolved

    @staticmethod
    def _rule_matches(rule: dict[str, Any], context: dict[str, Any]) -> bool:
        cond = rule.get("condition", {}) if isinstance(rule.get("condition"), dict) else {}

        jira_pattern = str(cond.get("jiraKeyPattern") or "").strip()
        if jira_pattern:
            jira_key = str(context.get("jiraKey") or "")
            if not jira_key or re.search(jira_pattern, jira_key, flags=re.IGNORECASE) is None:
                return False

        text_regex = str(cond.get("textRegex") or "").strip()
        if text_regex:
            if re.search(text_regex, str(context.get("text") or ""), flags=re.IGNORECASE) is None:
                return False

        lang_in = cond.get("languageIn") if isinstance(cond.get("languageIn"), list) else []
        if lang_in:
            if str(context.get("language") or "") not in {str(item) for item in lang_in}:
                return False

        policy_in = cond.get("qualityPolicyIn") if isinstance(cond.get("qualityPolicyIn"), list) else []
        if policy_in:
            if str(context.get("qualityPolicy") or "") not in {str(item) for item in policy_in}:
                return False

        return True

    def _validate_rule(
        self,
        data: dict[str, Any],
        *,
        default_id: str,
        now: str,
        created_at: Any = None,
    ) -> dict[str, Any]:
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("Rule name must not be empty")
        priority = int(data.get("priority", 100))
        condition = data.get("condition") if isinstance(data.get("condition"), dict) else {}
        actions = data.get("actions") if isinstance(data.get("actions"), dict) else {}

        jira_pattern = str(condition.get("jiraKeyPattern") or "").strip() or None
        text_regex = str(condition.get("textRegex") or "").strip() or None
        if jira_pattern:
            re.compile(jira_pattern)
        if text_regex:
            re.compile(text_regex)

        language_in = [str(item) for item in condition.get("languageIn", []) if str(item) in _ALLOWED_LANG]
        quality_policy_in = [str(item) for item in condition.get("qualityPolicyIn", []) if str(item) in _ALLOWED_QUALITY]

        quality_policy = str(actions.get("qualityPolicy") or "").strip() or None
        language = str(actions.get("language") or "").strip() or None
        target_path_template = str(actions.get("targetPathTemplate") or "").strip() or None
        apply_templates = [str(item) for item in actions.get("applyTemplates", []) if str(item).strip()]

        if quality_policy and quality_policy not in _ALLOWED_QUALITY:
            raise ValueError("Unsupported qualityPolicy")
        if language and language not in _ALLOWED_LANG:
            raise ValueError("Unsupported language")

        if not any([quality_policy, language, target_path_template, apply_templates]):
            raise ValueError("Rule actions must contain at least one value")

        return {
            "id": str(data.get("id") or default_id),
            "name": name,
            "enabled": bool(data.get("enabled", True)),
            "priority": priority,
            "source": str(data.get("source") or "api"),
            "createdAt": str(created_at or data.get("createdAt") or now),
            "updatedAt": now,
            "condition": {
                "jiraKeyPattern": jira_pattern,
                "languageIn": language_in,
                "qualityPolicyIn": quality_policy_in,
                "textRegex": text_regex,
            },
            "actions": {
                "qualityPolicy": quality_policy,
                "language": language,
                "targetPathTemplate": target_path_template,
                "applyTemplates": apply_templates,
            },
        }

    def _validate_template(
        self,
        data: dict[str, Any],
        *,
        default_id: str,
        now: str,
        created_at: Any = None,
    ) -> dict[str, Any]:
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("Template name must not be empty")
        priority = int(data.get("priority", 100))
        trigger_regex = str(data.get("triggerRegex") or "").strip() or None
        if trigger_regex:
            re.compile(trigger_regex)
        steps_raw = data.get("steps") if isinstance(data.get("steps"), list) else []
        steps = [str(item).strip() for item in steps_raw if str(item).strip()]
        if not steps:
            raise ValueError("Template steps must not be empty")

        return {
            "id": str(data.get("id") or default_id),
            "name": name,
            "enabled": bool(data.get("enabled", True)),
            "priority": priority,
            "source": str(data.get("source") or "api"),
            "triggerRegex": trigger_regex,
            "steps": steps,
            "createdAt": str(created_at or data.get("createdAt") or now),
            "updatedAt": now,
        }


__all__ = ["MemoryService"]
