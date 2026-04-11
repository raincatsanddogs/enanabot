/**
 * 类 NoneBot 风格的 JS 端指令框架
 *
 * 用法：
 *   const { on_command, dispatch_command, init_bot } = require('./commandManager');
 *
 *   // 初始化（传入 bot 实例，在 bot 创建后调用一次）
 *   init_bot(bot);
 *
 *   // 注册指令
 *   const tpa_status = on_command('tpa', {
 *       permission: 'user',       // 'admin' | 'user'，默认 'admin'
 *       description: 'TPA 控制',
 *   });
 *
 *   tpa_status.handle(async (session) => {
 *       const sub = session.args[0];
 *       if (sub === 'status') {
 *           await session.finish('TPA 已开启');
 *       }
 *       await session.send('未知子指令');
 *   });
 *
 *   // 在消息处理流程中调用：
 *   const intercepted = await dispatch_command(sender_name, message_text, permission_level);
 *   // intercepted === true  → 消息已被 JS 内部处理，不再发送到 Python
 *   // intercepted === false → 未命中任何内部指令，照常发往 Python
 */

const config = require('../configs/config');

// ===== 内部状态 =====
let _bot = null;
const _commands = [];  // 注册表：[{ name, options, handler }]

/**
 * 初始化 bot 实例引用。
 * @param {object} bot - mineflayer bot 实例
 */
function init_bot(bot) {
    _bot = bot;
}

// ===== CommandSession =====

class CommandSession {
    /**
     * @param {object} bot - mineflayer bot 实例
     * @param {string} sender_name - 触发者的 MC 玩家名
     * @param {string} command_name - 命中的指令名
     * @param {string[]} args - 指令参数
     * @param {string} raw_text - 原始文本
     * @param {string} permission - 触发者的权限等级
     * @param {string} source_type - 触发来源（'whisper' | 'chat'）
     */
    constructor(bot, sender_name, command_name, args, raw_text, permission, source_type) {
        this.bot = bot;
        this.sender_name = sender_name;
        this.command_name = command_name;
        this.args = args;
        this.raw_text = raw_text;
        this.permission = permission;
        this.source_type = source_type;
        this._finished = false;
    }

    /**
     * 发送私聊消息给触发者，不中断后续执行。
     * @param {string} msg - 要发送的消息
     */
    async send(msg) {
        if (!this.bot || !msg) return;
        this.bot.whisper(this.sender_name, msg);
    }

    /**
     * 发送私聊消息给触发者并立即结束指令处理。
     * @param {string} msg - 要发送的消息（可选）
     * @throws {CommandFinishSignal} 用于中断 handler 执行流
     */
    async finish(msg) {
        if (msg) {
            await this.send(msg);
        }
        this._finished = true;
        throw new CommandFinishSignal();
    }
}

/** 用于控制流中断的特殊信号，非真正异常。 */
class CommandFinishSignal {
    constructor() {
        this.name = 'CommandFinishSignal';
    }
}

// ===== 指令注册 =====

/**
 * 注册一个 JS 端内部指令。
 *
 * @param {string} name - 指令名（不含前缀），匹配消息内容在去除前缀后的首个单词
 * @param {object} [options={}]
 * @param {string} [options.permission='admin'] - 最低权限要求：'admin' | 'user'
 * @param {string} [options.description=''] - 指令描述
 * @returns {{ handle: (fn: (session: CommandSession) => Promise<void>) => void }}
 */
function on_command(name, options = {}) {
    const entry = {
        name: name.toLowerCase(),
        options: {
            permission: options.permission || 'admin',
            description: options.description || '',
        },
        handler: null,
    };

    _commands.push(entry);

    return {
        /**
         * 注册该指令的处理函数。
         * @param {(session: CommandSession) => Promise<void>} fn
         */
        handle(fn) {
            entry.handler = fn;
        },
    };
}

// ===== 权限检查 =====

const PERMISSION_LEVELS = { admin: 2, user: 1 };

/**
 * 检查实际权限是否满足指令要求。
 * @param {string} actual - 触发者权限
 * @param {string} required - 指令要求权限
 * @returns {boolean}
 */
function check_permission(actual, required) {
    return (PERMISSION_LEVELS[actual] || 0) >= (PERMISSION_LEVELS[required] || 0);
}

/**
 * 根据玩家名获取权限等级。
 * @param {string} player_name
 * @returns {'admin' | 'user' | null}
 */
function get_permission_level(player_name) {
    const admin_list = Array.isArray(config.admin_players) ? config.admin_players : [];
    const user_list = Array.isArray(config.user_players) ? config.user_players : [];
    if (admin_list.includes(player_name)) return 'admin';
    if (user_list.includes(player_name)) return 'user';
    return null;
}

// ===== 指令分发 =====

/**
 * 尝试将一条消息匹配到已注册的 JS 内部指令并执行。
 *
 * @param {string} sender_name - 发送者玩家名
 * @param {string} message_text - 去除指令前缀后的完整文本
 * @param {string} source_type - 触发来源：'whisper' | 'chat'
 * @returns {Promise<boolean>} true = 已拦截处理，false = 未命中
 */
async function dispatch_command(sender_name, message_text, source_type) {
    if (!sender_name || !message_text) return false;

    const prefix = (typeof config.whisper_command_prefix === 'string' && config.whisper_command_prefix.trim())
        ? config.whisper_command_prefix.trim()
        : '#';

    const text = message_text.trim();

    // 必须以指令前缀开头
    if (!text.startsWith(prefix)) return false;

    // 鉴权
    const permission = get_permission_level(sender_name);
    if (!permission) return false;

    // 解析：去掉前缀后按空格分割
    const body = text.slice(prefix.length).trim();
    if (!body) return false;

    const parts = body.split(/\s+/);
    const cmd_name = parts[0].toLowerCase();
    const args = parts.slice(1);

    // 查找注册的指令
    const entry = _commands.find(c => c.name === cmd_name);
    if (!entry) {
        // 未在 JS 端注册 → 不拦截，放行给 Python
        return false;
    }

    // 权限检查
    if (!check_permission(permission, entry.options.permission)) {
        if (_bot) {
            _bot.whisper(sender_name, `权限不足：${cmd_name} 需要 ${entry.options.permission} 权限`);
        }
        return true; // 已处理（权限不足的反馈），拦截
    }

    if (!entry.handler) {
        return true; // 指令已注册但未实现 handler，静默拦截
    }

    const session = new CommandSession(
        _bot, sender_name, cmd_name, args, text, permission, source_type
    );

    try {
        await entry.handler(session);
    } catch (e) {
        if (e instanceof CommandFinishSignal) {
            // session.finish() 的正常控制流中断
        } else {
            console.error(`指令 ${cmd_name} 执行异常:`, e);
            if (_bot) {
                _bot.whisper(sender_name, `指令执行出错：${e.message || e}`);
            }
        }
    }

    return true; // 已拦截
}

module.exports = {
    init_bot,
    on_command,
    dispatch_command,
    get_permission_level,
    CommandSession,
    CommandFinishSignal,
};
