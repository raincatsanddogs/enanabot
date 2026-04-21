# Mineflayer JS 桥接端指令系统文档

本文档介绍如何在 JS 端 (`mineflayer_js_bridge`) 内部注册和处理类 nonebot 风格的插件化指令。

## 设计目的与架构

默认情况下，桥接端收到的聊天或私聊消息会通过 IPC 发送至 Python 侧进行集中处理。
但是在一些需要**极高响应速度**或**强烈依赖 JS 端内部状态**的场景（例如 TPA 请求拦截验证、简单的内部状态查询等），来回的跨进程通信（IPC）不仅会增加延迟，也容易产生数据竞争死锁（Race Condition）（真的吗（）。

为了解决这个问题，在 JS 端编写了一套轻量级的类 nonebot 指令框架。其工作原理如下：

1. **消息拦截**：每次收到聊天或私聊消息时，首先使用 `dispatch_command` 进行匹配。
2. **本地处理**：如果在 JS 端注册了对应的指令，JS 拦截器就会拦截该消息并在本地消费执行处理函数。
3. **跳过 IPC**：一旦消息在本地被成功拦截（处理掉），就不会再作为普通消息被序列化发送至 Python 端。
4. **放行机制**：如果没有匹配到任何注册的 JS 原生指令，或者权限不足未能执行，消息生命周期继续，被正常发往 Python 端。

---

## 快速开始与示例

当前内置指令已采用统一格式注册：`tpa`、`home`、`echo`、`help`。

核心模块位于 `src/handler/commandManager.js`：

```javascript
const { on_command, dispatch_command, init_bot } = require('./commandManager');
```

### 1. 初始化 (必须)

在主逻辑 (`index.js`) 并且 bot 示例创建成功之后，务必需要先注入 `bot` 实例，供 session 控制流使用：

```javascript
// index.js 内：
const bot = mineflayer.createBot({...})
bot.once('spawn', () => {
    init_bot(bot);
});
```

### 2. 注册与编写指令

`on_command` 用于注册触发器，随后使用 `.handle()` 编写处理逻辑：

```javascript
// 假设我们在一个新文件里： src/plugins/pingCmd.js
const { on_command } = require('../handler/commandManager');

// 注册指令：触发词为 'ping'，最低权限要求 'user'
const ping_cmd = on_command('ping', { 
    permission: 'user', 
    description: '返回一个 Pong 以测试延迟' 
});

// 绑定处理函数
ping_cmd.handle(async (session) => {
    // 【发消息并继续】session.send: 向触发者私聊反馈，但代码继续执行
    await session.send('正在计算延迟...');
    
    // session.bot 为 mineflayer 的原生 bot 对象，可直接操控
    const latency = session.bot.player.ping;
    
    // 【发消息并终端指令流】session.finish: 发送消息后会立刻停止所在的控制流，类似 return
    await session.finish(`Pong! 延迟为：${latency}ms`);
    
    // 如果调用了 finish()，这里的代码永远不会执行
    console.log("这句不会被打印");
});
```

### 3. 被动触发拦截

在解析器模块（如 `messageHandler.js`）接受到消息体时进行分发：

```javascript
const { dispatch_command } = require('./commandManager');

async function processMessage(bot, type, username, message) {
    // 尝试进行指令匹配。注意第三个参数传递来源类型：'whisper' 还是 'chat'
    const intercepted = await dispatch_command(username, message, type);
    
    // 若拦截成功，直接退出接下来的 IPC 消息收发流程
    if (intercepted) {
        return; 
    }
    
    // ... 如果没有由于拦截而 return，就会发送给 Python ...
    sendToPython(type, username, message);
}
```

---

## 核心 API 参考

### 1. `init_bot(bot)`
* **描述**：为整个指令管理器单例提供 minecraft bot 的操作句柄。
* **参数**：
  * `bot`: 对象，mineflayer 实例。

