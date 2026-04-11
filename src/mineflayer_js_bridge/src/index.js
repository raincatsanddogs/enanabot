//process.env.DEBUG = "minecraft-protocol"

const mineflayer = require('mineflayer');
const readline = require('node:readline');
const fs = require('fs');
const path = require('path');
const config = require('./src/configs/config');
const { resolveSrv } = require('./src/login/srv');
const { handle_message, group_msg_handler, extract_whisper_info, extract_chat_info } = require('./src/handler/messageHandler');
const { init_bot, on_command, dispatch_command } = require('./src/handler/commandManager');
const homeCache = require('./src/handler/homeCache');
const ipc = require('./src/ipc/ipc_protocol');

// ===== TPA 状态管理（JS 端持久化） =====
const TPA_STATE_FILE = path.join(__dirname, '../../../../configs/tpa_state.json');
const TPA_STATE = {
    enabled: false,
    occupied: false,
    occupied_by: null,
};

function load_tpa_state() {
    try {
        if (!fs.existsSync(TPA_STATE_FILE)) return;
        const raw = fs.readFileSync(TPA_STATE_FILE, 'utf-8');
        const parsed = JSON.parse(raw);
        if (typeof parsed.enabled === 'boolean') TPA_STATE.enabled = parsed.enabled;
        if (typeof parsed.occupied === 'boolean') TPA_STATE.occupied = parsed.occupied;
        if (parsed.occupied_by !== undefined) TPA_STATE.occupied_by = parsed.occupied_by;
    } catch (e) {
        console.error(`[tpa] load state 失败: ${e.message || e}`);
    }
}

function save_tpa_state() {
    try {
        const dir = path.dirname(TPA_STATE_FILE);
        if (!fs.existsSync(dir)) {
            fs.mkdirSync(dir, { recursive: true });
        }
        fs.writeFileSync(TPA_STATE_FILE, JSON.stringify({
            enabled: TPA_STATE.enabled,
            occupied: TPA_STATE.occupied,
            occupied_by: TPA_STATE.occupied_by,
            updated_at: new Date().toISOString(),
        }, null, 2), 'utf-8');
    } catch (e) {
        console.error(`[tpa] save state 失败: ${e.message || e}`);
    }
}

const QQ_FORWARD_PREFIX = (typeof config.forward_prefix === 'string' && config.forward_prefix.trim())
    ? config.forward_prefix.trim()
    : '[群聊]>>';

const startArgs = process.argv.slice(2);
try {
    if (startArgs.length == 0) {
        console.log("未指定配置文件，默认使用第一个配置");
    } else if (startArgs.length > 0) {
        if (startArgs.length > 5 || startArgs[0] != "-p" || isNaN(startArgs[1]) || startArgs[1] <= 0
            || startArgs[2] != "-s" || isNaN(startArgs[3]) || startArgs[3] <= 0) {
            console.error(`无效的配置参数,参数应为: -p <档案编号> -s <服务器编号>，错误参数如下：`);
            throw new Error(startArgs);
        }
    }
} catch (e) {
    console.error(e.message);
    process.exit(1);
}

const profile = (startArgs[1] - 1) || 0;

function build_forward_message(message) {
    const normalized = (message || '').trim();
    if (!normalized) {
        return '';
    }

    if (normalized.startsWith(QQ_FORWARD_PREFIX)) {
        return normalized;
    }

    return `${QQ_FORWARD_PREFIX} ${normalized}`;
}

function normalize_command_text(raw_text) {
    if (typeof raw_text !== 'string') {
        return '';
    }

    return raw_text
        .replace(/\u00A7[0-9A-FK-OR]/ig, '')
        .replace(/[\u200B-\u200D\uFEFF]/g, '')
        .trim();
}

