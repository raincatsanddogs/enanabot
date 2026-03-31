const DEFAULT_FORWARD_PREFIX = '[群聊]>>';

function escapeRegex(text) {
    return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function buildForwardPrefixRegex(prefix) {
    const normalized = (typeof prefix === 'string' && prefix.trim())
        ? prefix.trim()
        : DEFAULT_FORWARD_PREFIX;
    return new RegExp(`${escapeRegex(normalized)}\\s*`);
}

function handleMessage(jsonMsg, options = {}) {
    try {
    const time_stamp = new Date().toISOString();
    let type = undefined;
    const translate = jsonMsg.translate || undefined;
    const text = text_define(jsonMsg);
    let params = [];
    type = type_define(jsonMsg);
    params = params_define(type,jsonMsg); 
    const forwardPrefixRegex = buildForwardPrefixRegex(options.forwardPrefix);

    // 过滤由桥接发送后被服务器回显的消息，避免 QQ->MC->QQ 回环。
    if (type === 'chat' && typeof text === 'string' && forwardPrefixRegex.test(text)) {
        return null;
    }

    return {
        time_stamp:time_stamp,
        type:type, 
        text:text, 
        translate:translate, 
        params:params
    }; 
    } catch (e) {
        
        console.error('处理消息时发生错误:', e);
        throw {
            jsonMsg: jsonMsg,
            error: e.toString()
        };
    }
}

/**
 * 提取非原版消息的可见文本。
 * 有 translate 的消息不处理，直接返回 undefined。
 *
 * 非原版消息的文本结构：jsonMsg.json.extra 数组中，
 * 每个 item 可能有嵌套 extra 子数组（含 text/"" 字段），
 * 或者 item 自身携带 text/"" 字段。逐项拼接即可。
 *
 * @param {object} jsonMsg
 * @returns {string|undefined}
 */
function text_define(jsonMsg) {
    if (jsonMsg.translate || (jsonMsg.json && jsonMsg.json.translate)) return undefined;

    const json = jsonMsg.json || {};
    const extras = json.extra || [];
    let result = json.text || '';

    for (const item of extras) {
        if (item.extra) {
            for (const sub of item.extra) {
                if (typeof sub === 'string') { result += sub; continue; }
                result += sub.text || sub[''] || '';
            }
        } else {
            result += item.text || item[''] || '';
        }
    }

    return result;
}

/**
 * 根据 jsonMsg 的结构特征判断消息类型。
 *
 * jsonMsg.json 层级中，extra 和 with 互斥：
 *   - with + translate  → join / left / whisper(原版) / kill / server_cmd
 *   - extra（无 translate）→ chat / whisper(非原版) / server_chat / server_cmd
 *
 * 支持的类型:
 *   join, left, whisper, kill, chat, server_chat, server_cmd
 *
 * @param {object} jsonMsg - mineflayer 的 jsonMsg 对象
 * @returns {string} 消息类型
 */
function type_define(jsonMsg) {
    const translate = jsonMsg.translate || (jsonMsg.json && jsonMsg.json.translate) || undefined;

    // 1. 有 translate 的消息（jsonMsg.json 用 with 数组携带参数）
    if (translate) {
        if (translate === 'multiplayer.player.joined') return 'join';
        if (translate === 'multiplayer.player.left') return 'left';
        if (translate === 'commands.message.display.incoming') return 'whisper';
        if (translate.startsWith('death.')) return 'kill';
        return 'server_cmd';
    }

    // 2. 无 translate 的消息（jsonMsg.json 用 extra 数组携带内容）
    const extras = (jsonMsg.json && jsonMsg.json.extra) || [];
    const first = extras[0];

    // chat / whisper（非原版）：/msg click_event 固定在 extras[0]
    const cmd = first && first.click_event && first.click_event.command;
    if (typeof cmd === 'string' && cmd.startsWith('/msg ')) {
        // 区分 chat 和 whisper：whisper 的 extras[0].extra[4].text 为 "-> "
        const arrowItem = first.extra && first.extra[4];
        const arrowText = arrowItem && (arrowItem.text || arrowItem['']);
        if (typeof arrowText === 'string' && arrowText.includes('->')) return 'whisper';
        return 'chat';
    }

    // server_chat：QQ 群桥接，提取 extras 一级文本判断
    const flatText = extras.map(item => item.text || item[''] || '').join('');
    if (flatText.includes('Q群')) return 'server_chat';

    // 兜底：服务器插件消息
    return 'server_cmd';
}

//
function params_define(type,jsonMsg) {
    if (type === 'join' || type === 'left') {
        if (jsonMsg.json.with?.[0]?.hasOwnProperty('hover_event')){
            const player_name = jsonMsg.json.with[0].hover_event.name;
            const player_type = jsonMsg.json.with[0].hover_event.id;
            const player_uuid = jsonMsg.json.with[0].hover_event.uuid;
            return [entity_structure(player_type, player_name, player_uuid)];
        }else{
            const player_name = jsonMsg.json.with[0];
            return [entity_structure('non_vanilla_message_player', player_name, [0,0,0,0])];
        }
    }
    if (type === 'whisper') {
        if (jsonMsg.translate === 'commands.message.display.incoming') {
            const sender_name = jsonMsg.json.with[0].hover_event.name;
            const sender_type = jsonMsg.json.with[0].hover_event.id;
            const sender_uuid = jsonMsg.json.with[0].hover_event.uuid;
            return [entity_structure(sender_type, sender_name, sender_uuid)];
        }//此为原版格式的处理方法
        const msg_extras = jsonMsg.json.extra[0]?.extra;//为什么原版和非原版表现形式完全不同，崎宵了
        let name = '';
        for (let i=1; i<msg_extras.length;i++){
            name += msg_extras[i].text;
            if (msg_extras[i][''] === ' '){
                break;
            }
        }
        return [entity_structure('non_vanilla_message_player', name, [0,0,0,0])];//连名字都可能不正确怎么会有entity名和uuid呢（
    }

    if (type === 'kill') {
        const player_name = jsonMsg.json.with[0].hover_event.name;
        const player_type = jsonMsg.json.with[0].hover_event.id;
        const player_uuid = jsonMsg.json.with[0].hover_event.uuid;
        if (jsonMsg.json.with[1] && jsonMsg.json.with[1].hover_event) {
            const killer_name = jsonMsg.json.with[1].hover_event.name?.text || jsonMsg.json.with[1].hover_event.name?.translate || jsonMsg.json.with[1].hover_event.name;
            const killer_type = jsonMsg.json.with[1].hover_event.id;
            const killer_uuid = jsonMsg.json.with[1].hover_event.uuid;
            if (jsonMsg.json.with[2] && jsonMsg.json.with[2].hover_event) {
                const item_type = jsonMsg.json.with[2].hover_event.id
                const item_name = extractCustomName(jsonMsg.json.with[2].hover_event.components) ?? ('item.' + jsonMsg.json.with[2].hover_event.id.replaceAll(":", "."));
                const item_components = jsonMsg.json.with[2].hover_event.components;
                return [entity_structure(player_type, player_name, player_uuid), entity_structure(killer_type, killer_name, killer_uuid),entity_structure(item_type,item_name,item_components)]
            }
            return [entity_structure(player_type, player_name, player_uuid), entity_structure(killer_type, killer_name, killer_uuid)]
        }
        return [entity_structure(player_type, player_name, player_uuid)];
    }

    if (type === 'chat'){
        return [jsonMsg.json.extra[1].extra];
    }
    return [];
}

function extractCustomName(itemData) {
    // 1. 安全地获取 custom_name 字段
    const customName = itemData?.['minecraft:custom_name'];
    
    // 如果没有自定义名称，返回 null
    if (!customName) return null;

    // 2. 形式二：如果直接是字符串，直接返回（如："经典红烧味杯面"）
    if (typeof customName === 'string') {
        return customName;
    }

    // 3. 形式一：如果是对象，说明是 JSON 文本组件（如："毒芽"）
    if (typeof customName === 'object') {
        // 获取基础文本（"毒"）
        let result = customName.text || '';
        
        // 检查是否有额外的文本数组 extra
        if (Array.isArray(customName.extra)) {
        // 遍历 extra 数组，拼接入子文本（"芽"）
        customName.extra.forEach(part => {
            if (part && part.text) {
            result += part.text;
            }
        });
        }
        return result;
    }

    return null;
}

function entity_structure(type, name, uuid) {
    return {
        type: type,
        name: name,
        uuid: uuid
    };
}

function group_msg_handler(jsonMsg, send_group, ignore_user) {
    try {
        const sendGroupList = Array.isArray(send_group) ? send_group : [];
        const ignoreUserList = Array.isArray(ignore_user) ? ignore_user : [];

        if (ignoreUserList.includes(jsonMsg.sender_id)) {
            return null;
        }

        // Strict whitelist: only groups in send_group are allowed.
        if (!sendGroupList.includes(jsonMsg.group_id)) {
            return null;
        }

        return jsonMsg.msg;
    }catch(e){
        throw new Error('error when handling group msg', jsonMsg, e);
    }
}

module.exports = { handleMessage, group_msg_handler };
