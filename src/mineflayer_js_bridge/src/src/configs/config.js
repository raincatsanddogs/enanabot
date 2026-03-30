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

// Support both top-level and connect.* fields for backward compatibility.
config.ignore_group = normalizeIdList(
  config.ignore_group ?? connectConfig.ignore_group ?? connectConfig.ignoreGroup
);
config.ignore_user = normalizeIdList(
  config.ignore_user ?? connectConfig.ignore_user ?? connectConfig.ignoreUser
);

// 4. 导出对象
module.exports = config;