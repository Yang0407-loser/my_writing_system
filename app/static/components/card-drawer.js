// 抽卡模式 UI 组件

import * as API from '../js/api.js';

const state = window.WriterState;

/**
 * 开始抽卡
 * @param {string} step - 步骤标识
 * @param {object} context - 当前上下文
 */
export async function drawCards(step, context = {}) {
    state.currentStep = step;
    state.cards = [];
    const container = document.getElementById('card-area');
    if (container) {
        container.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-secondary)">抽卡中...</div>';
    }
    try {
        const data = await API.request(`${state.apiBase}/api/cards/draw`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ step, context, num_cards: 4 }),
        });
        state.cards = data.cards || [];
        renderCards();
    } catch (e) {
        if (container) {
            container.innerHTML = `<div style="text-align:center;padding:40px;color:var(--red)">抽卡失败: ${e.message}</div>`;
        }
    }
}

/** 渲染卡片列表 */
export function renderCards() {
    const container = document.getElementById('card-area');
    if (!container) return;

    if (!state.cards.length) {
        container.innerHTML = '';
        return;
    }

    container.innerHTML = `
        <div class="cards-container">
            ${state.cards.map((card, i) => `
                <div class="card ${card.is_adopted ? 'selected' : ''}"
                     onclick="adoptCard(${i})" id="card-${i}">
                    <div class="card-header">
                        <span class="card-title">${escHtml(card.title)}</span>
                        ${card.quality_score ? `<span class="card-score">★ ${card.quality_score}</span>` : ''}
                    </div>
                    <div class="card-summary">${escHtml(card.summary || '')}</div>
                    ${(card.highlights || []).length ? `
                    <div class="card-tags">
                        ${card.highlights.map(t => `<span class="card-tag">${escHtml(t)}</span>`).join('')}
                    </div>` : ''}
                    <div class="card-actions">
                        <button class="primary small" onclick="event.stopPropagation();adoptCard(${i})">采纳</button>
                        <button class="small" onclick="event.stopPropagation();modifyCard(${i})">修改</button>
                        <button class="small" onclick="event.stopPropagation();expandCard(${i})">详情</button>
                    </div>
                </div>
            `).join('')}
        </div>
        <div style="text-align:center;padding:12px;display:flex;gap:8px;justify-content:center">
            <button onclick="redrawAllCards()">重新抽卡</button>
            <button onclick="skipCards()">跳过此步</button>
        </div>
    `;
}

/** 采纳卡片 */
window.adoptCard = function(index) {
    const card = state.cards[index];
    if (!card) return;
    // 取消其他卡片的选中
    state.cards.forEach(c => c.is_adopted = false);
    card.is_adopted = true;
    // 加入已采纳历史
    if (!state.adoptedCards) state.adoptedCards = [];
    state.adoptedCards.push({ ...card, adoptedAt: new Date().toISOString() });
    renderCards();
    // 触发回调
    if (window.onCardAdopted) window.onCardAdopted(card);
};

/** 基于卡片修改 */
window.modifyCard = async function(index) {
    const instruction = prompt('请输入修改要求：');
    if (!instruction) return;
    const card = state.cards[index];
    try {
        const data = await API.request(`${state.apiBase}/api/cards/redraw`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                step: state.currentStep,
                context: { topic: state.topic, world_setting: state.worldSetting },
                card_index: index,
                user_feedback: instruction,
            }),
        });
        if (data.card) {
            state.cards[index] = data.card;
            renderCards();
        }
    } catch (e) {
        alert('修改失败: ' + e.message);
    }
};

/** 展开卡片详情 */
window.expandCard = function(index) {
    const card = state.cards[index];
    if (!card) return;
    const detail = JSON.stringify(card.content, null, 2);
    const modal = document.createElement('div');
    modal.className = 'modal-overlay';
    modal.onclick = (e) => { if (e.target === modal) modal.remove(); };
    modal.innerHTML = `
        <div class="modal" style="max-width:600px">
            <div class="modal-title">${escHtml(card.title)}</div>
            <pre style="font-size:12px;line-height:1.6;white-space:pre-wrap;max-height:60vh;overflow-y:auto">${escHtml(detail)}</pre>
            <div class="modal-actions">
                <button onclick="this.closest('.modal-overlay').remove()">关闭</button>
                <button class="primary" onclick="adoptCard(${index});this.closest('.modal-overlay').remove()">采纳</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
};

window.redrawAllCards = function() {
    drawCards(state.currentStep, {
        topic: state.topic,
        world_setting: state.worldSetting,
        story_synopsis: state.storySynopsis,
    });
};

window.skipCards = function() {
    state.cards = [];
    renderCards();
    if (window.onCardsSkipped) window.onCardsSkipped();
};

// ── 灵感库侧边栏 ──

export async function toggleInspirationSidebar() {
    let sidebar = document.getElementById('inspiration-sidebar');
    if (!sidebar) {
        sidebar = document.createElement('div');
        sidebar.id = 'inspiration-sidebar';
        sidebar.className = 'inspiration-sidebar';
        document.body.appendChild(sidebar);
        await loadInspirations(sidebar);
    }
    sidebar.classList.toggle('open');
}

async function loadInspirations(sidebar) {
    try {
        const data = await API.request(`${state.apiBase}/api/cards/inspirations`);
        const categories = data.inspirations || {};
        let html = '<div class="panel-header">灵感库</div>';
        for (const [cat, items] of Object.entries(categories)) {
            if (cat === 'categories') continue;
            if (!Array.isArray(items)) continue;
            const catLabel = { world_setting: '世界观', protagonist: '主角设定', plot_twist: '反转套路', climax: '爽点模板' }[cat] || cat;
            html += `<div class="panel-section">
                <div style="font-weight:600;font-size:13px;margin-bottom:6px">${catLabel}</div>
                ${items.map(item => `
                    <div class="inspiration-item" style="padding:6px 0;cursor:pointer;font-size:12px;border-bottom:1px solid rgba(255,255,255,0.04)"
                         onclick="useInspiration('${cat}', '${escHtml(item.name)}')"
                         title="${escHtml(item.description)}">
                        <span style="color:var(--blue)">${escHtml(item.name)}</span>
                        <span style="color:var(--text-muted);margin-left:6px;font-size:11px">${escHtml(item.description).slice(0, 40)}</span>
                    </div>
                `).join('')}
            </div>`;
        }
        sidebar.innerHTML = html;
    } catch (e) {
        sidebar.innerHTML = '<div style="padding:12px;color:var(--red)">加载失败</div>';
    }
}

window.toggleInspirationSidebar = toggleInspirationSidebar;
window.useInspiration = function(category, name) {
    // 将灵感注入为user_requirement重新抽卡
    drawCards(state.currentStep, {
        topic: state.topic,
        world_setting: state.worldSetting,
        story_synopsis: state.storySynopsis,
    }, undefined, `参考「${name}」套路来设计`);
};

function escHtml(s) {
    if (!s) return '';
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// 键盘快捷键
document.addEventListener('keydown', (e) => {
    if (!state.cards.length) return;
    const num = parseInt(e.key);
    if (num >= 1 && num <= Math.min(5, state.cards.length)) {
        window.adoptCard(num - 1);
    } else if (e.key === 'r' || e.key === 'R') {
        window.redrawAllCards();
    } else if (e.key === 's' || e.key === 'S') {
        window.skipCards();
    }
});
