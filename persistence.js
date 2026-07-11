/**
 * persistence.js — 前端持久化模块
 *
 * 接口写死，只暴露三个函数。任何前端升级不得修改此文件。
 * 页面加载时自动自检，失败在 console 打红字。
 */
(function () {
  'use strict';

  const LS_KEY = 'writing_ui_v3_state';
  const LS_KEY_OLD = 'writing_ui_v2_state';

  // ═══ 自检 ═══
  (function selfCheck() {
    try {
      const testKey = '__persistence_self_check__';
      const testVal = { ts: Date.now() };
      localStorage.setItem(testKey, JSON.stringify(testVal));
      const readBack = JSON.parse(localStorage.getItem(testKey));
      localStorage.removeItem(testKey);
      if (!readBack || readBack.ts !== testVal.ts) {
        console.error('[PERSISTENCE] self-check FAILED: read/write mismatch');
      }
    } catch (e) {
      console.error('[PERSISTENCE] self-check FAILED:', e.message);
    }
  })();

  // ═══ 公开接口 ═══

  window.Persistence = {
    save(state) {
      try {
        state.savedAt = Date.now();
        localStorage.setItem(LS_KEY, JSON.stringify(state));
      } catch (e) {
        console.error('[PERSISTENCE] save failed:', e.message);
      }
    },

    restore() {
      try {
        // 新 key → 旧 key 迁移
        let raw = localStorage.getItem(LS_KEY);
        if (!raw) {
          raw = localStorage.getItem(LS_KEY_OLD);
          if (raw) {
            localStorage.setItem(LS_KEY, raw);
            localStorage.removeItem(LS_KEY_OLD);
          }
        }
        if (!raw) return null;

        const s = JSON.parse(raw);
        if (Date.now() - (s.savedAt || 0) > 30 * 60 * 1000) {
          this.clear();
          return null;
        }
        return s;
      } catch (e) {
        console.error('[PERSISTENCE] restore failed:', e.message);
        return null;
      }
    },

    clear() {
      try {
        localStorage.removeItem(LS_KEY);
        localStorage.removeItem(LS_KEY_OLD);
      } catch (e) {}
    },
  };

  // ═══ 页面关闭前强制写入 ═══
  let _dirty = false;
  window.addEventListener('beforeunload', function () {
    if (_dirty && window._persistenceFlush) {
      window._persistenceFlush();
    }
  });

  // 标记脏位，供 beforeunload 使用
  window.Persistence.markDirty = function () {
    _dirty = true;
  };
  window.Persistence.markClean = function () {
    _dirty = false;
  };
})();
