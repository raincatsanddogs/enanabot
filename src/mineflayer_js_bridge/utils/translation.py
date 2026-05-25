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
        "advancements.toast.task": "进度已达成！",
        "advancements.toast.challenge": "挑战已完成！",
        "advancements.toast.goal": "目标已达成！",
    }

    return _translations.get(key) or fallback_templates.get(key, key)


def _get_translate_keys(message: dict[str, Any]) -> list[str] | None:
    inner_data = message.get("data")
    if not isinstance(inner_data, dict):
        return None

    translate_keys = inner_data.get("translate")

    # 回退：部分消息将 translate 放在 extra 中（如 multiplayer.player.joined）
    if not isinstance(translate_keys, list) or not translate_keys:
        extra = message.get("extra")
        if isinstance(extra, dict):
            translate_keys = extra.get("translate")

    if not isinstance(translate_keys, list) or not translate_keys:
        return None

    return [key for key in translate_keys if isinstance(key, str)]


def _is_nonempty_name(value: Any) -> bool:
    """判断一个候选名称值是否为有效的非空值（过滤空字典、空列表等）。"""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        # 空字典 {} 或仅含空 text 的字典视为无效
        return bool(value) and bool((value.get("text") or "").strip())
    if isinstance(value, list):
        return bool(value)
    return False


def get_player_name_by_config(player_data: dict[str, Any]) -> str:
    """
    根据配置项 mineflayer_ws_player_info_type 提取并返回玩家名字
    """
    from ..context import config

    info_type = getattr(config, "mineflayer_ws_player_info_type", "nickname").lower()

    if info_type == "id":
        candidates = ["username", "player_name", "nickname", "displayName"]
    else:  # "nickname"
        candidates = ["nickname", "displayName", "username", "player_name"]

    name: Any = None
    for key in candidates:
        val = player_data.get(key)
        if _is_nonempty_name(val):
            name = val
            break

    if isinstance(name, list):
        parts = []
        for node in name:
            if isinstance(node, dict):
                parts.append(node.get("text") or node.get("") or "")
            elif isinstance(node, str):
                parts.append(node)
        name = "".join(parts).strip()
    elif isinstance(name, dict):
        name = (name.get("text") or "").strip()

    return name if isinstance(name, str) and name else "玩家"


