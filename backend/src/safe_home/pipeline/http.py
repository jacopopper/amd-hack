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
            import json
            try:
                return json.loads(content)
            except (ValueError, UnicodeDecodeError):
                raise ExternalFailure("Provider did not return valid JSON") from None
    except requests.RequestException as exc:
        raise ExternalFailure(f"transport_{type(exc).__name__}: outcome may be ambiguous; no automatic retry") from None
