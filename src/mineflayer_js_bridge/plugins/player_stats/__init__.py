"""在线玩家统计插件。

指令格式: list [-n|-g] [-{数字}{d|h|m}]
  -n  在线人数折线图（默认）
  -g  在线玩家甘特图
  -3d12h  组合时间范围

示例:
  @bot list           → 默认折线图，最近 24 小时
  @bot list -g -3d    → 甘特图，最近 3 天
  @bot list -n -12h   → 折线图，最近 12 小时
"""

from __future__ import annotations

import io
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import matplotlib
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.offsetbox import AnnotationBbox, OffsetImage
from nonebot import logger, on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.rule import to_me
from PIL import Image

try:
    from src.utils.command_reaction import (
        EMOJI_STATUS_FAILED,
        EMOJI_STATUS_PROCESSING,
        EMOJI_STATUS_SUCCESS,
        set_status_emoji,
    )
except ModuleNotFoundError:
    from utils.command_reaction import (
        EMOJI_STATUS_FAILED,
        EMOJI_STATUS_PROCESSING,
        EMOJI_STATUS_SUCCESS,
        set_status_emoji,
    )

from ...player_tracker import (
    generate_placeholder_head,
    get_player_head,
    load_player_meta,
    load_records,
)

matplotlib.use("Agg")

__plugin_meta__ = PluginMetadata(
    name="player-stats",
    description="在线玩家统计（折线图/甘特图）",
    usage="list [-n|-g] [-3d12h30m]",
)

# ===== 图表风格配置 =====
BG_COLOR = "#0f0f1a"
CARD_COLOR = "#1a1a2e"
TEXT_COLOR = "#e0e0e0"
GRID_COLOR = "#2a2a3e"
ACCENT_COLOR = "#00d2ff"
ACCENT_GRADIENT_TOP = "#00d2ff"
ACCENT_GRADIENT_BOT = "#0f0f1a"

GANTT_COLORS = [
    "#00d2ff",  # cyan
    "#7b68ee",  # 中板蓝
    "#ff6b6b",  # 珊瑚红
    "#51cf66",  # 绿
    "#ffd43b",  # 黄
    "#ff922b",  # 橙
    "#cc5de8",  # 紫
    "#20c997",  # 蓝绿
    "#ff8787",  # 浅红
    "#748ffc",  # 靛蓝
]

# list 图表统一使用 UTC+8 显示时间
DISPLAY_TIMEZONE = timezone(timedelta(hours=8))

# 常见中文字体候选（跨平台）
PREFERRED_CJK_FONTS = [
    "Microsoft YaHei",
    "SimHei",
    "PingFang SC",
    "Hiragino Sans GB",
    "Noto Sans CJK SC",
    "Noto Sans CJK JP",
    "Noto Sans CJK TC",
    "WenQuanYi Zen Hei",
    "WenQuanYi Micro Hei",
    "Source Han Sans SC",
    "Arial Unicode MS",
]

LINUX_CJK_FONT_PATHS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]

CJK_FONT_KEYWORDS = [
    "Noto Sans CJK",
    "WenQuanYi",
    "Source Han Sans",
    "Droid Sans Fallback",
    "AR PL",
]


def _register_linux_cjk_fonts() -> list[str]:
    """注册 Linux 常见中文字体文件，返回注册到的字体名。"""
    loaded_names: list[str] = []
    if os.name == "nt":
        return loaded_names

    for font_path in LINUX_CJK_FONT_PATHS:
        if not Path(font_path).exists():
            continue
        try:
            font_manager.fontManager.addfont(font_path)
            font_name = font_manager.FontProperties(fname=font_path).get_name()
            if font_name:
                loaded_names.append(font_name)
        except Exception as error:
            logger.debug(f"注册 Linux 字体失败 {font_path}: {error}")

    return loaded_names


