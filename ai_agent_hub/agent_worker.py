"""Agent worker that processes queued envelopes and dispatches intents."""
from __future__ import annotations

import json
import importlib
import os
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from xml.etree import ElementTree

from ai_agent_hub import Envelope
from ai_agent_hub.cli_skills import CliSkillRunner
from ai_agent_hub.entropy_monitor import EntropyMonitor
from ai_agent_hub.payment_gateway import PaymentGateway
from ai_agent_hub.lmtp_handler import (
    FAILED,
    PROCESSED,
    FileSystemRepository,
    SQLiteRepository,
    get_queue_dir,
    get_repository,
)
from ai_agent_hub.human_in_the_loop import ApprovalRequest, ApprovalStore
from ai_agent_hub.smtp_sender import send_envelope_via_smtp

WORKER_ENV_FILES = (
    Path(".env"),
    Path.home() / ".bashrc",
)
OLLAMA_CONFIG_PATH = Path("/etc/ai-agent-hub/config")


def _iter_key_value_lines(path: Path) -> list[tuple[str, str]]:
    if not path.exists():
        return []

    pairs: list[tuple[str, str]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            pairs.append((key, value))
    return pairs


def _bootstrap_worker_environment() -> None:
    for path in WORKER_ENV_FILES:
        for key, value in _iter_key_value_lines(path):
            if key not in os.environ and value:
                os.environ[key] = value


def _read_config_value(path: Path, key_name: str) -> str | None:
    for key, value in _iter_key_value_lines(path):
        if key == key_name and value:
            return value
    return None


_bootstrap_worker_environment()

PROCESSED_DIR = Path(
    os.environ.get("AI_AGENT_HUB_PROCESSED_DIR")
    or os.environ.get("AGENT_HUB_PROCESSED_DIR")
    or "./processed"
)
REPLIES_DIR = Path(
    os.environ.get("AI_AGENT_HUB_REPLIES_DIR")
    or os.environ.get("AGENT_HUB_REPLIES_DIR")
    or "./replies"
)


INTENT_HANDLERS: Dict[str, Callable[[Envelope], Optional[Any]]] = {}


def _get_entropy_threshold() -> float:
    return float(os.environ.get("AI_AGENT_HUB_ENTROPY_THRESHOLD", "0.3"))


def _extract_json_object(raw_text: str) -> dict[str, Any] | None:
    text = raw_text.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _parse_rss_items(content: str, limit: int = 6) -> list[str]:
    if not content.strip():
        return []

    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError:
        return []

    items: list[str] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        description = (item.findtext("description") or "").strip()
        line = " - ".join(part for part in (title, description) if part)
        if line:
            items.append(line)
        if len(items) >= limit:
            break
    return items


def intent_handler(name: str) -> Callable[[Callable[[Envelope], Optional[Any]]], Callable[[Envelope], Optional[Any]]]:
    """Decorator to register an intent handler."""

    def decorator(func: Callable[[Envelope], Optional[Any]]) -> Callable[[Envelope], Optional[Any]]:
        INTENT_HANDLERS[name] = func
        return func

    return decorator


def _find_oldest_queue_file() -> Optional[Path]:
    queue_dir = get_queue_dir()
    if not queue_dir.exists():
        return None

    files = [p for p in queue_dir.iterdir() if p.is_file()]
    if not files:
        return None

    return sorted(files, key=lambda p: p.stat().st_mtime)[0]


def _load_envelope(file_path: Path) -> Envelope:
    raw = file_path.read_text(encoding="utf-8")
    return Envelope.from_json(raw)


def get_intent(envelope: Envelope | dict[str, Any]) -> Optional[str]:
    if isinstance(envelope, Envelope):
        envelope_dict: dict[str, Any] = {
            "intent": None,
            "payload": envelope.payload if isinstance(envelope.payload, dict) else {},
        }
    else:
        envelope_dict = envelope

    payload = envelope_dict.get("payload") or {}
    intent = envelope_dict.get("intent")
    if isinstance(intent, str) and intent:
        return intent
    nested_intent = payload.get("intent")
    if isinstance(nested_intent, str) and nested_intent:
        return nested_intent
    nested_payload = payload.get("payload")
    if isinstance(nested_payload, dict):
        nested_payload_intent = nested_payload.get("intent")
        if isinstance(nested_payload_intent, str) and nested_payload_intent:
            return nested_payload_intent
    return None


def _extract_intent(env: Envelope) -> Optional[str]:
    payload = env.payload if isinstance(env.payload, dict) else {}
    if isinstance(payload, dict):
        headers = payload.get("headers")
        if isinstance(headers, dict):
            payment_required = headers.get("X-Agent-Payment-Required")
            if str(payment_required).lower() in {"1", "true", "yes"}:
                return "payment"

    return get_intent(env)


def find_answers(obj: Any) -> dict[str, Any] | None:
    if isinstance(obj, str):
        parsed = _extract_json_object(obj)
        if isinstance(parsed, dict):
            return find_answers(parsed)
        return None

    if isinstance(obj, dict):
        if "answers" in obj and isinstance(obj["answers"], dict):
            return obj["answers"]

        for value in obj.values():
            result = find_answers(value)
            if result:
                return result
        return None

    if isinstance(obj, list):
        for item in obj:
            result = find_answers(item)
            if result:
                return result
        return None

    return None


@intent_handler("ping")
def _handle_ping(_: Envelope) -> dict:
    return {"pong": True}


@intent_handler("echo")
def _handle_echo(env: Envelope) -> dict:
    text = ""
    if isinstance(env.payload, dict):
        text_val = env.payload.get("text")
        if isinstance(text_val, str):
            text = text_val
        else:
            text = json.dumps(env.payload, ensure_ascii=False)
    else:
        text = str(env.payload)
    return {"echo": text}


@intent_handler("help")
@intent_handler("list-intents")
def _handle_help(_: Envelope) -> dict:
    return {"intents": sorted(INTENT_HANDLERS.keys())}


@intent_handler("summarize")
def _handle_summarize(env: Envelope) -> dict:
    text = ""
    if isinstance(env.payload, dict):
        payload_text = env.payload.get("text")
        if isinstance(payload_text, str):
            text = payload_text
        else:
            text = json.dumps(env.payload, ensure_ascii=False)
    else:
        text = str(env.payload)

    summary = textwrap.shorten(text, width=100, placeholder="…")
    return {"summary": summary}


@intent_handler("entropy-check")
def _handle_entropy_check(env: Envelope) -> dict:
    threshold = _get_entropy_threshold()
    thread_id = "default"
    messages: list[str] = []

    if isinstance(env.payload, dict):
        payload_thread_id = env.payload.get("thread_id")
        if isinstance(payload_thread_id, str) and payload_thread_id:
            thread_id = payload_thread_id

        payload_threshold = env.payload.get("threshold")
        if payload_threshold is not None:
            try:
                threshold = float(payload_threshold)
            except (TypeError, ValueError):
                pass

        payload_messages = env.payload.get("messages")
        if isinstance(payload_messages, list):
            messages = [message for message in payload_messages if isinstance(message, str)]

    monitor = EntropyMonitor()
    for message in messages:
        monitor.add_message(thread_id, message)

    entropy = monitor.get_entropy(thread_id)
    is_low = monitor.is_low_entropy(thread_id, threshold=threshold)
    injected_context = monitor.check_and_inject(thread_id, threshold=threshold)
    return {
        "entropy": entropy,
        "is_low": is_low,
        "injected_context": injected_context,
    }




@intent_handler("cli-skill")
def _handle_cli_skill(env: Envelope) -> dict:
    if not isinstance(env.payload, dict):
        return {"error": "payload must be a dict", "exit_code": 1}

    skill = env.payload.get("skill")
    args = env.payload.get("args", [])
    stdin = env.payload.get("stdin")

    if not isinstance(skill, str) or not skill:
        return {"error": "payload.skill is required", "exit_code": 1}
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        return {"error": "payload.args must be a list[str]", "exit_code": 1}
    if stdin is not None and not isinstance(stdin, str):
        return {"error": "payload.stdin must be a string or null", "exit_code": 1}

    return CliSkillRunner().run(skill=skill, args=args, stdin=stdin)


@intent_handler("cli-pipeline")
def _handle_cli_pipeline(env: Envelope) -> dict:
    if not isinstance(env.payload, dict):
        return {"error": "payload must be a dict", "exit_code": 1}

    steps = env.payload.get("steps")
    if not isinstance(steps, list) or not steps:
        return {"error": "payload.steps must be a non-empty list", "exit_code": 1}

    runner = CliSkillRunner()
    # Pass payload stdin to the first pipeline step when provided.
    stdin: str | None = env.payload.get("stdin") if isinstance(env.payload.get("stdin"), str) else None
    last_result: dict[str, Any] = {"error": "payload.steps must be a non-empty list", "exit_code": 1}

    for step in steps:
        if not isinstance(step, dict):
            return {"error": "each pipeline step must be a dict", "exit_code": 1}

        skill = step.get("skill")
        args = step.get("args", [])

        if not isinstance(skill, str) or not skill:
            return {"error": "each pipeline step requires skill", "exit_code": 1}
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            return {"error": "each pipeline step args must be a list[str]", "exit_code": 1}

        last_result = runner.run(skill=skill, args=args, stdin=stdin)
        if last_result.get("exit_code") != 0:
            return last_result
        stdin = last_result.get("output") if isinstance(last_result.get("output"), str) else None

    return last_result

@intent_handler("request-approval")
def _handle_request_approval(env: Envelope) -> dict:
    if not isinstance(env.payload, dict):
        return {"error": "payload must be a dict"}

    description = env.payload.get("description")
    approver = env.payload.get("approver")
    callback_payload = env.payload.get("callback_payload")
    if not isinstance(callback_payload, dict):
        callback_payload = env.payload.get("callback")

    text_payload = env.payload.get("text")
    if isinstance(text_payload, str):
        try:
            parsed_text_payload = json.loads(text_payload)
        except json.JSONDecodeError:
            parsed_text_payload = None
        if isinstance(parsed_text_payload, dict):
            if not isinstance(description, str) or not description:
                description = parsed_text_payload.get("description")
            if not isinstance(approver, str) or not approver:
                approver = parsed_text_payload.get("approver")
            if not isinstance(callback_payload, dict):
                fallback_callback = parsed_text_payload.get("callback_payload")
                if not isinstance(fallback_callback, dict):
                    fallback_callback = parsed_text_payload.get("callback")
                callback_payload = fallback_callback

    if not isinstance(description, str) or not description:
        return {"error": "payload.description is required"}
    if not isinstance(approver, str) or not approver:
        return {"error": "payload.approver is required"}
    if not isinstance(callback_payload, dict):
        return {"error": "payload.callback_payload must be a dict"}

    request = ApprovalRequest(
        envelope_id=env.id,
        thread_id=env.context or env.id,
        description=description,
        requester=env.sender,
        approver=approver,
        status="pending",
        created_at=env.created_at,
        decided_at=None,
        callback_payload=callback_payload,
    )
    ApprovalStore().create(request)
    return {
        "status": "pending",
        "approval_id": request.envelope_id,
        "message": "承認待ちです",
    }


@intent_handler("approve")
def _handle_approve(env: Envelope) -> dict:
    if not isinstance(env.payload, dict):
        return {"error": "payload must be a dict"}

    approval_id = env.payload.get("approval_id")
    if not isinstance(approval_id, str) or not approval_id:
        return {"error": "payload.approval_id is required"}

    approved_request = ApprovalStore().approve(approval_id)
    callback_envelope = Envelope.new(
        envelope_type="command",
        sender=approved_request.approver,
        recipient=approved_request.requester,
        payload=approved_request.callback_payload,
        context=approved_request.thread_id,
        in_reply_to=approved_request.envelope_id,
    )
    send_envelope_via_smtp(callback_envelope)
    return {"status": "approved", "message": "承認しました"}


@intent_handler("reject")
def _handle_reject(env: Envelope) -> dict:
    if not isinstance(env.payload, dict):
        return {"error": "payload must be a dict"}

    approval_id = env.payload.get("approval_id")
    reason = env.payload.get("reason")
    if not isinstance(approval_id, str) or not approval_id:
        return {"error": "payload.approval_id is required"}
    if not isinstance(reason, str) or not reason:
        return {"error": "payload.reason is required"}

    ApprovalStore().reject(approval_id, reason)
    return {"status": "rejected", "reason": reason}


@intent_handler("list-pending-approvals")
def _handle_list_pending_approvals(_: Envelope) -> dict:
    pending = ApprovalStore().list_pending()
    return {"pending": [request.to_dict() for request in pending]}


@intent_handler("payment")
def _handle_payment(env: Envelope) -> dict:
    api_key = os.environ.get("CIRCLE_API_KEY")
    if not api_key:
        return {"error": "CIRCLE_API_KEY is not set"}

    gateway = PaymentGateway(api_key=api_key)
    return gateway.execute(env)


@intent_handler("llm-query")
def _handle_llm_query(env: Envelope) -> dict:
    text = None
    if isinstance(env.payload, dict):
        text = env.payload.get("text")
    if not text:
        return {"error": "payload.text is required"}

    provider = os.environ.get("LLM_PROVIDER", "ollama").strip().lower()

    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return {"error": "OPENAI_API_KEY is not set"}

        try:
            openai = importlib.import_module("openai")
        except ImportError:
            return {"error": "openai package not installed"}

        model = "gpt-4o-mini"
        if isinstance(env.payload, dict):
            model = env.payload.get("model", model)

        try:
            client = openai.OpenAI(api_key=api_key)
            response = client.responses.create(model=model, input=text)
            return {"result": response.output_text}
        except Exception as exc:
            return {"error": str(exc)}

    api_key = None
    if isinstance(env.payload, dict):
        payload_api_key = env.payload.get("api_key")
        if isinstance(payload_api_key, str) and payload_api_key.strip():
            api_key = payload_api_key.strip()
    if not api_key:
        api_key = os.environ.get("OLLAMA_API_KEY")
    if not api_key:
        return {"error": "OLLAMA_API_KEY is not set (checked payload and env)"}

    model = "gemma3:4b"
    if isinstance(env.payload, dict):
        model = env.payload.get("model", model)

    try:
        httpx = importlib.import_module("httpx")
    except ImportError:
        return {"error": "httpx package not installed"}

    try:
        response = httpx.post(
            "https://api.ollama.com/api/chat",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": text}],
                "stream": False,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        message = data.get("message", {})
        content = message.get("content")
        if not isinstance(content, str):
            return {"error": "Ollama response did not include message.content"}
        return {"result": content}
    except Exception as exc:
        return {"error": str(exc)}


def _llm_json_response(prompt: str, model: str = "gemma3:4b") -> dict[str, Any]:
    env = Envelope.new(
        envelope_type="command",
        sender="https://agent.local/@worker",
        recipient="https://agent.local/@worker",
        payload={"intent": "llm-query", "text": prompt, "model": model},
    )
    response = _handle_llm_query(env)
    result_text = response.get("result")
    if not isinstance(result_text, str):
        return {}
    return _extract_json_object(result_text) or {}


@intent_handler("threat-scan")
def _handle_threat_scan(env: Envelope) -> dict[str, Any]:
    if not isinstance(env.payload, dict):
        return {"error": "payload must be a dict"}

    keywords = env.payload.get("keywords")
    languages = env.payload.get("languages")
    sector = env.payload.get("sector")
    if not isinstance(keywords, list) or not all(isinstance(k, str) and k.strip() for k in keywords):
        return {"error": "payload.keywords must be a non-empty list[str]"}
    if not isinstance(languages, list) or not all(isinstance(lang, str) and lang.strip() for lang in languages):
        return {"error": "payload.languages must be a non-empty list[str]"}
    if not isinstance(sector, str) or not sector.strip():
        return {"error": "payload.sector is required"}

    runner = CliSkillRunner()
    rss_urls = ["https://news.ycombinator.com/rss"]
    for keyword in keywords[:4]:
        for language in languages[:4]:
            rss_urls.append(
                "https://news.google.com/rss/search?"
                f"q={keyword.strip().replace(' ', '+')}&hl={language}&gl=US&ceid=US:{language}"
            )

    snippets: list[str] = []
    for url in rss_urls:
        result = runner.run(skill="curl", args=[url])
        if result.get("exit_code") != 0:
            continue
        output = result.get("output")
        if isinstance(output, str):
            snippets.extend(_parse_rss_items(output))

    content = "\n".join(snippets[:30])
    prompt = f"""
以下のニュースから猫への脅威（虐待、危険、事故等）を検知してください。
脅威レベル（1-5）とラベルを返してください。
JSON形式で返答: {{"level": 1-5, "label": "説明", "active": true/false}}
ニュース: {content}
"""
    judged = _llm_json_response(prompt)
    try:
        level = int(judged.get("level", 1))
    except (TypeError, ValueError):
        level = 1
    level = max(1, min(5, level))
    label = judged.get("label") if isinstance(judged.get("label"), str) else "cat safety baseline"
    active = bool(judged.get("active", False))

    cells = [{"threatLevel": level, "label": label, "active": active}]
    while len(cells) < 16:
        cells.append({"threatLevel": 1, "label": "baseline", "active": False})

    if level >= 4:
        activity_label = "HIGH"
    elif level >= 3:
        activity_label = "MODERATE"
    else:
        activity_label = "LOW"

    return {
        "sector": sector,
        "cells": cells,
        "activityLabel": activity_label,
        "source": "ai-agent-hub-monitor",
        "updatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


@intent_handler("cat-assessment")
def _handle_cat_assessment(env: Envelope) -> dict[str, Any]:
    envelope_data: dict[str, Any] = {
        "payload": env.payload,
    }
    if isinstance(env.payload, dict) and isinstance(env.payload.get("answers"), dict):
        envelope_data["answers"] = env.payload.get("answers")

    payload = envelope_data.get("payload")
    if not isinstance(payload, dict):
        payload = {}

    answers = payload.get("answers")
    if answers is None and isinstance(envelope_data.get("answers"), dict):
        answers = envelope_data["answers"]
    if answers is None and isinstance(payload.get("payload"), dict):
        nested_payload = payload.get("payload", {})
        nested_answers = nested_payload.get("answers")
        if isinstance(nested_answers, dict):
            answers = nested_answers
        elif isinstance(nested_payload.get("payload"), dict):
            deep_nested_answers = nested_payload.get("payload", {}).get("answers")
            if isinstance(deep_nested_answers, dict):
                answers = deep_nested_answers

    print("=== WORKER PAYLOAD ===")
    print(payload)
    print("=== WORKER ANSWERS ===")
    print(answers)

    if answers is None:
        return {
            "error": "answers missing",
            "status": "failed",
            "debug": envelope_data,
        }
    if not isinstance(answers, dict):
        return {
            "error": "answers must be a dict",
            "status": "failed",
            "debug": envelope_data,
        }

    prompt = (
        "以下の飼い主候補情報を審査し、猫の飼育適正を評価してください。"
        "厳格な審査官『パトラ閣下』として回答し、JSONのみを返してください。"
        '形式: {"score": 0-100, "verdict": "APPROVED|PROBATION|REJECTED", '
        '"patra_message": "string", "strengths": ["..."], "concerns": ["..."]}\n'
        f"回答情報: {json.dumps(answers, ensure_ascii=False)}"
    )
    judged = _llm_json_response(prompt)
    if not judged:
        return {
            "score": 0,
            "verdict": "REJECTED",
            "patra_message": "評価情報を生成できませんでした。",
            "strengths": [],
            "concerns": ["LLM応答が不正でした"],
        }

    score_raw = judged.get("score", 0)
    try:
        score = int(score_raw)
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(100, score))

    verdict = judged.get("verdict")
    if verdict not in {"APPROVED", "PROBATION", "REJECTED"}:
        verdict = "PROBATION" if score >= 60 else "REJECTED"

    patra_message = judged.get("patra_message")
    if not isinstance(patra_message, str):
        patra_message = "提出情報を再評価せよ。"

    strengths = judged.get("strengths")
    concerns = judged.get("concerns")
    if not isinstance(strengths, list):
        strengths = []
    if not isinstance(concerns, list):
        concerns = []

    return {
        "score": score,
        "verdict": verdict,
        "patra_message": patra_message,
        "strengths": [item for item in strengths if isinstance(item, str)],
        "concerns": [item for item in concerns if isinstance(item, str)],
    }


def _build_reply(env: Envelope, result_payload: Any) -> Envelope:
    return Envelope.new(
        envelope_type="reply",
        sender=env.recipient,
        recipient=env.sender,
        payload=result_payload,
        context=env.context,
        in_reply_to=env.id,
    )


def _handle_envelope(env: Envelope) -> Optional[Envelope]:
    intent_name = _extract_intent(env)
    if not intent_name:
        print("Missing intent; generating error reply", env.id)
        return _build_reply(env, {"error": "No intent found", "status": "failed"})

    handler = INTENT_HANDLERS.get(intent_name)

    if handler:
        print(
            f"[agent_worker] intent={intent_name} from={env.sender} → handler={handler.__name__}"
        )
        try:
            reply_payload = handler(env)
        except Exception as exc:  # pragma: no cover - safeguard
            print(f"Handler error for intent '{intent_name}': {exc}")
            reply_payload = {"error": str(exc)}
    else:
        print(f"[agent_worker] intent={intent_name} from={env.sender} → handler=UNKNOWN")
        reply_payload = {"error": "unknown intent"}

    if reply_payload is None:
        reply_payload = {"error": "empty handler response", "status": "failed"}

    return _build_reply(env, reply_payload)


def _mark_processed(env_id: str) -> None:
    repository = get_repository()

    if isinstance(repository, SQLiteRepository):
        repository.mark_status(env_id, PROCESSED)
        return

    if isinstance(repository, FileSystemRepository):
        file_path = repository.find_file_by_id(env_id)
        if file_path is None:
            return
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        destination = PROCESSED_DIR / file_path.name
        file_path.rename(destination)


def _mark_failed(env_id: str) -> None:
    repository = get_repository()
    if isinstance(repository, SQLiteRepository):
        repository.mark_status(env_id, FAILED)


def _save_reply(in_reply_to: str, reply: Envelope) -> None:
    os.makedirs(REPLIES_DIR, exist_ok=True)
    reply_path = REPLIES_DIR / f"{in_reply_to}.json"
    reply_path.write_text(reply.to_json(indent=2), encoding="utf-8")


def process_next_envelope() -> bool:
    """Process the oldest envelope in storage if present."""

    repository = get_repository()
    pending = repository.list_pending()
    if not pending:
        return False

    env = pending[0]
    reply = _handle_envelope(env)

    try:
        if reply:
            _save_reply(env.id, reply)
        _mark_processed(env.id)

        if reply:
            send_envelope_via_smtp(reply)
        return True
    except Exception:
        _mark_failed(env.id)
        raise


def main(poll_interval: float = 1.0) -> None:
    """Continuously watch storage and process envelopes."""

    while True:
        processed = process_next_envelope()
        if not processed:
            time.sleep(poll_interval)


if __name__ == "__main__":
    main()
