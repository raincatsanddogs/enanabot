const DEFAULT_FORWARD_PREFIX = '[群聊]>>';

function escape_regex(text) {
    return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function build_forward_prefix_regex(prefix) {
    const normalized = (typeof prefix === 'string' && prefix.trim())
        ? prefix.trim()
        : DEFAULT_FORWARD_PREFIX;
    return new RegExp(`${escape_regex(normalized)}\\s*`);
}

function handle_message(jsonMsg, options = {}) {
    try {
        const time_stamp = new Date().toISOString();
        let type = undefined;
        const translate = jsonMsg.translate || undefined;
        const text = text_define(jsonMsg);
        let params = [];
        type = type_define(jsonMsg);
        params = params_define(type, jsonMsg);
        const forward_prefix_regex = build_forward_prefix_regex(options.forwardPrefix);

        // 过滤由桥接发送后被服务器回显的消息，避免 QQ->MC->QQ 回环。
        if (type === 'chat' && typeof text === 'string' && forward_prefix_regex.test(text)) {
            return null;
        }

        return {
            time_stamp: time_stamp,
            type: type,
            text: text,
            translate: translate,
            params: params
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
 *   - extra（无 translate）→ chat / whisper(非原版) / server_chat / server_cmd / tpa
 *
 * 支持的类型:
 *   join, left, whisper, kill, chat, server_chat, server_cmd, tpa
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

    // TPA 检测：遍历 extras 查找 click_event 中包含 tpaccept/tpdeny 的指令
    const tpa_info = detect_tpa_in_extras(extras);
    if (tpa_info) {
        return 'tpa';
    }

    // chat：非原版消息中 click_event 固定在 extras[0]
    // 注意：非原版 whisper 不可信，统一归为 chat 或其他类型，不再识别为 whisper
    const cmd = first && first.click_event && first.click_event.command;
    if (typeof cmd === 'string' && cmd.startsWith('/msg ')) {
        return 'chat';
    }

    // server_chat：QQ 群桥接，提取 extras 一级文本判断
    const flat_text = extras.map(item => item.text || item[''] || '').join('');
    if (flat_text.includes('Q群')) return 'server_chat';

    // 兜底：服务器插件消息
    return 'server_cmd';
}

/**
 * 遍历 extras 数组，深度检测 click_event 中是否包含 TPA 相关指令。
 *
 * @param {Array} extras - jsonMsg.json.extra 数组
 * @returns {{ requester: string, tpa_type: string, accept_command: string } | null}
 */
function detect_tpa_in_extras(extras) {
    if (!Array.isArray(extras)) return null;

    for (const item of extras) {
        const result = check_click_event_for_tpa(item);
        if (result) return result;

        // 检查嵌套的 extra 子元素
        if (Array.isArray(item.extra)) {
            for (const sub of item.extra) {
                const sub_result = check_click_event_for_tpa(sub);
                if (sub_result) return sub_result;
            }
        }
    }
    return null;
}

/**
 * 检查单个元素的 click_event 是否为 TPA 接受/拒绝指令。
 *
 * @param {object} element
 * @returns {{ requester: string, tpa_type: string, accept_command: string } | null}
 */
function check_click_event_for_tpa(element) {
    if (!element || typeof element !== 'object') return null;
    const click = element.click_event;
    if (!click) return null;

    const cmd = click.command || click.value || '';
    if (typeof cmd !== 'string') return null;

    // 匹配常见 TPA 接受指令：/tpaccept, /cmi tpaccept, /tpayes 等
    const tpa_accept_match = cmd.match(/\/((?:cmi\s+)?tp(?:a(?:ccept|yes))|tpyes)\b/i);
    if (!tpa_accept_match) return null;

    return {
        requester: '',  // 在 params_define 中从文本提取
        tpa_type: cmd.toLowerCase().includes('tpahere') ? 'tpahere' : 'tpa',
        accept_command: cmd,
    };
}

//
function params_define(type, jsonMsg) {
    if (type === 'join' || type === 'left') {
        if (jsonMsg.json.with?.[0]?.hasOwnProperty('hover_event')) {
            const player_name = jsonMsg.json.with[0].hover_event.name;
            const player_type = jsonMsg.json.with[0].hover_event.id;
            const player_uuid = jsonMsg.json.with[0].hover_event.uuid;
            return [entity_structure(player_type, player_name, player_uuid)];
        } else {
            const player_name = jsonMsg.json.with[0];
            return [entity_structure('non_vanilla_message_player', player_name, [0, 0, 0, 0])];
        }
    }
    if (type === 'whisper') {
        // 仅处理原版 whisper（translate === 'commands.message.display.incoming'）
        if (jsonMsg.translate === 'commands.message.display.incoming') {
            const sender_name = jsonMsg.json.with[0].hover_event.name;
            const sender_type = jsonMsg.json.with[0].hover_event.id;
            const sender_uuid = jsonMsg.json.with[0].hover_event.uuid;
            return [entity_structure(sender_type, sender_name, sender_uuid)];
        }
        // 非原版 whisper 不可信，返回空（不应到达此处，type_define 已不再识别非原版 whisper）
        return [];
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
                const item_name = extract_custom_name(jsonMsg.json.with[2].hover_event.components) ?? ('item.' + jsonMsg.json.with[2].hover_event.id.replaceAll(":", "."));
                const item_components = jsonMsg.json.with[2].hover_event.components;
                return [entity_structure(player_type, player_name, player_uuid), entity_structure(killer_type, killer_name, killer_uuid), entity_structure(item_type, item_name, item_components)]
            }
            return [entity_structure(player_type, player_name, player_uuid), entity_structure(killer_type, killer_name, killer_uuid)]
        }
        return [entity_structure(player_type, player_name, player_uuid)];
    }

    if (type === 'chat') {
        return [jsonMsg.json.extra[1].extra];
    }

    if (type === 'tpa') {
        const extras = (jsonMsg.json && jsonMsg.json.extra) || [];
        const tpa_info = detect_tpa_in_extras(extras);
        if (tpa_info) {
            // 尝试从文本中提取请求者名称
            const flat_text = text_define(jsonMsg) || '';
            // 常见格式："xxx 请求传送到你的位置" / "xxx 请求你传送到 TA 的位置"
            const requester_match = flat_text.match(/^(\S+)\s+请求/);
            if (requester_match) {
                tpa_info.requester = requester_match[1];
            }
            return [tpa_info];
        }
        return [];
    }

    return [];
}

function extract_custom_name(itemData) {
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
    } catch (e) {
        throw new Error('error when handling group msg', jsonMsg, e);
    }
}