def _configure_matplotlib_fonts() -> bool:
    """配置 matplotlib 字体，返回是否可用中文字体。"""
    reload_func = getattr(font_manager, "_load_fontmanager", None)
    if callable(reload_func):
        try:
            reload_func(try_read_cache=False)
        except Exception as error:
            logger.debug(f"刷新 matplotlib 字体缓存失败: {error}")

    loaded_names = _register_linux_cjk_fonts()
    available = {font.name for font in font_manager.fontManager.ttflist}

    if loaded_names:
        available.update(loaded_names)
        logger.info(f"player_stats 已注册 Linux 字体: {', '.join(loaded_names)}")

    matched = [name for name in PREFERRED_CJK_FONTS if name in available]

    if not matched:
        matched = sorted(
            [
                name
                for name in available
                if any(keyword in name for keyword in CJK_FONT_KEYWORDS)
            ],
        )

    if matched:
        plt.rcParams["font.sans-serif"] = [*matched, "DejaVu Sans", "sans-serif"]
        logger.info(f"player_stats 使用中文字体: {matched[0]}")
        return True

    # 没有可用中文字体时回退英文文案，避免中文缺字告警。
    plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "sans-serif"]
    logger.warning(
        "player_stats 未检测到可用中文字体，将使用英文图表文案。"
        "请安装 fonts-noto-cjk 或 fonts-wqy-zenhei，并清理 ~/.cache/matplotlib。",
    )
    return False


CJK_FONT_AVAILABLE = _configure_matplotlib_fonts()
plt.rcParams["axes.unicode_minus"] = False

list_cmd = on_command("list", rule=to_me(), priority=5)


def _chart_text(cn: str, en: str) -> str:
    """根据字体可用性选择中英文图表文案。"""
    return cn if CJK_FONT_AVAILABLE else en


# ===== 指令处理 =====


@list_cmd.handle()
async def handle_list(
    bot: Bot,
    event: MessageEvent,
    args: Message = CommandArg(),
) -> None:
    """处理 list 指令。"""
    message_id = getattr(event, "message_id", None)
    await set_status_emoji(bot, message_id, EMOJI_STATUS_PROCESSING)

    args_text = args.extract_plain_text().strip()
    chart_type, duration_seconds = _parse_args(args_text)

    now_ts = int(time.time())
    since_ts = now_ts - duration_seconds

    records = load_records(since_ts, now_ts)

    if not records:
        await set_status_emoji(bot, message_id, EMOJI_STATUS_SUCCESS)
        await list_cmd.finish("该时间范围内没有在线数据记录 📭")

    duration_label = _format_duration(duration_seconds)

    try:
        if chart_type == "g":
            img_bytes = await _generate_gantt_chart(records, duration_label)
        else:
            img_bytes = await _generate_line_chart(records, duration_label)
    except Exception as e:
        logger.error(f"生成图表失败: {e}")
        await set_status_emoji(bot, message_id, EMOJI_STATUS_FAILED)
        await list_cmd.finish(f"生成图表时出错：{e}")

    # 发送图片
    seg = MessageSegment.image(img_bytes)
    await list_cmd.send(seg)
    await set_status_emoji(bot, message_id, EMOJI_STATUS_SUCCESS)



# ===== 参数解析 =====


def _parse_args(text: str) -> tuple[str, int]:
    """解析指令参数，返回 (chart_type, duration_seconds)。"""
    chart_type = "n"
    duration = 24 * 3600  # 默认 24h

    parts = text.split()
    for part in parts:
        if part == "-n":
            chart_type = "n"
        elif part == "-g":
            chart_type = "g"
        elif part.startswith("-") and len(part) > 1:
            parsed = _parse_duration(part[1:])
            if parsed > 0:
                duration = parsed

    return chart_type, duration


def _parse_duration(text: str) -> int:
    """解析组合时间字符串，如 '3d12h30m' → 秒数。"""
    pattern = r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?"
    match = re.fullmatch(pattern, text)
    if not match or not any(match.groups()):
        return 0

    days = int(match.group(1) or 0)
    hours = int(match.group(2) or 0)
    minutes = int(match.group(3) or 0)

    return days * 86400 + hours * 3600 + minutes * 60


