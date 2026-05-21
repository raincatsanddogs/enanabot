from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from nonebot.log import logger

_translations: dict[str, str] = {}

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
    inner_data = message.get("data")
    if not isinstance(inner_data, dict):
        return None

    translate_keys = inner_data.get("translate")
    if not isinstance(translate_keys, list) or not translate_keys:
        return None

    # 判断是否为进度（advancement）相关的系统消息
    template_key = next((k for k in translate_keys if k.startswith("chat.type.advancement.")), None)
    if template_key:
        # 筛选出进度标题键（如 advancements.adventure.honey_block_slide.title）
        title_key = next((k for k in translate_keys if k.startswith("advancements.") and k.endswith(".title")), None)
        if not title_key:
            return None

        # 获取括号包裹模板，如 chat.square_brackets
        bracket_key = next((k for k in translate_keys if k.startswith("chat.") and "bracket" in k), "chat.square_brackets")

        # 翻译各部分组件
        template = get_translation(template_key)
        bracket = get_translation(bracket_key)
        title = get_translation(title_key)

        # 拼接进度名称，带上括号，如 "[胶着状态]"
        formatted_title = format_minecraft_template(bracket, title)

        # 获取玩家名称
        player_name = message.get("player", {}).get("username") or "玩家"

        # 格式化最终消息
        return format_minecraft_template(template, player_name, formatted_title)

    return None
