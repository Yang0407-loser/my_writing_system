// 全局响应式状态管理

export const state = {
    // ── 通用 ──
    taskId: '',
    statusText: '就绪',
    statusColor: '#888',
    apiBase: 'http://localhost:8000',
    mode: 'celery',           // 'celery' | 'interactive'
    granularity: 'section',   // 'section' | 'paragraph'

    // ── 输入 ──
    topic: '',
    worldSetting: '',
    storySynopsis: '',
    referenceText: '',
    globalWordLimit: 10000,

    // ── 大纲 ──
    outlineTree: [],          // 树状 [{id, title, description, key_points, target_words, collapsed, children}]
    flatOutline: [],          // 扁平 [{section, title, key_points, subsections}]

    // ── 风格 ──
    styleProfile: null,       // 50维风格参数
    styleBrief: '',

    // ── 角色 ──
    libraryChars: [],
    selectedCharIds: [],

    // ── 草稿/流式 ──
    draftBlocks: [],          // [{type, title, text, wordCount, targetWords, section, subsection}]
    isGenerating: false,
    generatingBlockIdx: -1,
    completedSections: 0,
    taskDone: false,

    // ── 约束/伏笔/规则 (Phase 1) ──
    constraints: [],
    foreshadowings: [],
    rules: [],

    // ── 抽卡 (Phase 2) ──
    cards: [],                // 当前步骤的卡片
    currentStep: '',          // 当前抽卡步骤
    adoptedCards: [],         // 已采纳的卡片历史

    // ── 分析 ──
    analysisData: null,

    // ── 历史 ──
    taskHistory: [],
};

// Vue 3 兼容: 如果用 Vue reactive() 包装，在 app.js 中处理
// 这里导出纯对象，app.js 负责用 reactive() 包装

/** 重置写作状态（保留设置） */
export function resetWritingState() {
    state.taskId = '';
    state.statusText = '就绪';
    state.statusColor = '#888';
    state.draftBlocks = [];
    state.isGenerating = false;
    state.generatingBlockIdx = -1;
    state.completedSections = 0;
    state.taskDone = false;
    state.constraints = [];
    state.foreshadowings = [];
    state.cards = [];
    state.currentStep = '';
    state.analysisData = null;
}

/** 计算总字数 */
export function calcTotalWords() {
    return state.draftBlocks.reduce((sum, b) => sum + (b.wordCount || 0), 0);
}