def _format_duration(seconds: int) -> str:
    """将秒数格式化为可读字符串。"""
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60

    if not CJK_FONT_AVAILABLE:
        parts_en: list[str] = []
        if days > 0:
            parts_en.append(f"{days}d")
        if hours > 0:
            parts_en.append(f"{hours}h")
        if minutes > 0:
            parts_en.append(f"{minutes}m")
        return " ".join(parts_en) if parts_en else "24h"

    parts: list[str] = []
    if days > 0:
        parts.append(f"{days} 天")
    if hours > 0:
        parts.append(f"{hours} 小时")
    if minutes > 0:
        parts.append(f"{minutes} 分钟")

    return " ".join(parts) if parts else "24 小时"


# ===== 在线会话计算 =====

MAX_GAP = 325  # 两次快照最大间隔（秒），超过则视为新会话（意义明确的间隔（）


def _compute_sessions(
    records: list[dict[str, Any]],
) -> dict[str, list[tuple[datetime, datetime]]]:
    """从快照记录中推算每个玩家的在线会话区间。"""
    if not records:
        return {}

    records_sorted = sorted(records, key=lambda r: r["t"])

    sessions: dict[str, list[tuple[datetime, datetime]]] = {}
    last_seen: dict[str, int] = {}  # 玩家 → 上次出现的时间戳

    for record in records_sorted:
        t = record["t"]
        current_players = set(record.get("p", []))

        for player in current_players:
            dt_now = datetime.fromtimestamp(t, tz=DISPLAY_TIMEZONE)

            if player in last_seen:
                gap = t - last_seen[player]
                if gap <= MAX_GAP:
                    # 延续当前会话
                    old_start, _ = sessions[player][-1]
                    sessions[player][-1] = (old_start, dt_now)
                else:
                    # 间隔过大，新会话
                    sessions.setdefault(player, []).append((dt_now, dt_now))
            else:
                # 首次出现
                sessions.setdefault(player, []).append((dt_now, dt_now))

            last_seen[player] = t

        # 标记不在当前快照中的玩家为下线
        for player in list(last_seen.keys()):
            if player not in current_players:
                del last_seen[player]

    return sessions


# ===== 折线图生成 =====


