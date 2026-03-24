# enanatest

本项目已包含本地插件 `nonebot_plugin_mineflayer`，用于将 Minecraft 消息转发到 QQ 群。

## 1. 安装依赖

先安装项目基础依赖：

```bash
pip install -e .
```

再安装插件额外依赖（SRV 解析）：

```bash
pip install -r src/plugins/nonebot_plugin_mineflayer/requirements.txt
```

如果你使用 `python-javascript` / `mineflayer` 方案，请确保 Node.js 环境可用并已安装对应 npm 包。

另外，`javascript` Python 包会在其自身目录解析 Node 模块。若启动时报错 `Cannot find package 'mineflayer'`，请执行：

```powershell
Push-Location .venv/Lib/site-packages/javascript/js
npm install mineflayer
Pop-Location
```

提示：重建虚拟环境后，这一步通常需要重新执行一次。

## 2. 配置插件

插件配置文件位于：

- `src/plugins/nonebot_plugin_mineflayer/configs/settings.yaml`
- `src/plugins/nonebot_plugin_mineflayer/configs/accounts.yaml`

你可以先复制示例配置：

- `src/plugins/nonebot_plugin_mineflayer/exampleconfigs/settings.yaml`
- `src/plugins/nonebot_plugin_mineflayer/exampleconfigs/accounts.yaml`

然后在 NoneBot 环境变量（如 `.env`）中设置：

```env
mc_profile_index=0
mc_server_index=0
mc_group_whitelist=[123456789]
mc_console_channel_whitelist=[]
mc_console_user_whitelist=[]
```

说明：

- `mc_profile_index`：选择 `account` / `skin` 档位
- `mc_server_index`：选择 `server` 档位
- `mc_group_whitelist`：允许推送的 QQ 群号列表（OneBot）
- `mc_console_channel_whitelist`：Console 频道 ID 列表
- `mc_console_user_whitelist`：Console 私聊用户 ID 列表

Console 调试时可发送命令 `/mc_target`，插件会回显当前 `channel_id` / `user_id`，用于填写上面两个 Console 白名单。

## 3. 启动

```bash
nb run --reload
```

插件位于 `src/plugins`，会由 `pyproject.toml` 中的 `tool.nonebot.plugin_dirs` 自动发现并加载。

## 文档

- NoneBot 文档：https://nonebot.dev/
- 插件说明：`src/plugins/nonebot_plugin_mineflayer/README.md`
