// config.js
const fs = require('fs');
const yaml = require('js-yaml');
const path = require('path');

let config = {};

try {
  // 1. 拼接正确的文件路径
  const accountsPath = path.join(__dirname, '../../../../../../configs/accounts.yaml');
  const settingsPath = path.join(__dirname, '../../../../../../configs/settings.yaml');
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

config.skin.forEach(server => {

    server.sessionServer = server.sessionServer || server.url + '/sessionserver';
    server.authServer = server.authServer || server.url + '/authserver';

});

config.account.forEach(account => {

    account.authType = account.authType === 'third' ? 'mojang' : account.authType;

});

// 4. 导出对象
module.exports = config;