async def _generate_line_chart(
    records: list[dict[str, Any]],
    duration_label: str,
) -> bytes:
    """生成在线人数折线图（含玩家头像面板）。"""
    records_sorted = sorted(records, key=lambda r: r["t"])

    timestamps = [datetime.fromtimestamp(r["t"], tz=DISPLAY_TIMEZONE) for r in records_sorted]
    counts = [len(r.get("p", [])) for r in records_sorted]

    # 收集所有出现过的玩家
    all_players: set[str] = set()
    for r in records_sorted:
        all_players.update(r.get("p", []))
    all_players_list = sorted(all_players)

    # 预加载头像
    meta = load_player_meta()
    heads = await _load_heads_batch(all_players_list, meta)

    # --- 绘图 ---
    has_players = len(all_players_list) > 0
    # 头像面板行数
    heads_per_row = 8
    head_rows = (len(all_players_list) + heads_per_row - 1) // heads_per_row if has_players else 0
    head_panel_height = max(0.8 * head_rows, 0) if has_players else 0

    fig_height = 5 + head_panel_height + (0.6 if has_players else 0)

    if has_players:
        fig, (ax_main, ax_heads) = plt.subplots(
            2, 1,
            figsize=(10, fig_height),
            gridspec_kw={"height_ratios": [5, head_panel_height + 0.6]},
            facecolor=BG_COLOR,
        )
    else:
        fig, ax_main = plt.subplots(1, 1, figsize=(10, 5), facecolor=BG_COLOR)
        ax_heads = None

    ax_main.set_facecolor(CARD_COLOR)

    # 折线 + 渐变填充
    ax_main.plot(
        timestamps,
        counts,
        color=ACCENT_COLOR,
        linewidth=2,
        marker="o",
        markersize=3,
        zorder=5,
    )
    ax_main.fill_between(
        timestamps,
        counts,
        alpha=0.15,
        color=ACCENT_COLOR,
    )

    # 样式
    ax_main.set_title(
        _chart_text("在线人数统计（最近 {duration}）", "Online Players (last {duration})").format(
            duration=duration_label,
        ),
        color=TEXT_COLOR,
        fontsize=14,
        fontweight="bold",
        pad=12,
    )
    # 不添加 y 轴标题，图表标题已足够说明
    ax_main.tick_params(colors=TEXT_COLOR, labelsize=9)
    ax_main.grid(True, which="major", color=GRID_COLOR, alpha=0.5, linewidth=0.5)
    ax_main.grid(True, which="minor", color=GRID_COLOR, alpha=0.2, linewidth=0.3)
    ax_main.minorticks_on()
    ax_main.set_xlim(timestamps[0], timestamps[-1])

    # Y 轴整数刻度
    max_count = max(counts) if counts else 1
    ax_main.set_ylim(0, max(max_count + 1, 2))
    ax_main.yaxis.set_major_locator(plt.MaxNLocator(integer=True))

    # X 轴时间格式
    _auto_format_xaxis(ax_main, timestamps)

    for spine in ax_main.spines.values():
        spine.set_color(GRID_COLOR)

    # --- 头像面板 ---
    if has_players and ax_heads is not None:
        ax_heads.set_facecolor(BG_COLOR)
        ax_heads.set_xlim(0, 1)
        ax_heads.set_ylim(0, 1)
        ax_heads.axis("off")
        ax_heads.set_title(
            _chart_text("本时段在线过的玩家", "Players active in this period"),
            color=TEXT_COLOR,
            fontsize=10,
            loc="left",
            pad=4,
        )

        _draw_head_grid(ax_heads, all_players_list, heads, heads_per_row)

    fig.tight_layout(pad=1.5)
    fig.subplots_adjust(right=0.95)
    return _fig_to_bytes(fig)


# ===== 甘特图生成 =====


