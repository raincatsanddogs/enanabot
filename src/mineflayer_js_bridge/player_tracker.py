"""在线玩家数据追踪与存储模块。

数据格式说明：
- player_tracking.jsonl：每行一条快照 {"t": unix_ts, "p": ["name1", "name2"]}
- player_meta.json：玩家元数据 {"name": {"uuid": "...", "skin_url": "..."}}
- player_heads/：缓存的 32×32 头像 PNG（以 UUID 命名）
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import time
from pathlib import Path
from typing import Any

from nonebot import logger

DATA_DIR = Path(__file__).resolve().parents[2] / "configs"
TRACKING_FILE = DATA_DIR / "player_tracking.jsonl"
META_FILE = DATA_DIR / "player_meta.json"
HEADS_DIR = DATA_DIR / "player_heads"
BOT_NAME_FILE = DATA_DIR / "bot_username.txt"

TWELVE_MONTHS_SECONDS = 365 * 24 * 3600


def _save_bot_username(name: str) -> None:
    """持久化 bot 用户名，以便读取历史数据时可以剔除。"""
    if not name:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BOT_NAME_FILE.write_text(name.strip(), encoding="utf-8")


def get_bot_username() -> str:
    """读取持久化的 bot 用户名。"""
    if BOT_NAME_FILE.exists():
        try:
            return BOT_NAME_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return ""


# ===== JSONL 快照存储 =====

def record_snapshot(
    players: list[dict[str, str]],
    timestamp_iso: str,
    *,
    bot_username: str = "",
) -> None:
    """追加一条玩家快照到 JSONL 文件。"""
    # 持久化 bot 用户名以便后续过滤历史数据
    if bot_username:
        _save_bot_username(bot_username)

    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(timestamp_iso.replace("Z", "+00:00"))
        ts = int(dt.timestamp())
    except Exception:
        ts = int(time.time())

    # 从玩家列表中剔除 bot 自身
    bot_name = bot_username or get_bot_username()
    bot_name_lower = bot_name.lower()
    filtered_players = [
        p for p in players
        if p.get("name") and p["name"].lower() != bot_name_lower
    ] if bot_name else [p for p in players if p.get("name")]

    names = [p["name"] for p in filtered_players]
    record = {"t": ts, "p": names}

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with TRACKING_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # 更新元数据（也排除 bot）
    update_player_meta(filtered_players)

    # 清理旧数据（仅在首条记录过期时才执行重写）
    cleanup_old_records()


def load_records(
    since_ts: int,
    until_ts: int | None = None,
) -> list[dict[str, Any]]:
    """读取指定时间范围 [since_ts, until_ts] 内的快照记录。

    自动从每条记录的玩家列表中剔除 bot 自身，
    确保历史数据中即使包含 bot 也不会出现在结果中。
    """
    if until_ts is None:
        until_ts = int(time.time())

    records: list[dict[str, Any]] = []
    if not TRACKING_FILE.exists():
        return records

    # 读取持久化的 bot 用户名用于过滤
    bot_name = get_bot_username()
    bot_name_lower = bot_name.lower() if bot_name else ""

    with TRACKING_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
                t = record.get("t", 0)
                if since_ts <= t <= until_ts:
                    # 从玩家列表中剔除 bot
                    if bot_name_lower and "p" in record:
                        record["p"] = [
                            name for name in record["p"]
                            if name.lower() != bot_name_lower
                        ]
                    records.append(record)
            except (json.JSONDecodeError, TypeError):
                continue

    return records


def cleanup_old_records() -> None:
    """删除 12 个月前的记录（重写 JSONL 文件）。"""
    if not TRACKING_FILE.exists():
        return

    cutoff = int(time.time()) - TWELVE_MONTHS_SECONDS

    # 快速检查：如果第一条记录就在截止之后，无需重写
    with TRACKING_FILE.open("r", encoding="utf-8") as f:
        first_line = f.readline().strip()
        if not first_line:
            return
        try:
            first_record = json.loads(first_line)
            if first_record.get("t", 0) >= cutoff:
                return  # 无过期记录
        except (json.JSONDecodeError, TypeError):
            pass

    # 需要重写
    kept_lines: list[str] = []
    with TRACKING_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
                if record.get("t", 0) >= cutoff:
                    kept_lines.append(stripped)
            except (json.JSONDecodeError, TypeError):
                continue

    with TRACKING_FILE.open("w", encoding="utf-8") as f:
        for kept_line in kept_lines:
            f.write(kept_line + "\n")

    logger.info(f"已清理过期玩家追踪记录，保留 {len(kept_lines)} 条")


# ===== 玩家元数据（UUID + 皮肤 URL）=====

def load_player_meta() -> dict[str, dict[str, str]]:
    """加载玩家元数据映射。"""
    if not META_FILE.exists():
        return {}
    try:
        with META_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def update_player_meta(players: list[dict[str, str]]) -> None:
    """更新/新增玩家的 UUID 和皮肤 URL。"""
    meta = load_player_meta()
    changed = False

    for p in players:
        name = p.get("name", "")
        uuid = p.get("uuid", "")
        skin_url = p.get("skin_url", "")
        if not name:
            continue

        existing = meta.get(name, {})
        if existing.get("uuid") != uuid or existing.get("skin_url") != skin_url:
            meta[name] = {"uuid": uuid, "skin_url": skin_url}
            changed = True

    if changed:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with META_FILE.open("w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)


# ===== 玩家头像下载与缓存 =====

async def get_player_head(
    player_name: str,
    meta: dict[str, dict[str, str]] | None = None,
) -> bytes | None:
    """获取玩家头像 PNG 字节数据（优先使用缓存）。"""
    if meta is None:
        meta = load_player_meta()

    player_info = meta.get(player_name, {})
    uuid = player_info.get("uuid", "")
    skin_url = player_info.get("skin_url", "")

    if not uuid:
        return None

    HEADS_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = HEADS_DIR / f"{uuid}.png"

    # 缓存命中
    if cache_file.exists():
        return cache_file.read_bytes()

    # 下载并处理
    if not skin_url:
        skin_url = f"https://crafatar.com/avatars/{uuid}?size=32&overlay"

    head_bytes = await _download_and_process_head(skin_url, cache_file)
    return head_bytes


async def _download_and_process_head(
    url: str,
    cache_path: Path,
) -> bytes | None:
    """下载皮肤图片，裁剪头部并缓存。"""
    try:
        raw_data = await asyncio.to_thread(_http_get, url)
        if raw_data is None:
            return None

        head_png = await asyncio.to_thread(_crop_head, raw_data)
        if head_png is None:
            return None

        cache_path.write_bytes(head_png)
        return head_png

    except Exception as e:
        logger.warning(f"下载/处理玩家头像失败: {e}")
        return None


def _http_get(url: str) -> bytes | None:
    """同步 HTTP GET（在线程中调用）。"""
    import urllib.request

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            return resp.read()
    except Exception as e:
        logger.warning(f"HTTP 请求失败 {url}: {e}")
        return None


def _crop_head(raw_data: bytes) -> bytes | None:
    """从皮肤纹理中裁剪头像，或直接缩放已有头像。"""
    from PIL import Image

    img = Image.open(io.BytesIO(raw_data)).convert("RGBA")

    if img.width >= 64:
        # 完整皮肤纹理：基础脸 (8,8)-(16,16) + 帽子层 (40,8)-(48,16)
        face = img.crop((8, 8, 16, 16))
        try:
            hat = img.crop((40, 8, 48, 16))
            face = Image.alpha_composite(face, hat)
        except Exception:
            pass
        head = face.resize((32, 32), Image.Resampling.NEAREST)
    else:
        # 已经是头像图片，直接缩放
        head = img.resize((32, 32), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    head.save(buf, format="PNG")
    return buf.getvalue()


def generate_placeholder_head(player_name: str) -> bytes:
    """生成占位头像（纯色方块，颜色由名字哈希决定）。"""
    from PIL import Image

    h = int(hashlib.md5(player_name.encode()).hexdigest()[:6], 16)  # noqa: S324
    r = (h >> 16) & 0xFF
    g = (h >> 8) & 0xFF
    b = h & 0xFF
    # 确保颜色不太暗
    r = max(r, 80)
    g = max(g, 80)
    b = max(b, 80)

    img = Image.new("RGBA", (32, 32), (r, g, b, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
