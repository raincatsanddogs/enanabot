const dns = require('dns').promises;
const { Resolver } = require('dns').promises;
const net = require('net');

/**
 * Resolve Minecraft SRV record for a hostname.
 * 自动回退到 Google / Cloudflare DNS 进行容错查询。
 */
async function resolveSrv(host) {
  // 1. 如果已经是 IP 或 localhost，无需解析 SRV
  if (net.isIP(host) || host === 'localhost') {
    return null;
  }

  const srvName = `_minecraft._tcp.${host}`;

  try {
    // 2. 优先尝试系统默认 DNS 解析
    const addresses = await dns.resolveSrv(srvName);
    if (addresses && addresses.length > 0) {
      return { host: addresses[0].name, port: addresses[0].port };
    }
  } catch (err) {
    // 3. 系统 DNS 失败（如不支持 SRV），进入备用 DNS 轮询
    const fallbackServers = [
      ['8.8.8.8', '8.8.4.4'], // Google
      ['1.1.1.1', '1.0.0.1']  // Cloudflare
    ];

    for (const servers of fallbackServers) {
      try {
        const resolver = new Resolver();
        resolver.setServers(servers);
        
        const addresses = await resolver.resolveSrv(srvName);
        if (addresses && addresses.length > 0) {
          return { host: addresses[0].name, port: addresses[0].port };
        }
      } catch (e) {
        // 当前备用 DNS 也失败了，忽略报错，continue 尝试下一个
        continue; 
      }
    }
  }

  // 4. 所有手段都失败了，返回 null
  return null;
}

module.exports = { resolveSrv };