function parse_prefixed_command(raw_text) {
    const text = normalize_command_text(raw_text);
    if (!text) {
        return null;
    }

    const prefix = (typeof config.whisper_command_prefix === 'string' && config.whisper_command_prefix.trim())
        ? config.whisper_command_prefix.trim()
        : '#';

    if (!text.startsWith(prefix)) {
        return null;
    }

    const body = text.slice(prefix.length).trim();
    if (!body) {
        return null;
    }

    const parts = body.split(/\s+/);
    return {
        command: parts[0].toLowerCase(),
        args: parts.slice(1),
        normalized_text: `${prefix}${body}`,
    };
}

function stringify_error(err) {
    if (err === undefined || err === null) {
        return '未知错误';
    }

    if (typeof err === 'string') {
        return err;
    }

    if (Array.isArray(err)) {
        const parts = err
            .map((item) => stringify_error(item))
            .filter((item) => typeof item === 'string' && item.trim());
        return parts.length > 0 ? parts.join(', ') : '未知错误';
    }

    if (typeof err === 'object') {
        if (typeof err.message === 'string' && err.message.trim()) {
            return err.message.trim();
        }
        try {
            return JSON.stringify(err);
        } catch {
            return String(err);
        }
    }

    return String(err);
}

function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

// ===== Home 操作（纯指令 + 缓存） =====

/**
 * 执行 Home 操作。所有子指令均在 JS 端本地闭环。
 * - list: 纯缓存读取；若 needsRefresh 则走一次 GUI 同步
 * - tp: 直接发送 /home <name> 文本指令
 * - set: 发送 /sethome <name> 并更新缓存
 * - remove: 发送 /removehome <name> 并更新缓存
 *
 * @param {object} bot - mineflayer bot 实例
 * @param {string} command - 子指令
 * @param {string} [name] - home 名称
 * @returns {Promise<{ success: boolean, result?: any, error?: string }>}
 */
async function execute_home_operation(bot, command, name) {
    if (command === 'list') {
        // 首次且无缓存时触发一次 GUI 同步
        if (homeCache.needsRefresh()) {
            try {
                const { listHomes } = require('./src/handler/containerUtils');
                const homes = await listHomes(bot);
                homeCache.setFromGUI(homes);
            } catch (e) {
                return {
                    success: false,
                    error: `GUI 同步失败: ${stringify_error(e)}`,
                };
            }
        }
        return {
            success: true,
            result: homeCache.getList(),
        };
    }

    if (command === 'tp') {
        if (!name) {
            return { success: false, error: '缺少 home 名称' };
        }
        bot.chat(`/home ${name}`);
        return {
            success: true,
            result: name,
        };
    }

    if (command === 'set') {
        if (!name) {
            return { success: false, error: '缺少 home 名称' };
        }
        bot.chat(`/sethome ${name}`);
        homeCache.addHome(name);
        return {
            success: true,
            result: name,
        };
    }

    if (command === 'remove') {
        if (!name) {
            return { success: false, error: '缺少 home 名称' };
        }
        bot.chat(`/removehome ${name}`);
        homeCache.removeHome(name);
        return {
            success: true,
            result: name,
        };
    }

    return {
        success: false,
        error: `未知 home 子指令: ${command}`,
    };
}

// ===== TPA 自动接受逻辑 =====

/**
 * 处理 TPA 请求的自动接受流程（全 JS 端闭环）。
 * 1. 同步上锁（occupied = true）并写盘
 * 2. 执行 /sethome tpabackup 并更新缓存
 * 3. 等待 1000ms 弹性时间
 * 4. 执行 /tpaccept（或对应指令）
 * 5. 通过 IPC 通知 Python 端
 *
 * @param {object} bot - mineflayer bot 实例
 * @param {object} tpa_info - { requester, tpa_type, accept_command }
 */
