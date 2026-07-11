// Vue App 初始化 + 全局状态 + 路由

import { state, resetWritingState, calcTotalWords } from './store.js';
import { saveState, loadState, clearState } from './persistence.js';
import { startPolling } from './stream.js';
import * as API from './api.js';
import { countChineseChars, flattenTreeToOutline, flatOutlineToTree, debounce } from './utils.js';

// 暴露到全局（方便组件访问）
window.WriterState = state;
window.WriterAPI = API;
window.WriterUtils = { countChineseChars, flattenTreeToOutline, flatOutlineToTree, debounce };

// ── 应用初始化 ──
document.addEventListener('DOMContentLoaded', () => {
    const restored = loadState(state);

    // 加载数据
    loadCharacters();
    loadRules();
    loadTaskHistory();

    if (restored && state.taskId) {
        // 恢复轮询
        state.statusText = '恢复连接中...';
        resumePolling();
    }

    // 自动保存
    setInterval(() => saveState(state), 5000);
    window.addEventListener('beforeunload', () => saveState(state));
});

// ── 全局函数 ──

window.startWriting = async function() {
    if (!state.topic.trim() && !state.referenceText.trim()) {
        alert('请输入主题或参考文本');
        return;
    }
    resetWritingState();
    state.statusText = '提交中...';

    const flatOutline = state.outlineTree.length
        ? flattenTreeToOutline(state.outlineTree)
        : [];

    try {
        const body = {
            topic: state.topic,
            reference_text: state.referenceText || state.topic,
            world_setting: state.worldSetting,
            story_synopsis: state.storySynopsis,
            style_profile: state.styleProfile || {},
            outline: flatOutline,
            characters: state.selectedCharIds.map(id =>
                state.libraryChars.find(c => c.id === id)
            ).filter(Boolean),
            target_words_per_section: state.globalWordLimit,
        };
        const resp = await API.startWriting(body);
        state.taskId = resp.task_id;
        state.statusText = '生成中...';
        beginPolling();
    } catch (e) {
        state.statusText = '提交失败';
        state.statusColor = '#ff4757';
        console.error(e);
    }
};

window.stopWriting = async function() {
    if (!state.taskId) return;
    try {
        await API.sendDecision(state.taskId, 'section', 'stop');
        state.isGenerating = false;
        state.statusText = '已停止';
    } catch (e) {
        console.error(e);
    }
};

window.newSession = function() {
    if (state.isGenerating && !confirm('正在生成中，确定要新建会话吗？')) return;
    clearState();
    resetWritingState();
    state.outlineTree = [];
    state.flatOutline = [];
    state.draftBlocks = [];
    state.taskId = '';
};

// ── 轮询管理 ──

let pollingHandle = null;

function beginPolling() {
    if (pollingHandle) pollingHandle.stop();

    state.draftBlocks = [];
    if (state.flatOutline.length) {
        for (const sec of state.flatOutline) {
            for (const sub of (sec.subsections || [])) {
                state.draftBlocks.push({
                    type: 'subsection',
                    title: `第${sec.section}节 · ${sub.title || '未命名'}`,
                    text: '',
                    wordCount: 0,
                    targetWords: sub.target_words || 2000,
                    section: sec.section,
                    subsection: sub.subsection,
                });
            }
        }
    } else {
        // 没有预设大纲时, 创建待填充的块
        state.draftBlocks.push({
            type: 'section',
            title: '正文',
            text: '',
            wordCount: 0,
            targetWords: state.globalWordLimit,
            section: 1,
            subsection: 1,
        });
    }

    pollingHandle = startPolling(state, {
        onStatusUpdate(data) {
            state.statusText = data.progress || data.status;
        },
        onSectionStart(section, subsection) {
            const idx = findBlockIdx(section, subsection);
            if (idx >= 0) state.generatingBlockIdx = idx;
        },
        onToken(token, section, subsection) {
            const idx = findBlockIdx(section, subsection);
            if (idx >= 0) {
                state.draftBlocks[idx].text += token;
                state.draftBlocks[idx].wordCount = countChineseChars(state.draftBlocks[idx].text);
            }
        },
        onSectionEnd(fullText, section, subsection) {
            const idx = findBlockIdx(section, subsection);
            if (idx >= 0) {
                state.draftBlocks[idx].text = fullText;
                state.draftBlocks[idx].wordCount = countChineseChars(fullText);
            }
            state.completedSections = Math.max(state.completedSections, section);
            state.generatingBlockIdx = -1;
            saveState(state);
        },
        onComplete(data) {
            state.statusText = '完成';
            state.statusColor = '#4ecca3';
            state.taskDone = true;
            saveState(state);
            loadTaskHistory();
        },
        onError(err) {
            state.statusText = '出错';
            state.statusColor = '#ff4757';
            state.taskDone = true;
        },
    });
}

function resumePolling() {
    state.statusText = '恢复中...';
    beginPolling();
}

function findBlockIdx(section, subsection) {
    return state.draftBlocks.findIndex(
        b => b.section === section && b.subsection === subsection
    );
}

// ── 数据加载 ──

async function loadCharacters() {
    try {
        const data = await API.listCharacters();
        state.libraryChars = data.characters || data || [];
    } catch (e) {
        // 忽略
    }
}

async function loadRules() {
    try {
        const data = await API.listRules();
        state.rules = data.rules || [];
    } catch (e) {
        // 忽略
    }
}

async function loadTaskHistory() {
    try {
        const data = await API.listTaskHistory(30);
        state.taskHistory = Array.isArray(data) ? data : (data.tasks || []);
    } catch (e) {
        // 忽略
    }
}

window.loadCharacters = loadCharacters;
window.loadRules = loadRules;
window.loadTaskHistory = loadTaskHistory;
window.calcTotalWords = calcTotalWords;

// ── 大纲操作 ──

window.importOutlineText = async function(text) {
    try {
        const data = await API.importOutline(text);
        const outline = data.outline || data;
        if (outline && outline.length) {
            state.outlineTree = flatOutlineToTree(outline);
            state.flatOutline = outline;
            saveState(state);
        }
    } catch (e) {
        alert('大纲导入失败: ' + e.message);
    }
};

window.generateWorldSetting = async function() {
    if (!state.topic.trim()) { alert('请先输入主题'); return; }
    state.statusText = '生成世界观...';
    try {
        const data = await API.generateWorldSetting(state.topic);
        state.worldSetting = data.world_setting || data.setting || '';
        state.statusText = '就绪';
    } catch (e) {
        state.statusText = '生成失败';
    }
};

window.generateStorySynopsis = async function() {
    if (!state.topic.trim()) { alert('请先输入主题'); return; }
    state.statusText = '生成梗概...';
    try {
        const data = await API.generateStorySynopsis(state.topic, state.worldSetting);
        state.storySynopsis = data.synopsis || data.story_synopsis || '';
        state.statusText = '就绪';
    } catch (e) {
        state.statusText = '生成失败';
    }
};

console.log('[Writer] App initialized. State available at window.WriterState');
