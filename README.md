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

其中 `configs/settings.yaml` 新增桥接过滤项（位于 `connect` 下）：

- `send_group`: 仅这些群号的消息会转发到 MC（严格白名单，留空表示不转发）
- `ignore_user`: 这些用户号的消息不会转发到 MC
- `forward_prefix`: 发往 MC 的桥接消息前缀（用于回环过滤）

示例：

```yaml
connect:
  send_group: [123456789]
  ignore_user: [10001]
  forward_prefix: "[群聊]>>"
```

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

在群组中发送@bot /mc start 以加载互通（会持久化为重启后自动恢复）
发送@bot /mc stop 以结束（会关闭自动恢复并清空推送目标）
发送@bot /mc status 可查看当前运行状态、自动恢复状态和推送目标

为避免回环，JS bot 向 MC 发送桥接消息时会自动添加 `forward_prefix` 指定的前缀，并在 `messageHandler` 解析阶段通过正则过滤该前缀消息，防止再次回传到 QQ。

运行状态文件：`configs/mineflayer_js_bridge.runtime.json`

## 4. matplotlib 中文字体（Windows / Linux）

如果图表出现中文乱码或日志提示缺字，请安装任一中文字体（推荐 `Noto Sans CJK SC`）。

### Windows

1. 下载字体（任选其一）
- Noto Sans CJK SC（推荐）
- 微软雅黑（Microsoft YaHei）
- 黑体（SimHei）

2. 安装字体
- 双击字体文件（`.ttf` / `.otf`）
- 点击“为所有用户安装”（推荐）

3. 重启 bot 进程

### Ubuntu / Debian

```bash
sudo apt-get update
sudo apt-get install -y fonts-noto-cjk fonts-wqy-zenhei
fc-cache -f -v
```

安装完成后重启 bot 进程。

### 验证字体是否被 matplotlib 识别

在项目虚拟环境中执行：

```bash
python -c "from matplotlib import font_manager; fs={f.name for f in font_manager.fontManager.ttflist}; print([x for x in ['Microsoft YaHei','SimHei','Noto Sans CJK SC','WenQuanYi Zen Hei'] if x in fs])"
```

输出列表非空即表示已识别到可用中文字体。

### Ubuntu 仍无法显示中文时（排障）

1. 确认字体包已安装

```bash
sudo apt-get update
sudo apt-get install -y fontconfig fonts-noto-cjk fonts-wqy-zenhei
```

2. 刷新系统字体缓存

```bash
fc-cache -f -v
fc-list :lang=zh family | head -n 20
```

3. 清理 matplotlib 字体缓存（非常关键）

```bash
rm -rf ~/.cache/matplotlib
```

4. 在 bot 运行用户下验证 matplotlib 可见字体

```bash
python -c "from matplotlib import font_manager; names=sorted({f.name for f in font_manager.fontManager.ttflist}); print([n for n in names if 'Noto Sans CJK' in n or 'WenQuanYi' in n or 'Source Han' in n][:20])"
```

5. 重启 bot 进程

如果你是 systemd 部署，请确认服务用户与手动执行命令的用户一致，否则会出现“命令行可见字体、服务进程不可见字体”的情况。

## 文档

- NoneBot 文档：[https://nonebot.dev/](https://nonebot.dev/)
- 插件说明：`src/plugins/mineflayer_js_bridge/README.md`
