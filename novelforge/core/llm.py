import json
import os
import time
from typing import Any, Dict

from openai import OpenAI

from .cache import load_cached_text, store_cached_text
from .config import Settings


def get_client(settings: Settings) -> OpenAI:
    return OpenAI(
        base_url=settings.base_url,
        api_key=settings.api_key,
        timeout=1800,
    )


def _extract_json(text: str) -> Dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Extract the first valid JSON object by scanning balanced braces.
    start = text.find("{")
    while start != -1:
        depth = 0
        for idx in range(start, len(text)):
            char = text[idx]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : idx + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)

    raise ValueError("No JSON object found in model output.")


def _extract_output_text(response) -> str:
    if getattr(response, "output_text", None):
        return response.output_text

    if getattr(response, "output", None):
        parts = []
        for item in response.output:
            if getattr(item, "type", None) == "message":
                for content in getattr(item, "content", []) or []:
                    if getattr(content, "type", None) == "output_text":
                        parts.append(getattr(content, "text", ""))
        text = "".join(parts).strip()
        if text:
            return text

    return ""


def call_text(client: OpenAI, model: str, prompt: str, max_output_tokens: int | None = None) -> str:
    cached = load_cached_text(model, prompt)
    if cached is not None:
        return cached

    debug = os.getenv("NOVELFORGE_DEBUG", "").strip().lower() in {"1", "true", "yes"}
    last_error = None
    for attempt in range(3):
        kwargs = {
            "model": model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": prompt,
                        }
                    ],
                }
            ],
        }
        if max_output_tokens is not None:
            kwargs["max_output_tokens"] = max_output_tokens
        response = client.responses.create(**kwargs)
        text = _extract_output_text(response)
        if text:
            store_cached_text(model, prompt, text)
            return text
        if getattr(response, "error", None):
            last_error = RuntimeError(f"Model error: {response.error}")
            if debug:
                print("---- NOVELFORGE MODEL ERROR ----")
                print(response.error)
                print("---- END MODEL ERROR ----")
            break
        if debug:
            print("---- NOVELFORGE EMPTY RESPONSE ----")
            print(response)
            print("---- END EMPTY RESPONSE ----")
        time.sleep(1)

    if last_error:
        raise last_error
    raise RuntimeError("Empty model response output.")


def call_json(client: OpenAI, model: str, prompt: str, max_output_tokens: int | None = None) -> Dict[str, Any]:
    text = call_text(client, model, prompt, max_output_tokens=max_output_tokens)
    if os.getenv("NOVELFORGE_DEBUG", "").strip().lower() in {"1", "true", "yes"}:
        print("---- NOVELFORGE RAW MODEL OUTPUT ----")
        print(text)
        print("---- END RAW MODEL OUTPUT ----")
    return _extract_json(text)
