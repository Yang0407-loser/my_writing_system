// localStorage 持久化层

const STORAGE_KEY = 'writer_v4_state';
const MAX_AGE_MS = 30 * 60 * 1000; // 30分钟过期

export function saveState(state) {
    try {
        const snapshot = {
            taskId: state.taskId,
            topic: state.topic,
            worldSetting: state.worldSetting,
            storySynopsis: state.storySynopsis,
            referenceText: state.referenceText,
            apiBase: state.apiBase,
            styleProfile: state.styleProfile,
            styleBrief: state.styleBrief,
            outlineTree: state.outlineTree,
            globalWordLimit: state.globalWordLimit,
            mode: state.mode,
            granularity: state.granularity,
            selectedCharIds: state.selectedCharIds,
            draftBlocks: state.draftBlocks,
            savedAt: Date.now(),
        };
        localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot));
    } catch (e) {
        console.warn('状态保存失败:', e);
    }
}

export function loadState(state) {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) return false;

        const snapshot = JSON.parse(raw);

        // 过期检查
        if (snapshot.savedAt && Date.now() - snapshot.savedAt > MAX_AGE_MS) {
            localStorage.removeItem(STORAGE_KEY);
            return false;
        }

        // 恢复状态
        if (snapshot.taskId) state.taskId = snapshot.taskId;
        if (snapshot.topic) state.topic = snapshot.topic;
        if (snapshot.worldSetting) state.worldSetting = snapshot.worldSetting;
        if (snapshot.storySynopsis) state.storySynopsis = snapshot.storySynopsis;
        if (snapshot.referenceText) state.referenceText = snapshot.referenceText;
        if (snapshot.apiBase) state.apiBase = snapshot.apiBase;
        if (snapshot.styleProfile) state.styleProfile = snapshot.styleProfile;
        if (snapshot.styleBrief) state.styleBrief = snapshot.styleBrief;
        if (snapshot.outlineTree && snapshot.outlineTree.length) state.outlineTree = snapshot.outlineTree;
        if (snapshot.globalWordLimit) state.globalWordLimit = snapshot.globalWordLimit;
        if (snapshot.mode) state.mode = snapshot.mode;
        if (snapshot.granularity) state.granularity = snapshot.granularity;
        if (snapshot.selectedCharIds) state.selectedCharIds = snapshot.selectedCharIds;
        if (snapshot.draftBlocks && snapshot.draftBlocks.length) state.draftBlocks = snapshot.draftBlocks;

        return !!snapshot.taskId;
    } catch (e) {
        console.warn('状态加载失败:', e);
        return false;
    }
}

export function clearState() {
    try {
        localStorage.removeItem(STORAGE_KEY);
    } catch (e) {
        // ignore
    }
}