### 2. `on_command(name, options)`
* **描述**：声明一个新指令。支持忽略大小写触发。
* **参数**：
  * `name` (String): 指令名，例如 `"tpa"`。如果用户输入 `#tpa` 或 `#TPA` 均可触发。
  * `options` (Object): 
    * `permission` (String): 默认为 `'admin'`。可选值为 `'admin'`、`'user'` 或 `'guest'`。
    * `description` (String): 指令的中文描述信息，方便作为备注。
* **返回**：包含 `handle` 函数的执行器对象。

### 3. `CommandSession` 实例
每个触发的回调函数都会获得一个 session（会话）代表此时的指令上下文。

* **属性**：
  * `session.bot`: 原生 bot 实例。
  * `session.sender_name` (String): 触发指令的游戏玩家名称。
  * `session.command_name` (String): 命中的指令名称（一律转换为小写格式）。
  * `session.args` (Array&lt;String&gt;): 按空格切割后的所有参数组成。比如 `#tpa status` 得到的是 `['status']`。
  * `session.raw_text` (String): 指令完整的原始文本。
  * `session.permission` (String): 该指令触发者的权限等级，`'admin'`、`'user'` 或 `'guest'`。
  * `session.source_type` (String): 触发方式（聊天/私聊等）。

* **方法**：
  * `async session.send(msg)`: 给用户发送一条**私聊**消息（所有响应默认使用私聊以免引发聊天刷屏），方法不会中断原先流。
  * `async session.finish(msg)`: 发送消息，然后立即以抛出 `CommandFinishSignal` 的方式终止处理函数流。类似 `nonebot2` 的 `finish()`。

### 4. `dispatch_command(sender_name, message_text, source_type)`
* **描述**：供消息分发器使用的触发器。
* **参数**：
  * `sender_name` (String): 触发来源玩家名。
  * `message_text` (String): 收到的消息详情。
  * `source_type` (String): 类别。
* **返回**：Promise&lt;Boolean&gt;，如果拦截返回 true。

---

## 权限系统与配置

指令权限等级根据 `configs/config.js` 文件中的列表动态判定。

* 如果配置文件中的 `config.whisper_command_prefix` 有值，那么它将作为指令前缀（默认是 `#`）。如果用户要触发 `tpa` 指令，需要在游戏里输入 `#tpa`。
* 名称存在于 `config.guest_players` 的玩家会被判定为 `guest` 等级（可选显式配置）。
* 只有名称存在于 `config.user_players` 的玩家会被判定为 `user` 等级。
* 名称存在于 `config.admin_players` 的玩家会被判定为 `admin` 等级。
* 若名称未命中 `admin_players/user_players`，默认判定为 `guest`。
* **权限继承**：`admin > user > guest`。要求 `guest` 权限时，三者都可触发；要求 `user` 时，`admin/user` 可触发；要求 `admin` 时仅 `admin` 可触发。

若玩家权限不符合触发要求，不仅无法执行指令流，指令系统内部还会**拦截该消息并向报错用户发送一条私聊错误提示**。这条错误并不会抛向 Python 层。

---

## TPA 状态机特例

**TPA 业务说明：** 由于 TPA（传送请求）依赖于机器人实时所在游戏的状态（是否在挂机、在睡觉、或者正在忙碌），这如果放在 Python 端会因为网络或者性能导致接受了无效传送，或者造成死锁。因此诸如 `#tpa status`、`#tpa back` 这类强交互的内部功能，都是专门用此 JS 桥接指令系统实现的，实现逻辑可参考 `index.js` 底部的 `tpa_cmd.handle` 以及 `_TPA_LOCK` 机制。

> 注意：为了减少冗余，Python 端的 QQ 群聊若下达相关控制指令，实际上是利用 IPC JSON `server_cmd` 推送特定的后台指令让桥接再转换为对应的逻辑，并非把所有控制放在 Python 里硬写。两者相辅相成。

## Home 指令说明

`home` 指令已按统一 `on_command + session` 格式在 JS 端实现。

- MC 私聊/聊天触发 `#home` 时，会优先由 JS 指令系统拦截并本地处理。
- Python 侧仍保留 `home` 入口用于兼容 QQ 指令与既有流程。
- 权限规则对齐 Python：`admin` 可执行 `list/tp/set/remove`，非 `admin` 仅允许 `home list`。
