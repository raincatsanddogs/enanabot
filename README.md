# EnanaBot (NoneBot & Mineflayer WebSocket Bridge)

EnanaBot 是一个基于 NoneBot2 的 QQ 与 Minecraft 互通机器人。Mineflayer 逻辑已独立到外部服务，本项目只作为 WebSocket 客户端连接该服务，并负责 OneBot 侧命令、消息转发、权限管理和在线玩家统计。

## 核心特性

- **WebSocket 桥接**：按外部 Mineflayer 服务的 WebSocket 协议完成 `auth`、`login_preset`、消息转发和命令委托。
- **QQ ↔ MC 互通**：将绑定群聊/私聊中的普通消息转发到 Minecraft，并把服务端推送的 MC 消息发回 QQ。
- **TPA/Home 委托**：QQ 侧 `/tpa`、`/home` 命令通过 WebSocket `command` 交由外部 Mineflayer 服务执行。
- **在线玩家统计**：定时请求 `player` 接口并生成 `/list` 在线趋势图。
- **权限管理与热更新**：保留 `/perm` 和 `/git pull` 等 Python 侧管理能力。

## 架构说明

本项目采用 **Python NoneBot 主进程 + 外部 Mineflayer WebSocket 服务** 结构。

- Python 端插件位于 `src/mineflayer_js_bridge`，负责 WebSocket 客户端连接和 OneBot 消息处理。
- 外部 Mineflayer 服务作为 WebSocket 服务端，保存账号、服务器、TPA/Home 等 Minecraft 侧实现。
- Python 端不再内嵌 Mineflayer 项目，也不再读取旧 YAML 账号/服务器配置或 MC 语言包。

## 安装

需求版本：Python 3.10+

```bash
pip install -r requirements.txt
```

启动前请先启动外部 Mineflayer WebSocket 服务，并确保服务端配置了可用的账号/服务器预设。

## 配置

NoneBot 与 OneBot 连接仍通过根目录 `.env`、`.env.dev` 或 `.env.prod` 配置。

Mineflayer WebSocket 桥接配置也从 `.env` 读取，常用字段如下：

```env
MINEFLAYER_WS_HOST=localhost
MINEFLAYER_WS_PORT=3001
MINEFLAYER_WS_TOKEN=change-me
MINEFLAYER_WS_ACCOUNT_PRESET=1
MINEFLAYER_WS_SERVER_PRESET=1
MINEFLAYER_WS_REQUEST_TIMEOUT=10
MINEFLAYER_WS_PLAYER_POLL_INTERVAL=300
MINEFLAYER_WS_FORWARD_PREFIX=[群聊]>>
MINEFLAYER_WS_MC_PREFIX=[插件服]>>
MINEFLAYER_ENABLE_MCGEN=true
MINEFLAYER_MCGEN_API_URL=https://mcgen.menzerath.eu
MINEFLAYER_WS_PLAYER_INFO_TYPE=nickname
```

`MINEFLAYER_WS_PLAYER_INFO_TYPE` 字段用于设定群聊转发消息时，玩家名称的展示类型。可选值为 `nickname`（使用昵称，不存在则回退为游戏 ID）或 `id`（使用游戏 ID，不存在则回退为昵称），默认值为 `nickname`。若消息包含玩家信息，转发至群聊时会在文本前自动拼接 `<玩家名称> ` 标识。

`MINEFLAYER_ENABLE_MCGEN` 默认开启。收到 Minecraft 进度/挑战/目标消息时，机器人会调用
`MINEFLAYER_MCGEN_API_URL` 对应的 mcgen 服务渲染图片：图片标题使用翻译后的进度标题，正文使用翻译后的进度描述。
公共服务网络抖动、限流或私有地址不可用时，会自动回退为原来的纯文本通知。

Python 运行时数据写入 `data/`，包括权限、玩家统计和桥接状态。旧 `configs/` 中的同名运行时数据会在首次读取时兼容迁移。

## 指令一览

可通过群聊 `@机器人` 或配置的命令前缀使用：

| 指令 | 权限 | 描述 |
| --- | --- | --- |
| `/mc connect [account_id] [server_id]` | Admin | 连接 WebSocket，认证并使用预设登录/恢复 MC bot。 |
| `/mc disconnect` | Admin | 断开 WebSocket 连接，不强制 bot 下线。 |
| `/mc logout` | Admin | 发送 `logout`，让当前 MC bot 下线并清除绑定。 |
| `/mc status` | Admin | 查看 WebSocket、认证、bot、轮询、转发目标和推送状态。 |
| `/mc push [on|off]` | Admin | 查询或设置当前消息向 WS server 的推送状态。 |
| `/tpa ...` | Admin | 委托外部 Mineflayer 服务执行 TPA 相关命令。 |
| `/home ...` | Admin | 委托外部 Mineflayer 服务执行 Home 相关命令。 |
| `/list [-n|-g] [-3d12h30m]` | User | 查看在线人数折线图或玩家在线时段甘特图。 |
| `/perm add|rm|list|check` | Admin/Super | 管理 Python 侧 admin 权限。 |
| `/git pull` | Super | 拉取代码并重启 NoneBot 进程。 |
| `/help` | User | 查看可用命令。 |

## WebSocket 协议

桥接层使用外部项目文档中的协议：

- 连接后第一条请求发送 `auth`。
- 认证后优先通过 `bot_list`/`bot_info` 恢复已有 bot。
- 没有可恢复 bot 时发送 `login_preset`，账号和服务器编号来自 `/mc connect` 参数或 `.env` 默认值。
- QQ 消息转发使用 `message`。
- TPA/Home 使用 `command`。
- 在线统计使用定时 `player` 请求。
- MC 消息和状态变化通过服务端推送的 `msg`、`event`、`error` 处理。

## 备注

本项目部分代码与文档由 AI 协助生成，（其实是很大部分，作者只写了最开始的的js、py代码以及之后的架构指导），可能存在不准确或不完整之处。请在使用前仔细核对相关内容，并根据实际情况进行调整和完善。使用前请结合实际部署环境核对配置和权限。
