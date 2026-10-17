import copy
import json

import requests


class ExternalFailure(RuntimeError):
    """Only fixed safe messages; never provider response bodies or request credentials."""


def post_json(url, payload, headers=None, timeout=(10, 120), max_bytes=4 * 1024 * 1024):
    # No POST retry: an ambiguous timeout could have already incurred usage.
    try:
        with requests.post(url, json=payload, headers=headers or {}, timeout=timeout, allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                category = "rate_limited" if response.status_code == 429 else "provider_unavailable" if response.status_code >= 500 else "request_rejected"
                raise ExternalFailure(f"{category}: HTTP {response.status_code}; inspect service configuration, then start an explicit new attempt")
            content = bytearray()
            for chunk in response.iter_content(64 * 1024):
                content.extend(chunk)
                if len(content) > max_bytes:
                    raise ExternalFailure("Provider response exceeded the configured bound")
            try:
                return json.loads(content)
            except (ValueError, UnicodeDecodeError):
                raise ExternalFailure("Provider did not return valid JSON") from None
    except requests.RequestException as exc:
        raise ExternalFailure(f"transport_{type(exc).__name__}: outcome may be ambiguous; no automatic retry") from None


def post_chat_streaming(url, payload, headers=None, timeout=(10, 120), max_bytes=4 * 1024 * 1024):
    """Streaming OpenAI-compatible chat completion.

    Long structured generations exceed some gateways' non-streaming idle cap
    (~120 s on the Hugging Face router); an SSE stream keeps the connection
    alive while tokens arrive and reassembles into the non-streaming shape.
    """
    body_payload = copy.deepcopy(payload)
    body_payload["stream"] = True
    body_payload["stream_options"] = {"include_usage": True}
    try:
        with requests.post(url, json=body_payload, headers=headers or {}, timeout=timeout, allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                category = "rate_limited" if response.status_code == 429 else "provider_unavailable" if response.status_code >= 500 else "request_rejected"
                raise ExternalFailure(f"{category}: HTTP {response.status_code}; inspect service configuration, then start an explicit new attempt")
            model = None
            content_parts = []
            finish_reason = None
            usage = None
            received = 0
            done = False
            tool_calls = False
            for line in response.iter_lines(chunk_size=1024):
                received += len(line) + 1
                if received > max_bytes:
                    raise ExternalFailure("Provider response exceeded the configured bound")
                text = line.strip()
                if not text.startswith(b"data:"):
                    continue
                data = text[5:].strip()
                if data == b"[DONE]":
                    done = True
                    break
                try:
                    event = json.loads(data)
                    if not isinstance(event, dict) or event.get("error"):
                        raise ValueError
                    model = event.get("model") or model
                    if event.get("usage"):
                        usage = event["usage"]
                    for choice in event.get("choices") or []:
                        if choice.get("index", 0) != 0:
                            continue
                        delta = choice.get("delta") or {}
                        tool_calls |= bool(delta.get("tool_calls") or delta.get("function_call"))
                        part = delta.get("content")
                        if part is not None:
                            if not isinstance(part, str):
                                raise ValueError
                            content_parts.append(part)
                        finish_reason = choice.get("finish_reason") or finish_reason
                except (ValueError, TypeError, AttributeError):
                    raise ExternalFailure("Provider returned an invalid chat stream") from None
            if not done or not finish_reason or not content_parts:
                raise ExternalFailure("Provider chat stream ended before completion")
            message = {"role": "assistant", "content": "".join(content_parts)}
            if tool_calls:
                message["tool_calls"] = True
            return {"model": model, "choices": [{"message": message, "finish_reason": finish_reason}], "usage": usage}
    except requests.RequestException as exc:
        raise ExternalFailure(f"transport_{type(exc).__name__}: outcome may be ambiguous; no automatic retry") from None
