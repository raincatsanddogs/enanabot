from __future__ import annotations

from dataclasses import dataclass
import json
import re
from pathlib import Path
from typing import Any, Literal

from nonebot.log import logger

_translations: dict[str, str] = {}
AdvancementType = Literal["task", "challenge", "goal"]
_advancement_backgrounds: dict[AdvancementType, str] = {
    "task": "grass",
    "challenge": "sword_diamond",
    "goal": "gold",
}


@dataclass(frozen=True)
class AdvancementMessage:
    """已翻译的 Minecraft 进度消息，用于 mcgen 图片渲染和文本回退。"""

    player_name: str
    advancement_type: AdvancementType
    title: str
    description: str
    fallback_text: str


def get_translation(key: str) -> str:
    """
    获取 Minecraft 翻译键对应的中文翻译，如果不存在则尝试回退或返回键名本身
    """
    global _translations
    if not _translations:
        # 尝试加载 configs/zh_cn.json
        try:
            # 优先从 workspace 根目录查找
            lang_path = Path("configs/zh_cn.json")
            if not lang_path.exists():
                # 备用：从当前文件所在位置向上查找
                lang_path = Path(__file__).parents[3] / "configs" / "zh_cn.json"
                
            if lang_path.exists():
                _translations = json.loads(lang_path.read_text(encoding="utf-8"))
                logger.info(f"成功加载本地语言包: {lang_path.resolve()}")
            else:
                logger.warning("未找到 configs/zh_cn.json，将使用默认回退翻译。")
        except Exception as e:
            logger.error(f"加载语言文件失败: {e}")

    # 常用进度的默认回退翻译，防止没有语言包时显示原始键名
    fallback_templates = {
        "chat.type.advancement.task": "%s取得了进度%s",
        "chat.type.advancement.challenge": "%s完成了挑战%s",
        "chat.type.advancement.goal": "%s达成了目标%s",
        "chat.square_brackets": "[%s]",
    }

    return _translations.get(key) or fallback_templates.get(key, key)


def _get_translate_keys(message: dict[str, Any]) -> list[str] | None:
    inner_data = message.get("data")
    if not isinstance(inner_data, dict):
        return None

    translate_keys = inner_data.get("translate")
    if not isinstance(translate_keys, list) or not translate_keys:
        return None

    return [key for key in translate_keys if isinstance(key, str)]


def _get_player_name(message: dict[str, Any]) -> str:
    player = message.get("player")
    if isinstance(player, dict):
        username = player.get("username")
        if isinstance(username, str) and username:
            return username
    return "玩家"


def _get_advancement_type(template_key: str) -> AdvancementType | None:
    advancement_type = template_key.rsplit(".", 1)[-1]
    if advancement_type in _advancement_backgrounds:
        return advancement_type
    return None


def _build_advancement_fallback_text(
    message: dict[str, Any],
    translate_keys: list[str],
    template_key: str,
    title: str,
) -> str:
    bracket_key = next(
        (key for key in translate_keys if key.startswith("chat.") and "bracket" in key),
        "chat.square_brackets",
    )
    template = get_translation(template_key)
    bracket = get_translation(bracket_key)
    formatted_title = format_minecraft_template(bracket, title)
    return format_minecraft_template(template, _get_player_name(message), formatted_title)


def format_minecraft_template(template: str, *args: Any) -> str:
    """
    格式化 Minecraft 的翻译模板，兼容 %s 和 %1$s 等定位占位符
    """
    # 处理带索引的占位符，如 %1$s, %2$s
    def replace_indexed(match: re.Match[str]) -> str:
        idx = int(match.group(1)) - 1
        if 0 <= idx < len(args):
            return str(args[idx])
        return match.group(0)

    # 替换形如 %1$s 或 %1$d 这样的模式
    formatted = re.sub(r'%(\d+)\$([a-zA-Z])', replace_indexed, template)

    # 替换普通的 %s 占位符
    parts = formatted.split('%s')
    res = []
    arg_idx = 0
    for i, part in enumerate(parts):
        res.append(part)
        if i < len(parts) - 1:
            if arg_idx < len(args):
                res.append(str(args[arg_idx]))
                arg_idx += 1
            else:
                res.append('%s')
    return "".join(res)


def try_translate_message(message: dict[str, Any]) -> str | None:
    """
    尝试解析带有 translate 的多语言消息数据并进行翻译
    """
    translate_keys = _get_translate_keys(message)
    if translate_keys is None:
        return None

    # 判断是否为进度（advancement）相关的系统消息
    template_key = next((k for k in translate_keys if k.startswith("chat.type.advancement.")), None)
    if template_key:
        # 筛选出进度标题键（如 advancements.adventure.honey_block_slide.title）
        title_key = next((k for k in translate_keys if k.startswith("advancements.") and k.endswith(".title")), None)
        if not title_key:
            return None

        # 翻译各部分组件
        title = get_translation(title_key)

        # 格式化最终消息
        return _build_advancement_fallback_text(
            message,
            translate_keys,
            template_key,
            title,
        )

    return None


def try_parse_advancement_message(
    message: dict[str, Any],
) -> AdvancementMessage | None:
    """解析进度消息，返回 mcgen 图片渲染所需的标题与描述。"""
    translate_keys = _get_translate_keys(message)
    if translate_keys is None:
        return None

    template_key = next(
        (key for key in translate_keys if key.startswith("chat.type.advancement.")),
        None,
    )
    if template_key is None:
        return None

    advancement_type = _get_advancement_type(template_key)
    if advancement_type is None:
        return None

    title_key = next(
        (
            key
            for key in translate_keys
            if key.startswith("advancements.") and key.endswith(".title")
        ),
        None,
    )
    if title_key is None:
        return None

    description_key = f"{title_key.removesuffix('.title')}.description"
    title = get_translation(title_key)
    description = get_translation(description_key)
    if not title.strip() or title == title_key:
        return None
    # 图片正文必须是描述；缺描述时交给原文本回退，避免把语言键发进图片。
    if not description.strip() or description == description_key:
        return None

    return AdvancementMessage(
        player_name=_get_player_name(message),
        advancement_type=advancement_type,
        title=title,
        description=description,
        fallback_text=_build_advancement_fallback_text(
            message,
            translate_keys,
            template_key,
            title,
        ),
    )


async def fetch_achievement_image(
    api_url: str,
    advancement: AdvancementMessage,
    timeout: float = 5.0,
) -> bytes:
    """从 mcgen 拉取进度图片，调用方负责捕获异常并回退文本。"""
    import httpx

    endpoint = f"{api_url.rstrip('/')}/api/v1/achievement"
    background = _advancement_backgrounds[advancement.advancement_type]
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(
            endpoint,
            params={
                "background": background,
                "title": advancement.title,
                "text": advancement.description,
            },
        )
    response.raise_for_status()
    image_data = response.content
    if not image_data:
        msg = "mcgen 返回空图片"
        raise ValueError(msg)
    return image_data
