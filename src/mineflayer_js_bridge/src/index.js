//process.env.DEBUG = "minecraft-protocol"

const mineflayer = require('mineflayer');
const readline = require('node:readline');
const config = require('./src/configs/config');
const { resolveSrv } = require('./src/login/srv');
const { handleMessage, group_msg_handler } = require('./src/handler/messageHandler');

const startArgs = process.argv.slice(2);
try {
    if (startArgs.length == 0){
        console.log("未指定配置文件，默认使用第一个配置");
    }else if (startArgs.length > 0){
        if (startArgs.length > 5 || startArgs[0] != "-p" || isNaN(startArgs[1]) || startArgs[1] <= 0
            || startArgs[2] != "-s" || isNaN(startArgs[3]) || startArgs[3] <= 0){
            console.error(`无效的配置参数,参数应为: -p <档案编号> -s <服务器编号>，错误参数如下：`);
            throw new Error(startArgs);
        }
    }
}catch (e) {
    console.error(e.message);
    process.exit(1);
}

const profile = (startArgs[1] - 1) || 0;

//debug
//console.log(config.skin)

function normalizeNumericId(rawId) {
    if (typeof rawId === 'number') {
        return rawId;
    }

    if (typeof rawId === 'string' && /^\d+$/.test(rawId)) {
        return Number(rawId);
    }

    return rawId;
}

function normalizeIncomingPayload(rawLine) {
    const line = rawLine.trim();
    if (!line) {
        return null;
    }

    // Keep backward compatibility: non-JSON line is treated as direct chat text.
    if (!line.startsWith('{')) {
        return { msg: line };
    }

    let parsed;
    try {
        parsed = JSON.parse(line);
    } catch (error) {
        throw new Error(`stdin 不是有效 JSON: ${error.message}`);
    }

    if (!parsed || typeof parsed !== 'object') {
        throw new Error('stdin 消息必须是 JSON 对象');
    }

    if (typeof parsed.msg !== 'string') {
        throw new Error('stdin 消息缺少字符串字段 msg');
    }

    const normalized = {
        msg: parsed.msg.trim(),
        group_id: normalizeNumericId(parsed.group_id ?? parsed.group ?? parsed.groupId),
        sender_id: normalizeNumericId(parsed.sender_id ?? parsed.sender ?? parsed.senderId),
    };

    if (!normalized.msg) {
        return null;
    }

    return normalized;
}

function setupReadlineBridge(bot) {
    const sendGroup = Array.isArray(config.send_group) ? config.send_group : [];
    const ignoreUser = Array.isArray(config.ignore_user) ? config.ignore_user : [];

    const rl = readline.createInterface({
        input: process.stdin,
        crlfDelay: Infinity,
    });

    rl.on('line', (line) => {
        try {
            const incoming = normalizeIncomingPayload(line);
            if (!incoming) {
                return;
            }

            const msg = group_msg_handler(incoming, sendGroup, ignoreUser);
            if (typeof msg !== 'string' || msg.trim().length === 0) {
                return;
            }

            bot.chat(msg.trim());
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
    }else {
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
            console.log('Received message');
            const post_msg = handleMessage(jsonMsg);
            const post_const = {
                timestamp : Date.now(),
                msg : post_msg
            }
            console.info(JSON.stringify(post_const));
        } catch (e) {
            console.error('Error processing message:', e?.jsonMsg || e);
            return;
        }   
    });

    bot.on('error', (error) => {
        console.error('Bot error:', error);
    });

    bot.on('end', (reason) => {
        console.warn(`Bot disconnected: ${reason}`);
        process.exit(1);
    });
}

main().catch(err => {
    console.error(`${err}`);
    process.exit(1);
});