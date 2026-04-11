/**
 * Whisper 指令鉴权与解析
 *
 * 来源无关：消息可信性由上游 messageHandler 的 extract_whisper_info 保证。
 */

const PERMISSION_ADMIN = 'admin';
const PERMISSION_USER = 'user';

/**
 * 判断玩家权限等级。
 *
 * @param {string} playerName
 * @param {object} config - 须含 admin_players, user_players 数组
 * @returns {'admin' | 'user' | null}
 */
function getPermissionLevel(playerName, config) {
    const adminList = Array.isArray(config.admin_players) ? config.admin_players : [];
    const userList = Array.isArray(config.user_players) ? config.user_players : [];

    if (adminList.includes(playerName)) return PERMISSION_ADMIN;
    if (userList.includes(playerName)) return PERMISSION_USER;
    return null;
}

/**
 * 处理 whisper 消息，进行鉴权和指令解析。
 *
 * @param {string} playerName  - 发送者玩家名（从原版 whisper 的 hover_event 中提取）
 * @param {string} rawText     - whisper 的原始文本内容
 * @param {object} config      - 须含 admin_players, user_players, whisper_command_prefix
 * @returns {{ player_name: string, permission_level: string, command: string, args: string[], raw_text: string } | null}
 */
function handleWhisperCommand(playerName, rawText, config) {
    if (!playerName || typeof playerName !== 'string') return null;
    if (!rawText || typeof rawText !== 'string') return null;

    const prefix = (typeof config.whisper_command_prefix === 'string' && config.whisper_command_prefix.trim())
        ? config.whisper_command_prefix.trim()
        : '#';

    const text = rawText.trim();

    // 必须以指令前缀开头
    if (!text.startsWith(prefix)) return null;

    // 鉴权
    const level = getPermissionLevel(playerName, config);
    if (!level) return null;

    // 解析指令：去掉前缀后按空格分割
    const commandBody = text.slice(prefix.length).trim();
    if (!commandBody) return null;

    const parts = commandBody.split(/\s+/);
    const command = parts[0];
    const args = parts.slice(1);

    return {
        player_name: playerName,
        permission_level: level,
        command,
        args,
        raw_text: text,
    };
}

module.exports = {
    PERMISSION_ADMIN,
    PERMISSION_USER,
    getPermissionLevel,
    handleWhisperCommand,
};
