# EnanaBot (NoneBot & Mineflayer Bridge)

EnanaBot 是一个结合了 NoneBot2 和 Node.js (Mineflayer) 的跨语言桥接机器人项目。其主要功能是实现 QQ 群聊与 Minecraft 服务器游戏内聊天的双向互通，并提供如 TPA 自动接管、自动化家（Home）传送、权限管理及热重载等（并非）强大功能。

## 🎯 核心特性

- **双向互通**: 基于 OneBot v11 与 Mineflayer 的 IPC 进程通信，QQ 群与游戏内私聊/公屏消息可无缝互转。
- **独立 JS 原生指令系统**: 为了追求极速的内部响应（如拦截 TPA 请求），在 JS 桥接端特供了一套无 IPC 通信开销的轻量化 NoneBot 风格原生指令架构。
- **智能化 TPA 自动化管理**: 包含高并发下防死锁的安全排队锁系统，支持收到 TPA 请求时自动传送至 Backup 坐标并自动返回。
- **内置权限控制 (`perm`)**: 提供粒度化的权限等级（Superuser, Admin, User）限制危险命令的滥用。
- **代码热重载 (`git pull`)**: 支持在直接在 QQ 或游戏中输入命令由机器人自动触发代码拉取和进程软重启，随时保持项目最新。

## ⚙️ 架构说明

本项目采用 **Python 主进程 + Node.js 子进程** 结构。
- Python 端 (`src/mineflayer_js_bridge`) 利用 `asyncio.subprocess` 启动并托管 Node.js 服务。
- 两个进程间通过标准流（`stdin`/`stdout`）使用 `JSON` 协议通信，保障跨进程消息收发及状态同步的一致性。

## 🔨 安装指引

### 1. Python 环境
需求版本： **Python 3.10+**

拉取依赖：
```bash
pip install -r requirements.txt
```

### 2. Node.js 环境
需求版本： **Node.js 24+**

需要进入到专属于插件的 JS 目录才能安装对应依赖：
```bash
cd src/mineflayer_js_bridge/src
npm install
```

## 📝 配置

1. **环境变量**：将 `.env.prod` / `.env.dev` 修改为你正在使用的 OneBot 连接配置（根据根目录下参考）。
2. **Bot及服务器配置**：转至 `exampleconfigs/` 目录，此处包含了各插件的基础参考，请将其复制至 `configs/` 并根据需求修改。
   （*出于隐私保护考虑，请自行在 `configs/` 内填入您的账号及连接凭据，不要泄露或上传它们！*）

## 💻 指令一览

### 发送给机器人的指令 (Python 端接收)
可通过群聊 `@机器人` 或私聊使用以下核心指令：

| 指令前缀 | 参数 / 子控制命令 | 权限示例要求 | 描述 |
| --- | --- | --- | --- |
| `/mc` | `start` / `stop` / `status` | Admin | 启动、停止并持久化 Mineflayer JS 等待进程；查看运行及连接情况。 |
| `/tpa` | `on` / `off` / `status` / `back` | Admin | 开启关闭 TPA 请求自动允许、传送返回原坐标。 |
| `/home` | `list` / `tp` / `set` / `remove` [名称]| Admin | 在 JS 端设定并在游戏内自动储存/移除 TPA 用 Home 坐标。 |
| `/perm` | `add` / `rm` / `list` / `check` | Super | 调整机器人的用户使用权限组。 |
| `/git` | `pull` | Super | 更新本地 Git 记录并软重启以载入新代码。 |
| `/help` | ➖ | User | 查看分组插件的所有可用使用帮助。 |

### 游戏内指令 (JS 原生接收)
通过游戏中聊天栏或向挂机 Bot 发送 Whisper (私聊)：

| 指令示例 | 描述 |
| --- | --- |
| `#tpa status` | 查看当前的 TPA 服务占用锁状况。 |
| `#tpa back` | （仅限原 TPA 请求占用者或 Admin）申请使 Bot 结束挂机占用，返回原有位置。 |

*注: 游戏内前缀根据你的 `config.js` 设置有所不同（此处默认为 `#`）。*

## 📖 文档速引

- 有关如何在 JS 代码编写本地原生跨通信指令，请参考该目录下的 `src/mineflayer_js_bridge/src/COMMAND_SYSTEM.md`。
- NoneBot 官方文档：[https://nonebot.dev/](https://nonebot.dev/)

### ai生成提醒

本项目的部分代码与文档由 AI 协助生成（其实是很大部分，作者只写了最开始的的js、py代码以及之后的架构指导），可能存在不准确或不完整之处。请在使用前仔细核对相关内容，并根据实际情况进行调整和完善。对于任何发现的问题或建议，欢迎提交 issue 或 pull request 以帮助改进项目质量。
