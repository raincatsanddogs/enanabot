//process.env.DEBUG = "minecraft-protocol"

const mineflayer = require('mineflayer');
const readline = require('node:readline');
const config = require('./src/configs/config');
const { resolveSrv } = require('./src/login/srv');
const { handle_message, group_msg_handler, extract_whisper_info, extract_chat_info } = require('./src/handler/messageHandler');
const { init_bot, on_command, dispatch_command } = require('./src/handler/commandManager');
const ipc = require('./src/ipc/ipc_protocol');

// ===== TPA 状态缓存（从 Python 同步） =====
const TPA_STATE = {
    enabled: false,
    occupied: false,
    occupied_by: null,
};

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

// ===== sethome Promise 校验 =====

/**
 * 等待服务器回显 sethome 成功消息。
 * 通过监听 bot 的 message 事件，匹配 "你创建了名叫 xxx 的家" 文本。
 *
 * @param {object} bot - mineflayer bot 实例
 * @param {string} home_name - 要等待确认的 home 名称
 * @param {number} [timeout_ms=8000] - 超时毫秒数
 * @returns {Promise<boolean>} 成功返回 true
 */
function wait_for_sethome_confirm(bot, home_name, timeout_ms = 8000) {
    return new Promise((resolve, reject) => {
        let timer = null;

        const on_message = (jsonMsg) => {
            try {
                // 提取可见文本
                const text = jsonMsg.toString();
                if (typeof text === 'string' && text.includes('你创建了名叫') && text.includes(home_name)) {
                    cleanup();
                    resolve(true);
                }
            } catch { /* 忽略解析错误 */ }
        };

        const cleanup = () => {
            if (timer) clearTimeout(timer);
            bot.removeListener('message', on_message);
        };

        bot.on('message', on_message);
        timer = setTimeout(() => {
            cleanup();
            reject(new Error(`sethome ${home_name} 超时 (${timeout_ms}ms)`));
        }, timeout_ms);
    });
}

// ===== TPA 自动接受逻辑 =====

/**
 * 处理 TPA 请求的自动接受流程。
 * 1. 同步上锁（occupied = true）
 * 2. 执行 /sethome tpabackup
 * 3. 等待服务器确认
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

    // 通知 Python 端 TPA 已占用
    const occupied_msg = ipc.encode(ipc.ACTION_TPA_OCCUPIED, {
        occupied_by: requester,
        tpa_type: tpa_type,
    });
    process.stdout.write(occupied_msg);

    // 2. 执行 sethome
    bot.chat('/sethome tpabackup');

    try {
        // 3. 等待服务器确认 sethome 成功
        await wait_for_sethome_confirm(bot, 'tpabackup', 8000);

        // 4. 执行 tpaccept
        bot.chat(accept_command);

        // 5. 通知 QQ 群
        const notification = ipc.encode(ipc.ACTION_TPA_NOTIFICATION, {
            msg: `TPA 自动接受: ${requester} (${tpa_type})`,
        });
        process.stdout.write(notification);

        console.log(`TPA auto-accepted: ${requester} (${tpa_type})`);
    } catch (e) {
        // sethome 超时或失败 → 回滚锁
        TPA_STATE.occupied = false;
        TPA_STATE.occupied_by = null;

        console.error(`TPA auto-accept 失败: ${e.message}`);

        const fail_notification = ipc.encode(ipc.ACTION_TPA_NOTIFICATION, {
            msg: `TPA 自动接受失败: ${e.message}`,
        });
        process.stdout.write(fail_notification);
    }
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

        case ipc.ACTION_TPA_UPDATE_STATE: {
            // Python 端推送 TPA 状态更新
            if (typeof data.enabled === 'boolean') TPA_STATE.enabled = data.enabled;
            if (typeof data.occupied === 'boolean') TPA_STATE.occupied = data.occupied;
            if (data.occupied_by !== undefined) TPA_STATE.occupied_by = data.occupied_by;
            console.log(`TPA state updated: enabled=${TPA_STATE.enabled}, occupied=${TPA_STATE.occupied}`);
            break;
        }

        case ipc.ACTION_HOME_COMMAND: {
            // Python 端请求执行 home 命令
            handle_home_command(bot, data);
            break;
        }

        default:
            console.warn(`未知的 IPC action: ${action}`);
    }
}

/**
 * 处理来自 Python 的 home 命令请求。
 * @param {object} bot
 * @param {object} data - { command, name, reply_to }
 */
