/**
 * 容器交互工具模块
 * 
 * 用于 Home 容器界面的解析和交互操作。
 * 支持 1.20.5+ 的 componentMap 和旧版 NBT 格式。
 */

const nbt = require('prismarine-nbt');

/**
 * 延迟执行
 * @param {number} ms - 毫秒数
 * @returns {Promise<void>}
 */
function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * 递归提取 Minecraft JSON 文本组件的纯文本。
 * 支持两种格式：
 * 1. 已简化格式: { text: 'hello', extra: [{ text: 'world' }] }
 * 2. prismarine-nbt 格式: { type: 'compound', value: { text: { type: 'string', value: '...' } } }
 * 
 * @param {any} obj - 文本组件对象
 * @returns {string}
 */
function extractTextComponent(obj) {
    if (!obj) return '';
    if (typeof obj === 'string') return obj;
    if (typeof obj === 'number') return String(obj);

    // 如果是 NBT 格式（有 type/value），先简化
    if (obj.type !== undefined && obj.value !== undefined) {
        try {
            obj = nbt.simplify(obj);
        } catch (_) { }
    }

    // 简化后可能变成字符串
    if (typeof obj === 'string') return obj;

    let result = '';
    if (obj.text !== undefined) {
        result += String(obj.text);
    }
    if (obj.extra && Array.isArray(obj.extra)) {
        for (const e of obj.extra) {
            result += extractTextComponent(e);
        }
    }
    return result;
}

/**
 * 获取物品的显示名称/自定义名称。
 * 按优先级依次尝试：
 * 1. componentMap (1.20.5+)
 * 2. components 数组 (1.20.5+)
 * 3. customName getter (旧版 NBT)
 * 4. displayName / name 兜底
 * 
 * @param {object|null} item - 物品对象
 * @returns {string}
 */
function getItemLabel(item) {
    if (!item) {
        return '';
    }

    // 1. 尝试 componentMap (1.20.5+ 物品)
    if (item.componentMap && item.componentMap.size > 0) {
        const customNameComp = item.componentMap.get('custom_name') ||
            item.componentMap.get('minecraft:custom_name');
        if (customNameComp) {
            const data = customNameComp.data || customNameComp.value || customNameComp;
            const text = extractTextComponent(data);
            if (text && text.trim()) return text.trim();
        }

        // 也尝试 item_name 组件
        const itemNameComp = item.componentMap.get('item_name') ||
            item.componentMap.get('minecraft:item_name');
        if (itemNameComp) {
            const data = itemNameComp.data || itemNameComp.value || itemNameComp;
            const text = extractTextComponent(data);
            if (text && text.trim()) return text.trim();
        }
    }

    // 2. 尝试 components 数组 (1.20.5+)
    if (item.components && Array.isArray(item.components)) {
        for (const comp of item.components) {
            const typeName = comp.type || '';
            if (typeName === 'custom_name' || typeName === 'minecraft:custom_name') {
                const data = comp.data || comp.value;
                const text = extractTextComponent(data);
                if (text && text.trim()) return text.trim();
            }
        }
        for (const comp of item.components) {
            const typeName = comp.type || '';
            if (typeName === 'item_name' || typeName === 'minecraft:item_name') {
                const data = comp.data || comp.value;
                const text = extractTextComponent(data);
                if (text && text.trim()) return text.trim();
            }
        }
    }

    // 3. 尝试内置的 customName getter (旧版 NBT)
    if (item.customName) {
        const cn = item.customName;
        if (typeof cn === 'string') {
            try {
                const parsed = JSON.parse(cn);
                if (parsed && typeof parsed === 'object') {
                    const text = extractTextComponent(parsed);
                    if (text) return text;
                }
            } catch (_) { }
            return cn;
        }
        if (typeof cn === 'object' && cn.toString) {
            return cn.toString();
        }
    }

    return item.displayName || item.name || '';
}

/**
 * 检查物品是否为导航按钮（翻页箭头等）。
 * 
 * @param {object|null} item - 物品对象
 * @returns {boolean}
 */
function isNavigationItem(item) {
    const label = getItemLabel(item).toLowerCase();
    const name = (item && item.name ? item.name.toLowerCase() : '');
    return (
        name.includes('arrow') ||
        label.includes('next') ||
        label.includes('prev') ||
        label.includes('page') ||
        label.includes('back') ||
        label.includes('下一页') ||
        label.includes('上一页')
    );
}

/**
 * 检查物品是否为 GUI 边框/装饰元素（玻璃板等）。
 * 
 * @param {object|null} item - 物品对象
 * @returns {boolean}
 */
function isBorderItem(item) {
    if (!item) return false;
    const name = item.name || '';
    return name.includes('stained_glass_pane') || name === 'glass_pane';
}

/**
 * 检查物品是否为 GUI 功能按钮（非 home 条目）。
 * 常见的 CMI home GUI 元素：
 * - totem_of_undying: "死亡位置"
 * - player_head: "关闭" 按钮或玩家信息
 * - bone: 导航/设置
 * - barrier: 关闭/取消
 * 
 * @param {object|null} item - 物品对象
 * @returns {boolean}
 */
function isGuiButton(item) {
    if (!item) return false;
    const name = item.name || '';
    const label = getItemLabel(item);

    // 已知的功能物品类型
    if (name === 'player_head' || name === 'barrier') return true;
    if (name === 'totem_of_undying') return true;

    // 已知的功能标签
    const lowerLabel = label.toLowerCase();
    if (lowerLabel === '关闭' || lowerLabel === 'close') return true;
    if (lowerLabel === '死亡位置' || lowerLabel === 'death location') return true;

    return false;
}