async function handle_tpa_auto_accept(bot, tpa_info) {
    const { requester, tpa_type, accept_command } = tpa_info;

    // 1. 同步上锁
    TPA_STATE.occupied = true;
    TPA_STATE.occupied_by = requester;
    save_tpa_state();

    // 2. 执行 sethome + 缓存
    bot.chat('/sethome tpabackup');
    homeCache.addHome('tpabackup');

    try {
        // 3. 弹性等待
        await delay(1000);

        // 4. 执行 tpaccept
        bot.chat(accept_command);

        // 5. 通知 QQ 群
        const notification = ipc.encode(ipc.ACTION_TPA_NOTIFICATION, {
            msg: `TPA 自动接受: ${requester} (${tpa_type})`,
        });
        process.stdout.write(notification);

        console.log(`TPA auto-accepted: ${requester} (${tpa_type})`);
    } catch (e) {
        // 回滚锁
        TPA_STATE.occupied = false;
        TPA_STATE.occupied_by = null;
        save_tpa_state();

        console.error(`TPA auto-accept 失败: ${e.message}`);

        const fail_notification = ipc.encode(ipc.ACTION_TPA_NOTIFICATION, {
            msg: `TPA 自动接受失败: ${e.message}`,
        });
        process.stdout.write(fail_notification);
    }
}

/**
 * 执行 TPA Back 操作（全 JS 端闭环）。
 * 1. 传送到 tpabackup: /home tpabackup
 * 2. 等待 forcedMove 或 1500ms 保底延迟
 * 3. 删除备份: /removehome tpabackup + 移除缓存
 * 4. 释放锁并写盘
 *
 * @param {object} bot - mineflayer bot 实例
 * @returns {Promise<string>} 结果文本
 */
async function execute_tpa_back(bot) {
    if (!TPA_STATE.occupied) {
        return '当前没有占用，无需返回';
    }

    // 1. 传送
    bot.chat('/home tpabackup');

    // 2. 等待落地
    await new Promise((resolve) => {
        let timer = null;
        const on_move = () => {
            if (timer) clearTimeout(timer);
            // 给一点缓冲确保完全落地
            setTimeout(resolve, 300);
        };
        bot.once('forcedMove', on_move);
        timer = setTimeout(() => {
            bot.removeListener('forcedMove', on_move);
            resolve();
        }, 1500);
    });

    // 3. 删除备份
    bot.chat('/removehome tpabackup');
    homeCache.removeHome('tpabackup');

    // 4. 释放锁
    TPA_STATE.occupied = false;
    TPA_STATE.occupied_by = null;
    save_tpa_state();

    return '已返回原位置';
}


// ===== Home 辅助函数 =====

function normalize_home_list(result) {
    if (!Array.isArray(result)) {
        return null;
    }

    const names = [];
    for (const entry of result) {
        if (typeof entry === 'string') {
            const name = entry.trim();
            if (name) {
                names.push(name);
            }
            continue;
        }

        if (entry && typeof entry === 'object') {
            const candidate = [entry.name, entry.label, entry.home, entry.title]
                .find((item) => typeof item === 'string' && item.trim());
            if (candidate) {
                names.push(candidate.trim());
            }
        }
    }

    return [...new Set(names)];
}

function format_home_result_message(command, success, result, error, name) {
    const errMsg = stringify_error(error);

    if (command === 'list') {
        if (!success) {
            return `获取 home 列表失败: ${errMsg}`;
        }
        const homes = normalize_home_list(result);
        if (homes && homes.length > 0) {
            return `Home 列表: ${homes.join(', ')}`;
        }
        if (typeof result === 'string' && result.trim()) {
            return `Home 列表: ${result.trim()}`;
        }
        return '没有设置任何 home';
    }

    if (command === 'tp') {
        if (success) {
            return `已传送到 home: ${name || result || ''}`.trim();
        }
        return `传送失败: ${errMsg}`;
    }

    if (command === 'set') {
        if (success) {
            return `已设置 home: ${name || result || ''}`.trim();
        }
        return `设置失败: ${errMsg}`;
    }

    if (command === 'remove') {
        if (success) {
            return `已删除 home: ${name || result || ''}`.trim();
        }
        return `删除失败: ${errMsg}`;
    }

    return success ? String(result || '') : `操作失败: ${errMsg}`;
}