async function handle_home_command(bot, data) {
    const { command, name, reply_to } = data;
    try {
        const { listHomes, tpToHome } = require('./src/handler/containerUtils');

        if (command === 'list') {
            const homes = await listHomes(bot);
            const result = ipc.encode(ipc.ACTION_HOME_RESULT, {
                command: 'list',
                reply_to: reply_to || '',
                success: true,
                result: homes,
            });
            process.stdout.write(result);
        } else if (command === 'tp') {
            await tpToHome(bot, name);
            const result = ipc.encode(ipc.ACTION_HOME_RESULT, {
                command: 'tp',
                reply_to: reply_to || '',
                success: true,
                result: name,
            });
            process.stdout.write(result);
        } else if (command === 'set') {
            bot.chat(`/sethome ${name}`);
            const result = ipc.encode(ipc.ACTION_HOME_RESULT, {
                command: 'set',
                reply_to: reply_to || '',
                success: true,
                result: name,
            });
            process.stdout.write(result);
        } else if (command === 'remove') {
            bot.chat(`/delhome ${name}`);
            const result = ipc.encode(ipc.ACTION_HOME_RESULT, {
                command: 'remove',
                reply_to: reply_to || '',
                success: true,
                result: name,
            });
            process.stdout.write(result);
        }
    } catch (e) {
        const result = ipc.encode(ipc.ACTION_HOME_RESULT, {
            command: command,
            reply_to: reply_to || '',
            success: false,
            error: e.message || String(e),
        });
        process.stdout.write(result);
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

// ===== 注册 JS 端内部指令 =====

// tpa 指令
const tpa_command = on_command('tpa', { permission: 'guest', description: 'TPA 状态查看' });
tpa_command.handle(async (session) => {
    const sub = session.args[0];

    if (sub === 'status' || !sub) {
        const enabled_text = TPA_STATE.enabled ? '开启' : '关闭';
        const occupied_text = TPA_STATE.occupied
            ? `是（${TPA_STATE.occupied_by}）`
            : '否';
        await session.finish(
            `TPA 状态: 自动接受=${enabled_text}, 占用=${occupied_text}`
        );
    }

    // 非 status 子指令在 whisper 场景转发给 Python 端统一调度。
    if (session.source_type === 'whisper') {
        const payload = {
            player_name: session.sender_name,
            permission_level: session.permission,
            command: session.command_name,
            args: session.args,
            raw_text: session.raw_text,
        };
        process.stdout.write(ipc.encode(ipc.ACTION_WHISPER_COMMAND, payload));
        return;
    }

    // chat 场景保留本地提示，避免误触发。
    await session.finish(`未知子指令: ${sub}。可用: status`);
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
        response = '可用指令: tpa, echo, help。使用 "#help <指令名>" 查看指令详情。';
        await session.finish(response);
    }
    switch (sub) {
        case 'tpa':
            await session.finish('tpa 指令: 查看 TPA 状态。\n用法: #tpa [status|on|off|back]\n子指令 status: 查看状态；\n on: 开启自动接受；\n off: 关闭自动接受；\n back: 释放占用');
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

        // 向 Python 端请求 TPA 初始状态
        const request_state = ipc.encode(ipc.ACTION_REQUEST_TPA_STATE, {});
        process.stdout.write(request_state);
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

                // 先尝试 JS 端内部指令
                const intercepted = await dispatch_command(
                    whisper_info.player_name,
                    whisper_info.whisper_text,
                    'whisper'
                );

                if (intercepted) {
                    // JS 内部已处理，不发送到 Python
                    return;
                }

                // 未被 JS 端拦截 → 发送到 Python 端处理 whisper 指令
                // 复用原有的 whisperCommandHandler 解析逻辑
                const { handleWhisperCommand } = require('./src/handler/whisperCommandHandler');
                const cmdResult = handleWhisperCommand(
                    whisper_info.player_name,
                    whisper_info.whisper_text,
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
                    const intercepted = await dispatch_command(
                        chat_info.sender_name,
                        chat_info.chat_text,
                        'chat'
                    );

                    if (intercepted) {
                        // JS 内部已处理，不转发到 QQ
                        return;
                    }
                }
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