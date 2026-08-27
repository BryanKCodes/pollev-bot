#!/usr/bin/env python3
"""Extract and redact useful PollEV entries from a browser-exported HAR file.

This utility deliberately uses only the Python standard library so it can be run
before the project's dependencies are installed. It is intended for captures
from an authorized test account containing synthetic question text.
"""

import argparse
import base64
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "password",
    "pe_auth_token",
    "samlresponse",
    "set_cookie",
    "token",
    "x_csrf_token",
    "csrf_token",
    "firehose_token",
}

IDENTIFIER_KEYS = {
    "activity_id",
    "option_id",
    "poll_id",
    "uid",
}

INTERESTING_TERMS = (
    "activity",
    "csrf",
    "firehose",
    "free_text",
    "multiple_choice",
    "open_ended",
    "poll",
    "response",
    "result",
)

POLL_ID_PATH = re.compile(
    r"/(multiple_choice_polls|open_ended_polls|free_text_polls|polls)/[^/?#]+",
    re.IGNORECASE,
)


def _placeholder(value: Any, prefix: str, mapping: Dict[Tuple[str, str], str]) -> str:
    key = (prefix, str(value))
    if key not in mapping:
        mapping[key] = "<{}-{}>".format(prefix.replace("_", "-"), len(mapping) + 1)
    return mapping[key]


def redact_value(value: Any, key: str, mapping: Dict[Tuple[str, str], str]) -> Any:
    """Redact secrets and opaque identifiers while retaining JSON structure."""
    normalized_key = key.lower().replace("-", "_")
    if normalized_key in SENSITIVE_KEYS or normalized_key.endswith("_token"):
        return "<redacted>"
    if normalized_key in IDENTIFIER_KEYS or normalized_key == "id":
        return _placeholder(value, normalized_key, mapping)
    if isinstance(value, dict):
        return {
            str(child_key): redact_value(child_value, str(child_key), mapping)
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item, key, mapping) for item in value]
    return value


def redact_url(url: str) -> str:
    parsed = urlsplit(url)
    path = POLL_ID_PATH.sub(r"/\1/<poll-id>", parsed.path)
    query_items = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        normalized_key = key.lower().replace("-", "_")
        query_items.append((key, "<redacted>" if (
            normalized_key in SENSITIVE_KEYS or normalized_key.endswith("_token")
        ) else value))
    return urlunsplit((parsed.scheme, "<pollev-host>", path, urlencode(query_items), ""))


def redact_headers(headers: Iterable[Dict[str, str]]) -> List[Dict[str, str]]:
    redacted = []
    for header in headers:
        name = header.get("name", "")
        normalized_name = name.lower().replace("-", "_")
        value = header.get("value", "")
        if normalized_name in SENSITIVE_KEYS or normalized_name.endswith("_token"):
            value = "<redacted>"
        redacted.append({"name": name, "value": value})
    return redacted


def redact_post_data(post_data: Optional[Dict[str, Any]], mapping: Dict[Tuple[str, str], str]) -> Optional[Dict[str, Any]]:
    if not post_data:
        return post_data

    result = {key: value for key, value in post_data.items() if key != "text"}
    if "params" in post_data:
        result["params"] = [
            {
                "name": item.get("name", ""),
                "value": redact_value(item.get("value", ""), item.get("name", ""), mapping),
            }
            for item in post_data["params"]
        ]
    if "text" in post_data:
        text = post_data["text"]
        if post_data.get("mimeType", "").startswith("application/json"):
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError):
                result["text"] = "<json body could not be parsed; review manually>"
            else:
                result["text"] = json.dumps(redact_value(parsed, "body", mapping), sort_keys=True)
        else:
            result["text"] = "<body omitted; review field names in the Network panel>"
    return result


def redact_response_content(content: Dict[str, Any], mapping: Dict[Tuple[str, str], str]) -> Dict[str, Any]:
    result = {
        key: value for key, value in content.items()
        if key not in {"text", "_transferSize"}
    }
    text = content.get("text")
    if content.get("encoding") == "base64":
        try:
            text = base64.b64decode(text or "").decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            result["text"] = "<base64 body omitted>"
            return result
    if isinstance(text, str) and text:
        try:
            parsed = json.loads(text)
        except ValueError:
            result["text"] = "<non-JSON body omitted>"
        else:
            result["text"] = redact_value(parsed, "body", mapping)
    else:
        result["text"] = text
    return result


def is_interesting(url: str) -> bool:
    lowered = url.lower()
    return any(term in lowered for term in INTERESTING_TERMS)


def classify(method: str, url: str) -> str:
    lowered = url.lower()
    if method.upper() in {"POST", "PUT", "PATCH"} and any(
        term in lowered for term in ("result", "response", "answer", "vote")
    ):
        return "submit"
    if "firehose" in lowered:
        return "activity-discovery"
    if "csrf" in lowered:
        return "csrf"
    return "fetch"


def redact_entry(entry: Dict[str, Any], mapping: Dict[Tuple[str, str], str]) -> Dict[str, Any]:
    request = entry.get("request", {})
    response = entry.get("response", {})
    method = request.get("method", "GET")
    url = request.get("url", "")
    return {
        "classification": classify(method, url),
        "request": {
            "method": method,
            "url": redact_url(url),
            "headers": redact_headers(request.get("headers", [])),
            "postData": redact_post_data(request.get("postData"), mapping),
        },
        "response": {
            "status": response.get("status"),
            "statusText": response.get("statusText"),
            "headers": redact_headers(response.get("headers", [])),
            "content": redact_response_content(response.get("content", {}), mapping),
        },
    }


def build_capture(har: Dict[str, Any]) -> Dict[str, Any]:
    entries = har.get("log", {}).get("entries", [])
    mapping: Dict[Tuple[str, str], str] = {}
    selected = [
        redact_entry(entry, mapping)
        for entry in entries
        if is_interesting(entry.get("request", {}).get("url", ""))
    ]
    return {
        "format": "pollevbot-redacted-har-v1",
        "warning": "Review question text and response text manually before committing.",
        "entries": selected,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract redacted PollEV API entries from a browser HAR export."
    )
    parser.add_argument("har", type=Path, help="Input HAR file exported from browser DevTools")
    parser.add_argument("output", type=Path, help="Output JSON fixture path")
    args = parser.parse_args()

    with args.har.open("r", encoding="utf-8") as handle:
        har = json.load(handle)
    capture = build_capture(har)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(capture, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print("Wrote {} interesting entries to {}".format(len(capture["entries"]), args.output))


if __name__ == "__main__":
    main()
