// 伏笔管理面板

import * as API from '../js/api.js';

export function initForeshadowPanel() {
    renderForeshadowList();
}

export async function renderForeshadowList() {
    const container = document.getElementById('foreshadow-panel');
    if (!container) return;

    const taskId = window.WriterState?.taskId || '';
    try {
        const data = await API.listForeshadowings(taskId);
        const items = data.foreshadowings || [];
        if (!items.length) {
            container.innerHTML = '<div style="padding:8px;font-size:12px;color:var(--text-muted)">暂无伏笔</div>';
            return;
        }
        container.innerHTML = items.map(f => `
            <div class="foreshadow-item">
                <div class="foreshadow-name">${escHtml(f.name)}</div>
                <div class="foreshadow-desc">${escHtml(f.description).slice(0, 80)}</div>
                <div class="foreshadow-meta">
                    <span>埋设: 第${f.plant_chapter}章</span>
                    ${f.resolve_chapter ? `<span>回收: 第${f.resolve_chapter}章</span>` : '<span>未设定回收</span>'}
                    <span class="foreshadow-status ${f.status}">${statusLabel(f.status)}</span>
                    <span>重要性: ${'★'.repeat(Math.min(f.importance, 5))}</span>
                </div>
            </div>
        `).join('');
    } catch (e) {
        container.innerHTML = '<div style="padding:8px;font-size:12px;color:var(--red)">加载失败</div>';
    }
}

function statusLabel(s) {
    const map = { pending: '待埋设', planted: '已埋设', hinted: '已暗示', resolved: '已回收' };
    return map[s] || s;
}

function escHtml(s) {
    if (!s) return '';
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// Export to window
window.initForeshadowPanel = initForeshadowPanel;
window.renderForeshadowList = renderForeshadowList;