function is_home_related_chat_message(post_msg) {
    if (!post_msg || typeof post_msg !== 'object') {
        return false;
    }

    const text = normalize_command_text(post_msg.text || '');
    if (!text) {
        return false;
    }

    return (
        text.includes('点击传送至') ||
        text.includes('Shift+右键来移除家') ||
        text.includes('按 Q 编辑') ||
        /^#\d+:\s*x\d+/i.test(text)
    );
}

/**
 * 处理来自 Py 的统一 IPC 消息。
 * @param {object} bot - mineflayer bot 实例
 * @param {object} envelope - 已解码的 IPC envelope { action, timestamp, data }
 */
function handle_incoming_ipc(bot, envelope) {
    const { action, data } = envelope;

    switch (action) {
        case ipc.ACTION_QQ_MESSAGE: {
            // QQ 群消息转发到 MC
            const sendGroup = Array.isArray(config.send_group) ? config.send_group : [];
            const ignoreUser = Array.isArray(config.ignore_user) ? config.ignore_user : [];

            const incoming = {
                msg: (data.msg || '').trim(),
                group_id: data.group_id,
                sender_id: data.sender_id,
            };

            if (!incoming.msg) return;

            const msg = group_msg_handler(incoming, sendGroup, ignoreUser);
            if (typeof msg !== 'string' || msg.trim().length === 0) return;

            const outgoingText = build_forward_message(msg);
            if (!outgoingText) return;

            bot.chat(outgoingText);
            break;
        }

        case ipc.ACTION_WHISPER_REPLY: {
            // 指令执行结果回复给 MC 玩家
            const targetPlayer = data.target_player;
            const replyMsg = data.msg;
            if (typeof targetPlayer === 'string' && targetPlayer && typeof replyMsg === 'string' && replyMsg) {
                bot.whisper(targetPlayer, replyMsg);
            }
            break;
        }

        case ipc.ACTION_DELEGATE_COMMAND: {
            // Python 委托 JS 执行指令
            handle_delegated_command(bot, data);
            break;
        }

        default:
            console.warn(`未知的 IPC action: ${action}`);
    }
}

/**
 * 处理 Python 委托的指令执行。
 * @param {object} bot
 * @param {object} data - { command, args, reply_to, permission }
 */
async function handle_delegated_command(bot, data) {
    const { command, args, reply_to, permission } = data;
    const arg_list = Array.isArray(args) ? args : [];

    let result_text = '';

    try {
        if (command === 'tpa') {
            result_text = await execute_delegated_tpa(bot, arg_list, permission || 'user');
        } else if (command === 'home') {
            result_text = await execute_delegated_home(bot, arg_list, permission || 'user');
        } else {
            result_text = `未知委托指令: ${command}`;
        }
    } catch (e) {
        result_text = `指令执行失败: ${stringify_error(e)}`;
    }

    // 回传结果给 Python
    const result_msg = ipc.encode(ipc.ACTION_DELEGATE_RESULT, {
        reply_to: reply_to || '',
        command: command,
        args: arg_list,
        result: result_text,
    });
    process.stdout.write(result_msg);
}

/**
 * 执行被委托的 tpa 指令。
 */