async def _generate_gantt_chart(
    records: list[dict[str, Any]],
    duration_label: str,
) -> bytes:
    """生成玩家在线时段甘特图。"""
    sessions = _compute_sessions(records)

    if not sessions:
        msg = "该时间范围内没有玩家会话数据"
        raise ValueError(msg)

    # 按总在线时长排序（最长的在上面）
    def total_online(player: str) -> float:
        return sum(
            (end - start).total_seconds()
            for start, end in sessions[player]
        )

    players_sorted = sorted(sessions.keys(), key=total_online, reverse=True)

    # 预加载头像
    meta = load_player_meta()
    heads = await _load_heads_batch(players_sorted, meta)

    n_players = len(players_sorted)
    fig_height = max(3, 0.6 * n_players + 2)

    fig, ax = plt.subplots(1, 1, figsize=(12, fig_height), facecolor=BG_COLOR)
    ax.set_facecolor(CARD_COLOR)

    # 收集需要标注的时间信息（延迟到 axes 配置后绘制）
    _time_annotations: list[tuple[float, float, int, str, str]] = []

    # 绘制每个玩家的在线条
    for i, player in enumerate(players_sorted):
        color = GANTT_COLORS[i % len(GANTT_COLORS)]
        player_sessions = sessions[player]

        for start, end in player_sessions:
            # 单点快照（仅出现一次）：扩展到 5 分钟显示宽度
            display_end = end
            if start == end:
                display_end = start + timedelta(minutes=5)

            bar_width = mdates.date2num(display_end) - mdates.date2num(start)
            ax.barh(
                i,
                bar_width,
                left=mdates.date2num(start),
                height=0.6,
                color=color,
                alpha=0.85,
                edgecolor=color,
                linewidth=0.5,
            )

            # 记录标注信息
            start_str = start.strftime("%H:%M")
            end_str = end.strftime("%H:%M")
            _time_annotations.append((
                mdates.date2num(start),
                mdates.date2num(display_end),
                i,
                start_str,
                end_str,
            ))

    # Y 轴标签（玩家名）
    ax.set_yticks(range(n_players))
    ax.set_yticklabels(
        ["" for _ in players_sorted],  # 不用 tick label，改用头像+文字
    )

    # 在 Y 轴左侧放置头像（上）+ 玩家名（下），垂直排列
    for i, player in enumerate(players_sorted):
        head_data = heads.get(player)

        if head_data is not None:
            try:
                head_img = Image.open(io.BytesIO(head_data)).convert("RGBA")
                head_arr = np.array(head_img)

                imagebox = OffsetImage(head_arr, zoom=0.45)
                imagebox.image.axes = ax

                ab = AnnotationBbox(
                    imagebox,
                    (0, i),
                    xybox=(-40, 8),
                    xycoords=("axes fraction", "data"),
                    boxcoords="offset points",
                    frameon=False,
                )
                ax.add_artist(ab)
            except Exception as e:
                logger.debug(f"头像渲染失败 {player}: {e}")

        # 玩家名放在头像下方
        ax.annotate(
            player,
            xy=(0, i),
            xytext=(-40, -8),
            xycoords=("axes fraction", "data"),
            textcoords="offset points",
            ha="center",
            va="top",
            fontsize=7,
            color=TEXT_COLOR,
        )

    # 样式
    ax.set_title(
        _chart_text("玩家在线时段（最近 {duration}）", "Player Sessions (last {duration})").format(
            duration=duration_label,
        ),
        color=TEXT_COLOR,
        fontsize=14,
        fontweight="bold",
        pad=12,
    )

    ax.xaxis_date()
    _auto_format_xaxis(ax, None)

    ax.tick_params(axis="x", colors=TEXT_COLOR, labelsize=9)
    ax.tick_params(axis="y", colors=TEXT_COLOR, labelsize=9)
    ax.grid(True, which="major", axis="x", color=GRID_COLOR, alpha=0.5, linewidth=0.5)
    ax.grid(True, which="minor", axis="x", color=GRID_COLOR, alpha=0.2, linewidth=0.3)
    ax.minorticks_on()
    ax.tick_params(axis="y", which="minor", left=False)
    ax.set_axisbelow(True)
    ax.invert_yaxis()

    for spine in ax.spines.values():
        spine.set_color(GRID_COLOR)

    # 绘制延迟的时间标注（此时 axes 变换已就绪）
    fig.canvas.draw()  # 确保 transData 准确
    for bar_start, bar_end, y_idx, s_str, e_str in _time_annotations:
        px_start = ax.transData.transform((bar_start, 0))[0]
        px_end = ax.transData.transform((bar_end, 0))[0]
        bar_px_w = px_end - px_start

        if bar_px_w > 100:
            # 条足够宽：时间标在条内两端
            ax.text(
                bar_start, y_idx, f" {s_str}",
                va="center", ha="left",
                fontsize=6, color="#ffffff", fontweight="bold", zorder=10,
            )
            ax.text(
                bar_end, y_idx, f"{e_str} ",
                va="center", ha="right",
                fontsize=6, color="#ffffff", fontweight="bold", zorder=10,
            )
        else:
            # 条较窄：时间标在条外两侧
            ax.text(
                bar_start, y_idx, f"{s_str} ",
                va="center", ha="right",
                fontsize=6, color=TEXT_COLOR, zorder=10,
            )
            ax.text(
                bar_end, y_idx, f" {e_str}",
                va="center", ha="left",
                fontsize=6, color=TEXT_COLOR, zorder=10,
            )

    # 增加左侧边距以容纳头像+玩家名
    fig.tight_layout(pad=1.5)
    fig.subplots_adjust(left=0.12)
    return _fig_to_bytes(fig)


# ===== 辅助函数 =====