/**
 * 从容器窗口中提取所有 home 名称。
 * 
 * @param {object} window - mineflayer 窗口对象
 * @returns {string[]} home 名称数组
 */
function extractHomes(window) {
    const homes = [];
    if (!window || !Array.isArray(window.slots)) {
        return homes;
    }

    // 仅扫描容器部分，排除玩家背包（36格）
    const totalSlots = window.slots.length;
    const containerSlots = Math.max(0, totalSlots - 36);

    for (let idx = 0; idx < containerSlots; idx++) {
        const item = window.slots[idx];
        if (!item) {
            continue;
        }

        // 跳过边框装饰物品
        if (isBorderItem(item)) {
            continue;
        }

        // 跳过 GUI 功能按钮
        if (isGuiButton(item)) {
            continue;
        }

        // 跳过导航按钮
        if (isNavigationItem(item)) {
            continue;
        }

        const label = getItemLabel(item);
        if (label) {
            homes.push(label);
        }
    }
    return homes;
}

/**
 * 查找"下一页"按钮的格子索引。
 * 
 * @param {object} window - mineflayer 窗口对象
 * @returns {number|null} 格子索引，未找到返回 null
 */
function findNextSlot(window) {
    if (!window || !Array.isArray(window.slots)) {
        return null;
    }
    for (let index = 0; index < window.slots.length; index++) {
        const item = window.slots[index];
        if (!item) continue;
        const label = getItemLabel(item).toLowerCase();
        const name = item.name ? item.name.toLowerCase() : '';
        if (name.includes('arrow') && (label.includes('next') || label.includes('下一页'))) {
            return index;
        }
    }
    return null;
}

/**
 * 查找指定 home 的格子索引。
 * 
 * @param {object} window - mineflayer 窗口对象
 * @param {string} homeName - 要查找的 home 名称
 * @returns {number|null} 格子索引，未找到返回 null
 */
function findHomeSlot(window, homeName) {
    if (!window || !Array.isArray(window.slots)) return null;

    const totalSlots = window.slots.length;
    const containerSlots = Math.max(0, totalSlots - 36);

    for (let idx = 0; idx < containerSlots; idx++) {
        const item = window.slots[idx];
        if (!item || isBorderItem(item) || isGuiButton(item) || isNavigationItem(item)) continue;

        const label = getItemLabel(item);
        if (label === homeName) {
            return idx;
        }
    }

    return null;
}

/**
 * 等待容器窗口打开。
 * 
 * @param {object} bot - mineflayer bot 实例
 * @param {number} timeoutMs - 超时毫秒数
 * @returns {Promise<object>} 打开的窗口对象
 */
function waitForWindowOpen(bot, timeoutMs) {
    return new Promise((resolve, reject) => {
        let timer = null;
        const onOpen = (window) => {
            cleanup();
            resolve(window);
        };
        const onTimeout = () => {
            cleanup();
            reject(new Error('Timed out waiting for window to open.'));
        };
        const cleanup = () => {
            if (timer) {
                clearTimeout(timer);
            }
            bot.removeListener('windowOpen', onOpen);
        };
        bot.once('windowOpen', onOpen);
        timer = setTimeout(onTimeout, timeoutMs);
    });
}

/**
 * 列出所有 home（支持翻页）。
 * 
 * @param {object} bot - mineflayer bot 实例
 * @returns {Promise<string[]>} home 名称数组
 */
async function listHomes(bot) {
    bot.chat('/home');
    const window = await waitForWindowOpen(bot, 5000);
    const homes = new Set(extractHomes(window));

    // 处理翻页
    const nextSlot = findNextSlot(window);
    if (nextSlot !== null) {
        bot.clickWindow(nextSlot, 0, 0);
        await delay(800);
        const updatedWindow = bot.currentWindow || window;
        for (const home of extractHomes(updatedWindow)) {
            homes.add(home);
        }
    }

    if (bot.currentWindow) {
        bot.closeWindow(bot.currentWindow);
    }

    return [...homes];
}

/**
 * 传送到指定 home。
 * 
 * @param {object} bot - mineflayer bot 实例
 * @param {string} homeName - 目标 home 名称
 * @returns {Promise<boolean>} 成功返回 true
 * @throws {Error} 找不到 home 时抛出异常
 */
async function tpToHome(bot, homeName) {
    bot.chat('/home');
    const window = await waitForWindowOpen(bot, 5000);

    // 在当前页查找
    let homeSlot = findHomeSlot(window, homeName);

    // 如果未找到，尝试翻页
    if (homeSlot === null) {
        const nextSlot = findNextSlot(window);
        if (nextSlot !== null) {
            bot.clickWindow(nextSlot, 0, 0);
            await delay(800);
            const updatedWindow = bot.currentWindow || window;
            homeSlot = findHomeSlot(updatedWindow, homeName);
        }
    }

    if (homeSlot === null) {
        if (bot.currentWindow) {
            bot.closeWindow(bot.currentWindow);
        }
        throw new Error(`Home not found: ${homeName}`);
    }

    // 点击传送
    bot.clickWindow(homeSlot, 0, 0);
    await delay(500);

    return true;
}

module.exports = {
    delay,
    extractTextComponent,
    getItemLabel,
    isNavigationItem,
    isBorderItem,
    isGuiButton,
    extractHomes,
    findNextSlot,
    findHomeSlot,
    waitForWindowOpen,
    listHomes,
    tpToHome,
};