async function execute_delegated_tpa(bot, args, permission) {
    const sub = (args[0] || '').toLowerCase();

    if (!sub) {
        return '用法: tpa <on|off|status|back>';
    }

    if (sub === 'on') {
        if (permission !== 'admin') return '权限不足：需要管理员权限';
        TPA_STATE.enabled = true;
        save_tpa_state();
        return 'TPA 自动接受已开启';
    }

    if (sub === 'off') {
        if (permission !== 'admin') return '权限不足：需要管理员权限';
        if (TPA_STATE.occupied) {
            try {
                await execute_tpa_back(bot);
            } catch (e) {
                return `关闭失败: ${stringify_error(e)}`;
            }
        }
        TPA_STATE.enabled = false;
        TPA_STATE.occupied = false;
        TPA_STATE.occupied_by = null;
        save_tpa_state();
        return 'TPA 自动接受已关闭';
    }

    if (sub === 'status') {
        const enabled_text = TPA_STATE.enabled ? '开启' : '关闭';
        const occupied_text = TPA_STATE.occupied
            ? `是（${TPA_STATE.occupied_by}）`
            : '否';
        return `TPA 状态：\n- 自动接受: ${enabled_text}\n- 当前占用: ${occupied_text}`;
    }

    if (sub === 'back') {
        if (!TPA_STATE.occupied) {
            return '当前没有占用，无需返回';
        }
        // 检查权限：占用者本人 (whisper 场景) 或 admin
        if (permission !== 'admin') {
            return '权限不足：需要管理员权限或占用者本人';
        }
        try {
            return await execute_tpa_back(bot);
        } catch (e) {
            return `返回失败: ${stringify_error(e)}`;
        }
    }

    return '用法: tpa <on|off|status|back>';
}

/**
 * 执行被委托的 home 指令。
 */
async function execute_delegated_home(bot, args, permission) {
    const sub = (args[0] || '').toLowerCase();
    const name = args[1] || null;

    if (!sub) {
        return '用法: home <list|tp|set|remove> [名称]';
    }

    const ALL_SUB = new Set(['list', 'tp', 'set', 'remove']);
    const USER_ALLOWED = new Set(['list']);

    if (!ALL_SUB.has(sub)) {
        return '用法: home <list|tp|set|remove> [名称]';
    }

    if (permission !== 'admin' && !USER_ALLOWED.has(sub)) {
        return `权限不足：home ${sub}`;
    }

    if (sub === 'tp' && !name) {
        // 没指定名称时返回列表
        const operation = await execute_home_operation(bot, 'list');
        return format_home_result_message('list', operation.success, operation.result, operation.error, '');
    }

    if ((sub === 'set' || sub === 'remove') && !name) {
        return `用法: home ${sub} <名称>`;
    }

    try {
        const operation = await execute_home_operation(bot, sub, name);
        return format_home_result_message(sub, operation.success, operation.result, operation.error, name);
    } catch (e) {
        return format_home_result_message(sub, false, '', stringify_error(e), name);
    }
}


function setup_readline_bridge(bot) {
    const rl = readline.createInterface({
        input: process.stdin,
        crlfDelay: Infinity,
    });

    rl.on('line', (line) => {
        try {
            const envelope = ipc.decode(line);
            if (!envelope) return;

            handle_incoming_ipc(bot, envelope);
        } catch (error) {
            console.error(`处理 stdin 消息失败: ${error.message || error}`);
        }
    });

    rl.on('close', () => {
        console.warn('stdin 已关闭，readline 停止监听');
    });
}

// ===== 注册 JS 端内部指令（whisper / chat 场景） =====

