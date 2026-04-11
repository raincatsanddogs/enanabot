//process.env.DEBUG = "minecraft-protocol"

const mineflayer = require('mineflayer');
const readline = require('node:readline');
const config = require('./src/configs/config');
const { resolveSrv } = require('./src/login/srv');
const { handleMessage, group_msg_handler, extractWhisperInfo } = require('./src/handler/messageHandler');
const { handleWhisperCommand } = require('./src/handler/whisperCommandHandler');
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

function buildForwardMessage(message) {
    const normalized = (message || '').trim();
    if (!normalized) {
        return '';
    }

    if (normalized.startsWith(QQ_FORWARD_PREFIX)) {
        return normalized;
    }

    return `${QQ_FORWARD_PREFIX} ${normalized}`;
}

/**
 * 处理来自 Py 的统一 IPC 消息。
 * @param {object} bot - mineflayer bot 实例
 * @param {object} envelope - 已解码的 IPC envelope { action, timestamp, data }
 */
function handleIncomingIPC(bot, envelope) {
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

            const outgoingText = buildForwardMessage(msg);
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

        default:
            console.warn(`未知的 IPC action: ${action}`);
    }
}

function setupReadlineBridge(bot) {
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

    setupReadlineBridge(bot);

    // ===== 在线玩家定时采集 =====
    let playerListInterval = null;

    function collectPlayerList() {
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
        collectPlayerList();

        // 之后每 5 分钟采集一次
        playerListInterval = setInterval(collectPlayerList, 5 * 60 * 1000);
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

    bot.on('message', jsonMsg => {
        try {
            const post_msg = handleMessage(jsonMsg, { forwardPrefix: QQ_FORWARD_PREFIX });
            if (!post_msg) {
                return;
            }

            // 原版 whisper → 尝试鉴权和指令处理
            if (post_msg.type === 'whisper') {
                const whisperInfo = extractWhisperInfo(jsonMsg);
                if (!whisperInfo) return; // 非原版 whisper 或解析失败

                const cmdResult = handleWhisperCommand(
                    whisperInfo.player_name,
                    whisperInfo.whisper_text,
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

            // 非 whisper 消息，使用统一 IPC 格式输出到 Py 端
            const encoded = ipc.encode(ipc.ACTION_MC_MESSAGE, post_msg);
            process.stdout.write(encoded);
        } catch (e) {
            console.error('Error processing message:', e?.jsonMsg || e);
            return;
        }
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