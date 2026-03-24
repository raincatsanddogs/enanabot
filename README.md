# enanabot

本项目已包含本地插件 `nonebot_plugin_mineflayer`，用于将 Minecraft 消息转发到 QQ 群。

## 1. 安装依赖

```bash
pip install -r requirements.txt
```

请确保 Node.js 环境可用并已安装对应 npm 包。

```bash
npm install
```

提示：重建虚拟环境后，这一步通常需要重新执行一次。

## 2. 配置插件

插件配置文件位于：

- `configs/`

你可以先复制示例配置：

- `exampleconfigs/`

然后在 NoneBot 环境变量（如 `.env`）中设置：

```env
DRIVER=~fastapi
SUPERUSERS=["123456789"]
HOST=0.0.0.0
PORT=8080
```

## 3. 启动

```bash
nb run --reload
```

插件位于 `src/plugins`，会由 `pyproject.toml` 中的 `tool.nonebot.plugin_dirs` 自动发现并加载。

在群组中发送@bot /mc start 以加载互通
发送@bot /mc stop 以结束

## 文档

- NoneBot 文档：https://nonebot.dev/
- 插件说明：`src/plugins/mineflayer_js_bridge/README.md`
