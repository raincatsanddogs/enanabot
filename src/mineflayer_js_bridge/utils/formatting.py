from __future__ import annotations

import json
import time
from typing import Any
from uuid import uuid4


def now_ms() -> int:
    return int(time.time() * 1000)


def new_msg_id(kind: str) -> str:
    return f"msg_{now_ms()}_{kind}_{uuid4().hex[:4]}"


def parse_positive_int(value: str) -> int | None:
    if not value.isdigit():
        return None
    parsed = int(value)
    return parsed if parsed > 0 else None


def message_result_text(result: Any) -> str:
    if result is None:
        return "（无返回结果）"
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        for key in ("reply", "message", "text", "state"):
            value = result.get(key)
            if isinstance(value, str) and value:
                return value
        return json.dumps(result, ensure_ascii=False)
    return str(result)
