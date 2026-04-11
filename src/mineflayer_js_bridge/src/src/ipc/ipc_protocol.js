/**
 * 统一 IPC 协议层
 *
 * 所有 Py ↔ JS 通信都遵循以下 envelope 格式：
 * { "action": string, "timestamp": ISO8601, "data": object }
 */

// ===== Action 常量 =====

// Py → JS
const ACTION_QQ_MESSAGE = 'qq_message';
const ACTION_WHISPER_REPLY = 'whisper_reply';
const ACTION_DELEGATE_COMMAND = 'delegate_command';

// JS → Py
const ACTION_MC_MESSAGE = 'mc_message';
const ACTION_WHISPER_COMMAND = 'whisper_command';
const ACTION_PLAYER_LIST = 'player_list';
const ACTION_TPA_NOTIFICATION = 'tpa_notification';
const ACTION_TPA_REQUEST_DETECTED = 'tpa_request_detected';
const ACTION_DELEGATE_RESULT = 'delegate_result';

/**
 * 编码一条 IPC 消息为 JSON 行（含换行符）。
 *
 * @param {string} action - action 类型
 * @param {object} data   - 载荷
 * @returns {string} JSON line（含尾部 \n）
 */
function encode(action, data) {
    const envelope = {
        action,
        timestamp: new Date().toISOString(),
        data: data || {},
    };
    return JSON.stringify(envelope) + '\n';
}

/**
 * 解码一行 JSON 为 IPC 消息。
 *
 * @param {string} line - 原始行文本
 * @returns {{ action: string, timestamp: string, data: object } | null}
 */
function decode(line) {
    const trimmed = (line || '').trim();
    if (!trimmed) return null;

    let parsed;
    try {
        parsed = JSON.parse(trimmed);
    } catch {
        return null;
    }

    if (!parsed || typeof parsed !== 'object') return null;
    if (typeof parsed.action !== 'string') return null;

    return {
        action: parsed.action,
        timestamp: parsed.timestamp || '',
        data: parsed.data || {},
    };
}

module.exports = {
    // Py → JS
    ACTION_QQ_MESSAGE,
    ACTION_WHISPER_REPLY,
    ACTION_DELEGATE_COMMAND,
    // JS → Py
    ACTION_MC_MESSAGE,
    ACTION_WHISPER_COMMAND,
    ACTION_PLAYER_LIST,
    ACTION_TPA_NOTIFICATION,
    ACTION_TPA_REQUEST_DETECTED,
    ACTION_DELEGATE_RESULT,
    // Functions
    encode,
    decode,
};
