// config.js
const fs = require('fs');
const yaml = require('js-yaml');
const path = require('path');

let config = {};
let accounts = {};
let settings = {};

try {
  // 1. 拼接正确的文件路径
  const accountsPath = path.join(__dirname, '../../../../../configs/accounts.yaml');
  const settingsPath = path.join(__dirname, '../../../../../configs/settings.yaml');
  //
  // 2. 读取文件内容 (指定 utf8 编码)
  const accountsContents = fs.readFileSync(accountsPath, 'utf8');
  const settingsContents = fs.readFileSync(settingsPath, 'utf8');
  
  // 3. 使用 yaml.load() 解析为 JavaScript 对象
  accounts = yaml.load(accountsContents);
  settings = yaml.load(settingsContents);
  
} catch (e) {
  console.error('读取或解析 YAML 配置文件失败:', e);
}

config = {...accounts, ...settings};

if (Array.isArray(config.skin)) {
  config.skin.forEach(server => {

    server.sessionServer = server.sessionServer || server.url + '/sessionserver';
    server.authServer = server.authServer || server.url + '/authserver';

  });
}

if (Array.isArray(config.account)) {
  config.account.forEach(account => {

    account.authType = account.authType === 'third' ? 'mojang' : account.authType;

  });
}

const connectConfig =
  config.connect && typeof config.connect === 'object' ? config.connect : {};

function normalizeIdList(listLike) {
  if (!Array.isArray(listLike)) {
    return [];
  }

  return listLike.map((item) => {
    if (typeof item === 'string' && /^\d+$/.test(item)) {
      return Number(item);
    }
    return item;
  });
}

// send_group is the only accepted group whitelist field.
config.send_group = normalizeIdList(
  config.send_group
  ?? connectConfig.send_group
  ?? connectConfig.sendGroup
);
config.ignore_user = normalizeIdList(
  config.ignore_user ?? connectConfig.ignore_user ?? connectConfig.ignoreUser
);
config.forward_prefix = (
  config.forward_prefix
  ?? connectConfig.forward_prefix
  ?? connectConfig.forwardPrefix
  ?? '[群聊]>>'
).toString().trim() || '[群聊]>>';

// Whisper 指令鉴权配置
config.admin_players = Array.isArray(config.admin_players)
  ? config.admin_players
  : Array.isArray(connectConfig.admin_players)
    ? connectConfig.admin_players
    : [];

config.user_players = Array.isArray(config.user_players)
  ? config.user_players
  : Array.isArray(connectConfig.user_players)
    ? connectConfig.user_players
    : [];

config.guest_players = Array.isArray(config.guest_players)
  ? config.guest_players
  : Array.isArray(connectConfig.guest_players)
    ? connectConfig.guest_players
    : Array.isArray(connectConfig.guestPlayers)
      ? connectConfig.guestPlayers
      : [];

config.whisper_command_prefix = (
  config.whisper_command_prefix
  ?? connectConfig.whisper_command_prefix
  ?? connectConfig.whisperCommandPrefix
  ?? '#'
).toString().trim() || '#';

// 4. 导出对象
module.exports = config;