def _get_player_name(message: dict[str, Any]) -> str:
    data = message.get("data")
    player = message.get("player")
    if not player and isinstance(data, dict):
        player = data.get("player")

    if isinstance(player, list) and player:
        player = player[0]

    if isinstance(player, dict):
        return get_player_name_by_config(player)
    if isinstance(player, str) and player:
        return player
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

    # 1. 判断是否为进度（advancement）相关的系统消息
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

    # 2. 判断是否为其他 system_info 系统消息（如 join, left, death 等）
    inner_data = message.get("data", {})
    position = inner_data.get("position")
    if isinstance(position, str) and position == "system_info":
        sys_template_key = next(
            (
                k for k in translate_keys
                if (
                    k.startswith("multiplayer.player.")
                    or k.startswith("death.")
                    or k.startswith("chat.type.")
                    or k.startswith("commands.")
                    or k.startswith("gameMode.")
                )
            ),
            None,
        )
        if not sys_template_key and translate_keys:
            sys_template_key = translate_keys[0]

        if sys_template_key:
            template = get_translation(sys_template_key)
            params = inner_data.get("params", [])
            if not isinstance(params, list):
                params = []

            formatted_args = []
            for p in params:
                if isinstance(p, dict):
                    name = p.get("name") or p.get("text")
                    if isinstance(name, str) and name:
                        # 嵌套翻译参数，如 entity.minecraft.zombie 等
                        formatted_args.append(get_translation(name))
                    else:
                        formatted_args.append(str(name or p))
                else:
                    formatted_args.append(str(p))

            # 兜底：如果无参数，尝试从结构化数据或翻译键列表中提取参数
            if not formatted_args:
                # 1. 优先尝试从结构化的 player/entity/item 数据中解析参数
                player_data = message.get("player") or inner_data.get("player")
                player_name = _get_player_name(message) if player_data is not None else None

                # 解析 entity (击杀者)
                entity_list = inner_data.get("entity", [])
                entity_name = None
                if isinstance(entity_list, list) and entity_list:
                    entity_data = entity_list[0]
                    if isinstance(entity_data, dict):
                        raw_name = entity_data.get("name")
                        if isinstance(raw_name, dict) and "translate" in raw_name:
                            entity_name = get_translation(raw_name["translate"])
                        elif isinstance(raw_name, str) and raw_name.strip():
                            entity_name = raw_name.strip()
                        else:
                            entity_id = entity_data.get("id")
                            if isinstance(entity_id, str) and entity_id.strip():
                                entity_key = f"entity.{entity_id.replace(':', '.')}"
                                entity_name = get_translation(entity_key)

                # 解析 item (武器/道具)
                item_list = inner_data.get("item", [])
                item_name = None
                if isinstance(item_list, list) and item_list:
                    item_data = item_list[0]
                    if isinstance(item_data, dict):
                        raw_name = item_data.get("display_name") or item_data.get("name")
                        if isinstance(raw_name, dict) and "translate" in raw_name:
                            item_name = get_translation(raw_name["translate"])
                        elif isinstance(raw_name, str) and raw_name.strip():
                            item_name = raw_name.strip()
                        else:
                            item_id = item_data.get("id")
                            if isinstance(item_id, str) and item_id.strip():
                                item_key = f"item.{item_id.replace(':', '.')}"
                                item_name = get_translation(item_key)
                                if item_name == item_key:
                                    block_key = f"block.{item_id.replace(':', '.')}"
                                    item_name = get_translation(block_key)
                if item_name and "chat.square_brackets" in translate_keys:
                    bracket_template = get_translation("chat.square_brackets")
                    item_name = format_minecraft_template(bracket_template, item_name)

                # 组合解析出的结构化参数
                extracted_args = []
                if player_name:
                    extracted_args.append(player_name)
                if entity_name:
                    extracted_args.append(entity_name)
                if item_name:
                    extracted_args.append(item_name)

                # 2. 如果未能从结构化字段中解析出非玩家参数（长度 <= 1），则使用原有 translate_keys 的翻译键提取逻辑
                if len(extracted_args) <= 1:
                    remaining_keys = [k for k in translate_keys if k not in {sys_template_key, "chat.square_brackets"}]
                    translated_params = [get_translation(k) for k in remaining_keys]
                    if player_name:
                        formatted_args = [player_name] + translated_params
                    else:
                        formatted_args = translated_params
                else:
                    formatted_args = extracted_args

                # 如果仍然为空且模板包含占位符，则将玩家名称作为参数放入
                if not formatted_args and ("%s" in template or "%1$s" in template):
                    player_name = _get_player_name(message)
                    if player_name != "玩家":
                        formatted_args.append(f"{player_name} ")

            # 给各个名字参数前后加上空格，格式化模板，并合并多余空格
            spaced_args = [f" {arg} " if arg else "" for arg in formatted_args]
            raw_result = format_minecraft_template(template, *spaced_args)
            return re.sub(r'\s+', ' ', raw_result).strip()

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

    # 获取原版 Toast 标题（例如：“进度已达成！”、“挑战已完成！”、“目标已达成！”）
    toast_key = f"advancements.toast.{advancement.advancement_type}"
    toast_title = get_translation(toast_key).replace("！", "!")

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(
            endpoint,
            params={
                "background": background,
                "title": toast_title,
                "text": advancement.title,
            },
        )
    response.raise_for_status()
    image_data = response.content
    if not image_data:
        msg = "mcgen 返回空图片"
        raise ValueError(msg)
    return image_data
