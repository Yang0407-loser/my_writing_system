// 规则管理面板

import * as API from '../js/api.js';

export function initRulesPanel() {
    renderRulesList();
}

export async function renderRulesList() {
    const container = document.getElementById('rules-panel');
    if (!container) return;

    try {
        const data = await API.listRules();
        const rules = data.rules || [];
        if (!rules.length) {
            container.innerHTML = '<div style="padding:8px;font-size:12px;color:var(--text-muted)">暂无规则，点击"管理"添加</div>';
            return;
        }
        container.innerHTML = rules.slice(0, 5).map(r => {
            const priorityClass = r.priority >= 8 ? 'high' : (r.priority >= 5 ? 'mid' : 'low');
            const typeLabel = { style: '风格', plot: '剧情', character: '角色', dialogue: '对话', global: '全局', custom: '自定义' }[r.type] || r.type;
            return `
                <div class="rule-item">
                    <span class="rule-priority ${priorityClass}">${r.priority}</span>
                    <span class="rule-content" title="${escHtml(r.content)}">${escHtml(r.name)}</span>
                    <span class="rule-type">${typeLabel}</span>
                </div>`;
        }).join('');
        if (rules.length > 5) {
            container.innerHTML += `<div style="padding:4px 12px;font-size:11px;color:var(--text-muted)">... 还有 ${rules.length - 5} 条规则</div>`;
        }
    } catch (e) {
        container.innerHTML = '<div style="padding:8px;font-size:12px;color:var(--red)">加载失败</div>';
    }
}

export async function openRulesModal() {
    // 简单实现: 弹窗管理规则
    const data = await API.listRules();
    const rules = data.rules || [];
    const html = `
    <div class="modal-overlay" id="rules-modal-overlay" onclick="if(event.target===this)this.remove()">
        <div class="modal" style="min-width:500px">
            <div class="modal-title">规则管理</div>
            <div style="max-height:50vh;overflow-y:auto">
                ${rules.map(r => `
                    <div class="rule-item" style="padding:8px 0">
                        <span class="rule-priority ${r.priority >= 8 ? 'high' : (r.priority >= 5 ? 'mid' : 'low')}">${r.priority}</span>
                        <div style="flex:1">
                            <div style="font-weight:600;font-size:13px">${escHtml(r.name)}</div>
                            <div style="font-size:11px;color:var(--text-secondary)">${escHtml(r.content)}</div>
                        </div>
                        <span class="rule-type">${r.type}</span>
                        <button class="small" onclick="toggleRule('${r.id}', ${!r.enabled})" style="margin-left:8px">${r.enabled ? '禁用' : '启用'}</button>
                    </div>
                `).join('')}
            </div>
            <div class="modal-actions">
                <button onclick="document.getElementById('rules-modal-overlay').remove()">关闭</button>
            </div>
        </div>
    </div>`;
    const overlay = document.createElement('div');
    overlay.innerHTML = html;
    document.body.appendChild(overlay.firstElementChild);
}

function escHtml(s) {
    if (!s) return '';
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// Export to window
window.initRulesPanel = initRulesPanel;
window.openRulesModal = openRulesModal;
window.renderRulesList = renderRulesList;