async def _load_heads_batch(
    player_names: list[str] | set[str],
    meta: dict[str, dict[str, str]],
) -> dict[str, bytes]:
    """批量加载/下载玩家头像。"""
    result: dict[str, bytes] = {}
    tasks = {}

    for name in player_names:
        tasks[name] = get_player_head(name, meta)

    for name, coro in tasks.items():
        try:
            head_data = await coro
            if head_data:
                result[name] = head_data
            else:
                result[name] = generate_placeholder_head(name)
        except Exception:
            result[name] = generate_placeholder_head(name)

    return result


def _draw_head_grid(
    ax: plt.Axes,
    players: list[str],
    heads: dict[str, bytes],
    per_row: int = 8,
) -> None:
    """在 axes 上绘制玩家头像网格。"""
    n = len(players)
    rows = (n + per_row - 1) // per_row

    for idx, player in enumerate(players):
        row = idx // per_row
        col = idx % per_row

        x = (col + 0.5) / per_row
        y = 1.0 - (row + 0.5) / max(rows, 1)

        head_data = heads.get(player)
        if head_data:
            try:
                head_img = Image.open(io.BytesIO(head_data)).convert("RGBA")
                head_arr = np.array(head_img)
                imagebox = OffsetImage(head_arr, zoom=0.45)
                ab = AnnotationBbox(
                    imagebox,
                    (x, y + 0.05),
                    frameon=False,
                    xycoords="axes fraction",
                )
                ax.add_artist(ab)
            except Exception:
                pass

        # 名字标签
        ax.text(
            x,
            y - 0.15,
            player,
            ha="center",
            va="top",
            fontsize=7,
            color=TEXT_COLOR,
            transform=ax.transAxes,
        )


def _auto_format_xaxis(
    ax: plt.Axes,
    timestamps: list[datetime] | None,
) -> None:
    """根据时间跨度自动选择 X 轴格式，含主刻度和细刻度。"""
    if timestamps and len(timestamps) >= 2:
        span = (timestamps[-1] - timestamps[0]).total_seconds()
    else:
        span = 86400  # 默认 1 天

    if span <= 3600 * 3:
        # ≤3h：主刻度 15min，细刻度 5min
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=DISPLAY_TIMEZONE))
        ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=15, tz=DISPLAY_TIMEZONE))
        ax.xaxis.set_minor_locator(mdates.MinuteLocator(interval=5, tz=DISPLAY_TIMEZONE))
    elif span <= 3600 * 12:
        # ≤12h：主刻度 1h，细刻度 15min
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=DISPLAY_TIMEZONE))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=1, tz=DISPLAY_TIMEZONE))
        ax.xaxis.set_minor_locator(mdates.MinuteLocator(interval=15, tz=DISPLAY_TIMEZONE))
    elif span <= 86400:
        # ≤24h：主刻度 1h，细刻度 30min
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=DISPLAY_TIMEZONE))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=1, tz=DISPLAY_TIMEZONE))
        ax.xaxis.set_minor_locator(mdates.MinuteLocator(interval=30, tz=DISPLAY_TIMEZONE))
    elif span <= 86400 * 3:
        # ≤3d：主刻度 6h，细刻度 1h
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M", tz=DISPLAY_TIMEZONE))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=6, tz=DISPLAY_TIMEZONE))
        ax.xaxis.set_minor_locator(mdates.HourLocator(interval=1, tz=DISPLAY_TIMEZONE))
    elif span <= 86400 * 7:
        # ≤7d：主刻度 12h，细刻度 3h
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M", tz=DISPLAY_TIMEZONE))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=12, tz=DISPLAY_TIMEZONE))
        ax.xaxis.set_minor_locator(mdates.HourLocator(interval=3, tz=DISPLAY_TIMEZONE))
    else:
        # >7d：主刻度 1d，细刻度 6h
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d", tz=DISPLAY_TIMEZONE))
        ax.xaxis.set_major_locator(mdates.DayLocator(tz=DISPLAY_TIMEZONE))
        ax.xaxis.set_minor_locator(mdates.HourLocator(interval=6, tz=DISPLAY_TIMEZONE))

    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")


def _fig_to_bytes(fig: plt.Figure) -> bytes:
    """将 matplotlib Figure 导出为 PNG 字节。"""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()
