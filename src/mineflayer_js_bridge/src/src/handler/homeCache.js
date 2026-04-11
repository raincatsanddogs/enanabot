/**
 * Home 缓存管理器
 *
 * 维护一份 Home 名称列表的内存缓存，并持久化到 JSON 文件。
 * 仅在首次启动且无持久化文件时触发一次 GUI 同步（由外部调用 refreshFromGUI 完成），
 * 之后所有读取操作均从内存返回。
 *
 * 用法：
 *   const homeCache = require('./homeCache');
 *   homeCache.load();                       // 启动时从磁盘恢复
 *   const list = homeCache.getList();       // 读取（纯内存）
 *   homeCache.addHome('myhouse');           // 新增并写盘
 *   homeCache.removeHome('myhouse');        // 删除并写盘
 *   homeCache.setFromGUI(['a', 'b']);       // GUI 全量同步后写盘
 */

const fs = require('fs');
const path = require('path');

// 持久化路径：项目根/configs/home_cache.json
const CACHE_FILE = path.join(__dirname, '../../../../../../configs/home_cache.json');

/** @type {Set<string>} */
let _homes = new Set();

/** 首次 load 时是否成功从磁盘读取到了数据 */
let _loaded_from_disk = false;

// ===== 持久化 =====

/**
 * 从磁盘加载缓存。启动时调用一次。
 * 文件不存在或格式非法时视为空缓存（需要 GUI 刷新）。
 */
function load() {
    try {
        if (!fs.existsSync(CACHE_FILE)) {
            _homes = new Set();
            _loaded_from_disk = false;
            return;
        }
        const raw = fs.readFileSync(CACHE_FILE, 'utf-8');
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed.homes)) {
            _homes = new Set(
                parsed.homes
                    .filter((h) => typeof h === 'string' && h.trim())
                    .map((h) => h.trim())
            );
            _loaded_from_disk = true;
        } else {
            _homes = new Set();
            _loaded_from_disk = false;
        }
    } catch (e) {
        console.error(`[homeCache] load 失败: ${e.message || e}`);
        _homes = new Set();
        _loaded_from_disk = false;
    }
}

/**
 * 将当前内存缓存写盘。
 */
function save() {
    try {
        const dir = path.dirname(CACHE_FILE);
        if (!fs.existsSync(dir)) {
            fs.mkdirSync(dir, { recursive: true });
        }
        const payload = {
            homes: [..._homes],
            updated_at: new Date().toISOString(),
        };
        fs.writeFileSync(CACHE_FILE, JSON.stringify(payload, null, 2), 'utf-8');
    } catch (e) {
        console.error(`[homeCache] save 失败: ${e.message || e}`);
    }
}

// ===== 查询 =====

/**
 * 返回当前缓存中的 Home 名称列表（纯内存读取）。
 * @returns {string[]}
 */
function getList() {
    return [..._homes];
}

/**
 * 判断缓存中是否包含指定 home。
 * @param {string} name
 * @returns {boolean}
 */
function hasHome(name) {
    if (typeof name !== 'string') return false;
    return _homes.has(name.trim());
}

/**
 * 判断是否需要首次 GUI 同步。
 * 仅当启动后没有成功从磁盘加载到任何数据时返回 true。
 * @returns {boolean}
 */
function needsRefresh() {
    return !_loaded_from_disk;
}

// ===== 修改 =====

/**
 * 添加一个 Home 并写盘。
 * @param {string} name
 */
function addHome(name) {
    if (typeof name !== 'string' || !name.trim()) return;
    _homes.add(name.trim());
    save();
}

/**
 * 删除一个 Home 并写盘。
 * @param {string} name
 */
function removeHome(name) {
    if (typeof name !== 'string' || !name.trim()) return;
    _homes.delete(name.trim());
    save();
}

/**
 * 从 GUI 全量同步后批量写入（覆盖）。
 * @param {string[]} names
 */
function setFromGUI(names) {
    if (!Array.isArray(names)) return;
    _homes = new Set(
        names
            .filter((h) => typeof h === 'string' && h.trim())
            .map((h) => h.trim())
    );
    _loaded_from_disk = true;
    save();
}

/**
 * 强制标记缓存需要刷新（例如重启后强制同步一次）。
 */
function invalidate() {
    _loaded_from_disk = false;
}

module.exports = {
    load,
    save,
    getList,
    hasHome,
    needsRefresh,
    addHome,
    removeHome,
    setFromGUI,
    invalidate,
};
