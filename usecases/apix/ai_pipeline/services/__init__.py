"""Chat / LLM interaction service."""

from __future__ import annotations

import json
import openai
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from ai_pipeline.logging_config import get_logger

logger = get_logger("services.chat")


class Status(Enum):
    ERROR = "error"
    OK = "ok"
    SKIPPED = "skipped"


class Role:
    USER = "user"
    TOOL = "tool"
    SYSTEM = "system"
    ASSISTANT = "assistant"

    @classmethod
    def get_valid_roles(cls) -> set:
        return {
            value
            for name, value in vars(cls).items()
            if name.isupper() and isinstance(value, str)
        }

    @classmethod
    def validate(cls, role: str) -> str:
        valid_roles = cls.get_valid_roles()
        if role not in valid_roles:
            raise ValueError(f"Invalid role: {role}")
        return role


def message_dict(role: str, prompt: str):
    return {"role": Role.validate(role), "content": prompt}


def get_timestamp():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


class Response(BaseModel):
    timestamp: str = Field(default_factory=get_timestamp)
    status: str
    message: str | dict
    completion_tokens: int = 0
    prompt_tokens: int = 0
    total_tokens: int = 0
    prompt_filters: Optional[list] = []


def parse_error(error) -> dict:
    prompt_filters = error.body.get("innererror", {}).get("content_filter_result", {})
    prompt_filters = [(category, data) for (category, data) in prompt_filters.items() if data["filtered"]]
    response = Response(status=Status.ERROR.value, message=error.body["message"], prompt_filters=prompt_filters)
    return response.model_dump()


def parse_response(response: dict, schema=None, parsed=None) -> dict:
    completion_tokens = response["usage"]["completion_tokens"]
    prompt_tokens = response["usage"]["prompt_tokens"]
    total_tokens = completion_tokens + prompt_tokens

    message = response["choices"][0]["message"]
    if schema is not None:
        # Structured output: always return a plain ``dict`` so downstream
        # steps (analysis persistence, weekly summary aggregation, reflection)
        # receive real fields instead of an opaque JSON string. Prefer the
        # SDK-parsed object; fall back to decoding the JSON content string.
        if parsed is None:
            parsed = message.get("parsed")
        if isinstance(parsed, BaseModel):
            content = parsed.model_dump()
        elif isinstance(parsed, dict):
            content = parsed
        else:
            raw = message.get("content")
            try:
                content = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
            except (json.JSONDecodeError, TypeError):
                content = {}
    else:
        content = message["content"]
    resp = Response(
        status=Status.OK.value,
        message=content,
        completion_tokens=completion_tokens,
        prompt_tokens=prompt_tokens,
        total_tokens=total_tokens,
    )
    return resp.model_dump()


async def request_completion(client, messages, model, temperature, schema, max_completion_tokens=None):
    kwargs = {"model": model, "messages": messages, "temperature": temperature}
    if max_completion_tokens:
        kwargs["max_completion_tokens"] = max_completion_tokens
    if schema:
        return await client.chat.completions.parse(response_format=schema, **kwargs)
    return await client.chat.completions.create(**kwargs)


def _is_content_filter(error: openai.BadRequestError) -> bool:
    body = getattr(error, "body", None) or {}
    if body.get("code") == "content_filter":
        return True
    return (body.get("innererror") or {}).get("code") == "ResponsibleAIPolicyViolation"


async def _query_impl(
    client: openai.AsyncOpenAI,
    user_prompt: str,
    system_prompt: str,
    model: str,
    temperature: float = 1.0,
    schema=None,
    max_completion_tokens: int | None = None,
    max_token_retries: int = 2,
):
    logger.debug("query start | model=%s schema=%s", model, schema.__name__ if schema else "None")
    messages = [
        message_dict("system", system_prompt),
        message_dict("user", user_prompt),
    ]

    tokens = max_completion_tokens or None  # 0/None => don't cap on first attempt
    import warnings

    for attempt in range(max_token_retries + 1):
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="Pydantic serializer warnings")
                response = await request_completion(
                    client, messages=messages, model=model,
                    temperature=temperature, schema=schema, max_completion_tokens=tokens,
                )
                response = response.model_dump()
            out = parse_response(response, schema=schema)
            logger.debug("query ok | tokens=%s", out.get("total_tokens"))
            return out

        except openai.LengthFinishReasonError:
            # Output was truncated. First recovery sets an explicit, larger
            # budget; subsequent retries double it before finally giving up.
            tokens = 16384 if not tokens else tokens * 2
            if attempt < max_token_retries:
                logger.warning("query truncated (length limit) - retrying with max_completion_tokens=%d", tokens)
                continue
            logger.warning("query truncated (length limit) - exhausted %d escalations", max_token_retries)
            return Response(status=Status.ERROR.value, message="length limit exceeded after escalation").model_dump()

        except openai.ContentFilterFinishReasonError:
            logger.warning("query content filter (response) - skipping item")
            return Response(status=Status.SKIPPED.value, message="content filter").model_dump()

        except openai.BadRequestError as error:
            if _is_content_filter(error):
                logger.warning("query content filter (prompt) - skipping item")
                out = parse_error(error)
                out["status"] = Status.SKIPPED.value
                return out
            logger.warning("query BadRequestError | %s", error)
            return parse_error(error)


async def query(
    client: openai.AsyncOpenAI,
    user_prompt: str,
    system_prompt: str,
    model: str,
    temperature: float = 1.0,
    schema=None,
    max_completion_tokens: int | None = None,
    max_token_retries: int = 2,
):
    """LLMOps-instrumented choke point for every pipeline LLM call.

    Delegates to :func:`_query_impl` (the unchanged OpenAI call) through the
    ``ai_pipeline.llmops`` adapter, which adds model-alias resolution, input/
    output guardrails, and a traced StepEvent (tokens + cost + latency). The
    adapter is fail-open: if the platform is unavailable the impl runs directly.
    """
    kwargs = dict(
        client=client,
        user_prompt=user_prompt,
        system_prompt=system_prompt,
        model=model,
        temperature=temperature,
        schema=schema,
        max_completion_tokens=max_completion_tokens,
        max_token_retries=max_token_retries,
    )
    try:
        from ai_pipeline import llmops

        return await llmops.instrumented_query(_query_impl, **kwargs)
    except Exception:
        return await _query_impl(**kwargs)