// tpa 指令 — 全功能本地闭环
const tpa_command = on_command('tpa', { permission: 'guest', description: 'TPA 控制' });
tpa_command.handle(async (session) => {
    const sub = (session.args[0] || '').toLowerCase();

    if (sub === 'status' || !sub) {
        const enabled_text = TPA_STATE.enabled ? '开启' : '关闭';
        const occupied_text = TPA_STATE.occupied
            ? `是（${TPA_STATE.occupied_by}）`
            : '否';
        await session.finish(
            `TPA 状态: 自动接受=${enabled_text}, 占用=${occupied_text}`
        );
    }

    if (sub === 'on') {
        if (session.permission !== 'admin') {
            await session.finish('权限不足：需要管理员权限');
        }
        TPA_STATE.enabled = true;
        save_tpa_state();
        await session.finish('TPA 自动接受已开启');
    }

    if (sub === 'off') {
        if (session.permission !== 'admin') {
            await session.finish('权限不足：需要管理员权限');
        }
        if (TPA_STATE.occupied) {
            try {
                await execute_tpa_back(session.bot);
            } catch (e) {
                await session.finish(`关闭失败: ${stringify_error(e)}`);
            }
        }
        TPA_STATE.enabled = false;
        TPA_STATE.occupied = false;
        TPA_STATE.occupied_by = null;
        save_tpa_state();
        await session.finish('TPA 自动接受已关闭');
    }

    if (sub === 'back') {
        if (!TPA_STATE.occupied) {
            await session.finish('当前没有占用，无需返回');
        }
        // 占用者本人或 admin
        const is_occupier = (
            session.sender_name &&
            TPA_STATE.occupied_by &&
            session.sender_name.toLowerCase() === TPA_STATE.occupied_by.toLowerCase()
        );
        if (session.permission !== 'admin' && !is_occupier) {
            await session.finish('权限不足：需要管理员权限或占用者本人');
        }
        try {
            const result = await execute_tpa_back(session.bot);
            await session.finish(result);
        } catch (e) {
            await session.finish(`返回失败: ${stringify_error(e)}`);
        }
    }

    await session.finish(`未知子指令: ${sub}。可用: status, on, off, back`);
});

// home 指令 — 全功能本地闭环
const home_command = on_command('home', { permission: 'guest', description: 'Home 管理指令' });
home_command.handle(async (session) => {
    const usage = '用法: #home <list|tp|set|remove> [名称]';
    const sub_raw = session.args[0];

    if (!sub_raw) {
        await session.finish(usage);
    }

    const sub = String(sub_raw).toLowerCase();
    const name = session.args[1];
    const ALL_SUB = new Set(['list', 'tp', 'set', 'remove']);
    const USER_ALLOWED = new Set(['list']);

    if (!ALL_SUB.has(sub)) {
        await session.finish(session.permission === 'admin' ? usage : `权限不足：home ${sub}`);
    }

    // 非 admin 仅允许 home list
    if (session.permission !== 'admin' && !USER_ALLOWED.has(sub)) {
        await session.finish(`权限不足：home ${sub}`);
    }

    if (sub === 'tp' && !name) {
        const operation = await execute_home_operation(session.bot, 'list');
        const message = format_home_result_message('list', operation.success, operation.result, operation.error, '');
        await session.finish(message);
    }

    if ((sub === 'set' || sub === 'remove') && !name) {
        await session.finish(`用法: #home ${sub} <名称>`);
    }

    try {
        const operation = await execute_home_operation(session.bot, sub, name);
        const message = format_home_result_message(sub, operation.success, operation.result, operation.error, name);
        await session.finish(message);
    } catch (e) {
        const message = format_home_result_message(sub, false, '', e.message || String(e), name);
        await session.finish(message);
    }
});

const echo = on_command('echo', { permission: 'guest', description: 'Echo 回显测试指令' });
echo.handle(async (session) => {
    const response = session.args.join(' ');
    await session.finish(`${response}`);
});

const help = on_command('help', { permission: 'guest', description: '显示帮助信息' });
help.handle(async (session) => {
    const sub = session.args[0];
    if (!sub) {
        response = '可用指令: tpa, home, echo, help。使用 "#help <指令名>" 查看指令详情。';
        await session.finish(response);
    }
    switch (sub) {
        case 'tpa':
            await session.finish('tpa 指令: 查看 TPA 状态。\n用法: #tpa [status|on|off|back]\n子指令 status: 查看状态；\n on: 开启自动接受；\n off: 关闭自动接受；\n back: 释放占用');
            break;
        case 'home':
            await session.finish('home 指令: 管理 home。\n用法: #home <list|tp|set|remove> [名称]\n非 admin 仅可使用 list。');
            break;
        case 'echo':
            await session.finish('echo 指令: 回显测试。用法: #echo <文本>');
            break;
        case 'help':
            await session.finish('help 指令: 显示帮助信息。用法: #help <指令名>');
            break;
    }
});