/**
 * 从原版 whisper 消息中提取发送者玩家名和私聊文本。
 * 仅处理 translate === 'commands.message.display.incoming' 的消息。
 *
 * @param {object} jsonMsg - mineflayer 的 jsonMsg 对象
 * @returns {{ player_name: string, whisper_text: string } | null}
 */
function extract_whisper_info(jsonMsg) {
    const translate = jsonMsg.translate || (jsonMsg.json && jsonMsg.json.translate);
    if (translate !== 'commands.message.display.incoming') return null;

    try {
        const withArr = jsonMsg.json && jsonMsg.json.with;
        if (!Array.isArray(withArr) || withArr.length < 2) return null;

        const player_name = withArr[0] && withArr[0].hover_event && withArr[0].hover_event.name;
        if (typeof player_name !== 'string' || !player_name) return null;

        // with[1] 是私聊内容，可能是字符串或对象
        let whisper_text = '';
        const content = withArr[1];
        if (typeof content === 'string') {
            whisper_text = content;
        } else if (content && typeof content === 'object') {
            whisper_text = content.text || '';
            if (Array.isArray(content.extra)) {
                for (const part of content.extra) {
                    whisper_text += (typeof part === 'string') ? part : (part.text || '');
                }
            }
        }

        return { player_name, whisper_text: whisper_text.trim() };
    } catch {
        return null;
    }
}

/**
 * 从非原版 chat 消息中提取发信人名与聊天文本。
 * 适用于 type_define 返回 'chat' 的消息（extras[0].click_event.command 以 '/msg ' 开头）。
 *
 * @param {object} jsonMsg - mineflayer 的 jsonMsg 对象
 * @returns {{ sender_name: string, chat_text: string } | null}
 */
function extract_chat_info(jsonMsg) {
    try {
        const extras = (jsonMsg.json && jsonMsg.json.extra) || [];
        const first = extras[0];
        if (!first) return null;

        // 从 click_event.command 提取发信人：/msg <player_name>
        let sender_name = null;
        const cmd = first.click_event && first.click_event.command;
        if (typeof cmd === 'string' && cmd.startsWith('/msg ')) {
            sender_name = cmd.slice(5).trim();
        }

        // 提取聊天文本：extras[1] 及之后的内容
        let chat_text = '';
        for (let i = 1; i < extras.length; i++) {
            const item = extras[i];
            if (item.extra) {
                for (const sub of item.extra) {
                    if (typeof sub === 'string') { chat_text += sub; continue; }
                    chat_text += sub.text || sub[''] || '';
                }
            } else {
                chat_text += item.text || item[''] || '';
            }
        }

        if (!sender_name) return null;

        return { sender_name, chat_text: chat_text.trim() };
    } catch {
        return null;
    }
}

module.exports = { handle_message, group_msg_handler, extract_whisper_info, extract_chat_info };