async function main() {

    // 启动时加载状态
    load_tpa_state();
    homeCache.load();
    console.log(`[init] TPA state: enabled=${TPA_STATE.enabled}, occupied=${TPA_STATE.occupied}`);
    console.log(`[init] Home cache: ${homeCache.getList().length} homes, needsRefresh=${homeCache.needsRefresh()}`);

    const srvHost = await resolveSrv(config.server[profile].url);
    if (srvHost) {
        console.log(`SRV record found: ${srvHost.host}:${srvHost.port}`);
        config.server[profile].url = srvHost.host;
        config.server[profile].port = srvHost.port;
    } else {
        console.log(`No SRV record found for ${config.server[profile].url}, using original host and port.`);
    }

    const bot = mineflayer.createBot({
        host: config.server[profile].url,
        port: config.server[profile].port,
        username: config.account[profile].name,
        password: config.account[profile].password,
        auth: config.account[profile].authType,
        version: config.server[profile].version,
        authServer: config.skin[profile].authServer,
        sessionServer: config.skin[profile].sessionServer,
    });

    // 初始化 commandManager 的 bot 引用
    init_bot(bot);

    setup_readline_bridge(bot);

    // ===== 在线玩家定时采集 =====
    let playerListInterval = null;

    function collect_player_list() {
        const players = bot.players;
        const playerList = [];

        for (const name in players) {
            const player = players[name];
            if (!player || player.username === bot.username) continue;

            // 优先使用服务器提供的 skinData.url，否则回退到 crafatar
            const skinUrl = (player.skinData && player.skinData.url)
                ? player.skinData.url
                : `https://crafatar.com/avatars/${player.uuid}?size=32&overlay`;

            playerList.push({
                name: player.username,
                uuid: player.uuid || '',
                skin_url: skinUrl,
            });
        }

        const encoded = ipc.encode(ipc.ACTION_PLAYER_LIST, {
            players: playerList,
            count: playerList.length,
            timestamp: new Date().toISOString(),
            bot_username: bot.username,
        });
        process.stdout.write(encoded);
    }

    bot.once('spawn', () => {
        // 首次上线立即采集一次
        collect_player_list();

        // 之后每 5 分钟采集一次
        playerListInterval = setInterval(collect_player_list, 5 * 60 * 1000);
    });

    //唉，资源包
    bot._client.on('add_resource_pack', (packet) => {
        const uuid = packet.uuid || packet.packId || '00000000-0000-0000-0000-000000000000';
        bot._client.write('resource_pack_receive', { uuid, result: 0 });
        setTimeout(() => {
            bot._client.write('resource_pack_receive', { uuid, result: 3 });
        }, 30);
    });

    // Also handle resource_pack_send for older protocol versions
    bot._client.on('resource_pack_send', (packet) => {
        bot._client.write('resource_pack_receive', { result: 0 });
        setTimeout(() => {
            bot._client.write('resource_pack_receive', { result: 3 });
        }, 300);
    });

    bot.on('message', async (jsonMsg) => {
        try {
            const post_msg = handle_message(jsonMsg, { forwardPrefix: QQ_FORWARD_PREFIX });
            if (!post_msg) {
                return;
            }

            // ===== TPA 检测（必须在指令分发之前） =====
            if (post_msg.type === 'tpa') {
                if (!TPA_STATE.enabled) {
                    // TPA 自动接受未开启，记录并跳过
                    const tpa_params = post_msg.params[0] || {};
                    const detected = ipc.encode(ipc.ACTION_TPA_REQUEST_DETECTED, {
                        requester: tpa_params.requester || '',
                        type: tpa_params.tpa_type || '',
                        auto_accepted: false,
                    });
                    process.stdout.write(detected);
                    return;
                }

                if (TPA_STATE.occupied) {
                    // 已占用，拒绝并发
                    const tpa_params = post_msg.params[0] || {};
                    const notification = ipc.encode(ipc.ACTION_TPA_NOTIFICATION, {
                        msg: `TPA 请求被拒绝（当前被 ${TPA_STATE.occupied_by} 占用）: ${tpa_params.requester || '未知'}`,
                    });
                    process.stdout.write(notification);
                    return;
                }

                // 启动自动接受流程
                const tpa_params = post_msg.params[0] || {};
                handle_tpa_auto_accept(bot, tpa_params);
                return;
            }

            // ===== 指令分发（chat / whisper 都支持）=====

            // whisper（原版 incoming + 非原版入站私聊）
            if (post_msg.type === 'whisper') {
                const whisper_info = extract_whisper_info(jsonMsg);
                if (!whisper_info) return; // 非入站私聊或解析失败

                const parsed_whisper_command = parse_prefixed_command(whisper_info.whisper_text);
                const normalized_whisper_text = parsed_whisper_command
                    ? parsed_whisper_command.normalized_text
                    : normalize_command_text(whisper_info.whisper_text);

                // 先尝试 JS 端内部指令
                const intercepted = await dispatch_command(
                    whisper_info.player_name,
                    normalized_whisper_text,
                    'whisper'
                );

                if (intercepted) {
                    // JS 内部已处理，不发送到 Python
                    return;
                }

                // home 和 tpa 指令必须在 JS 本地闭环，不再回退到 Python。
                if (parsed_whisper_command && (parsed_whisper_command.command === 'home' || parsed_whisper_command.command === 'tpa')) {
                    console.warn(`[${parsed_whisper_command.command}] dispatch 未拦截，已阻止回退到 Python`);
                    return;
                }

                // 未被 JS 端拦截 → 发送到 Python 端处理 whisper 指令
                const { handleWhisperCommand } = require('./src/handler/whisperCommandHandler');
                const cmdResult = handleWhisperCommand(
                    whisper_info.player_name,
                    normalized_whisper_text,
                    config
                );

                if (cmdResult) {
                    // 鉴权通过，发送指令到 Py 端
                    const encoded = ipc.encode(ipc.ACTION_WHISPER_COMMAND, cmdResult);
                    process.stdout.write(encoded);
                }
                // 无论是否为指令，whisper 都不转发到 QQ
                return;
            }

            // 非原版 chat → 也可触发 JS 端指令
            if (post_msg.type === 'chat') {
                const chat_info = extract_chat_info(jsonMsg);
                if (chat_info && chat_info.sender_name && chat_info.chat_text) {
                    const parsed_chat_command = parse_prefixed_command(chat_info.chat_text);
                    const normalized_chat_text = parsed_chat_command
                        ? parsed_chat_command.normalized_text
                        : normalize_command_text(chat_info.chat_text);

                    const intercepted = await dispatch_command(
                        chat_info.sender_name,
                        normalized_chat_text,
                        'chat'
                    );

                    if (intercepted) {
                        // JS 内部已处理，不转发到 QQ
                        return;
                    }
                }
            }

            // Home 本地操作期间（GUI 同步），抑制 home 列表相关的系统聊天行
            if (homeCache.needsRefresh() && is_home_related_chat_message(post_msg)) {
                return;
            }

            // 非 whisper 消息（且未被内部指令拦截），使用统一 IPC 格式输出到 Py 端
            const encoded = ipc.encode(ipc.ACTION_MC_MESSAGE, post_msg);
            process.stdout.write(encoded);
        } catch (e) {
            console.error('Error processing message:', e?.jsonMsg || e);
            return;
        }
    });

    bot.on('death',() => {
        bot.chat('/dback')
        console.warn('bot died, sent /dback command');
    });

    bot.on('error', (error) => {
        console.error('Bot error:', error);
    });

    bot.on('end', (reason) => {
        if (playerListInterval) {
            clearInterval(playerListInterval);
            playerListInterval = null;
        }
        console.warn(`Bot disconnected: ${reason}`);
        process.exit(1);
    });
}

main().catch(err => {
    console.error(`${err}`);
    process.exit(1);
});