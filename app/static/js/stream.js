// 流式轮询逻辑

import * as API from './api.js';

/**
 * 开始流式轮询
 * @param {object} state - 全局状态
 * @param {object} callbacks - 事件回调
 * @param {function} callbacks.onToken - (token, section, subsection)
 * @param {function} callbacks.onSectionStart - (section, subsection)
 * @param {function} callbacks.onSectionEnd - (fullText, section, subsection)
 * @param {function} callbacks.onStatusUpdate - (status)
 * @param {function} callbacks.onComplete - ()
 * @param {function} callbacks.onError - (error)
 */
export function startPolling(state, callbacks = {}) {
    let statusTimer = null;
    let streamTimer = null;
    let stopped = false;

    const pollStatus = async () => {
        if (stopped || !state.taskId) return;
        try {
            const data = await API.getStatus(state.taskId);
            if (callbacks.onStatusUpdate) callbacks.onStatusUpdate(data);

            if (data.status === 'completed') {
                stop();
                if (callbacks.onComplete) callbacks.onComplete(data);
                return;
            }
            if (data.status === 'failed' || data.status === 'error') {
                stop();
                if (callbacks.onError) callbacks.onError(data.error || '任务失败');
                return;
            }
            if (data.status === 'stopped') {
                stop();
                return;
            }
            // 大纲更新
            if (data.outline && data.outline.length) {
                state.flatOutline = data.outline;
            }
            // 约束更新
            if (data.constraints) {
                state.constraints = data.constraints;
            }
            // 进度更新
            if (data.progress) {
                state.statusText = data.progress;
            }
        } catch (e) {
            console.warn('Status polling error:', e);
        }
        if (!stopped) {
            statusTimer = setTimeout(pollStatus, 1000);
        }
    };

    let lastId = '0-0';

    const pollStream = async () => {
        if (stopped || !state.taskId) return;
        try {
            const data = await API.getStream(state.taskId, lastId, 100);
            lastId = data.last_id;

            for (const [, event] of data.events) {
                if (stopped) break;
                const ev = event.event || event;

                if (ev === 'section_start' || event.event === 'section_start') {
                    if (callbacks.onSectionStart) {
                        callbacks.onSectionStart(event.section || 0, event.subsection || 0);
                    }
                } else if (ev === 'token' || event.event === 'token') {
                    if (callbacks.onToken) {
                        callbacks.onToken(
                            event.token || event.data || '',
                            event.section || 0,
                            event.subsection || 0
                        );
                    }
                } else if (ev === 'section_end' || event.event === 'section_end') {
                    if (callbacks.onSectionEnd) {
                        callbacks.onSectionEnd(
                            event.data || event.text || '',
                            event.section || 0,
                            event.subsection || 0
                        );
                    }
                } else if (ev === 'done' || event.event === 'done') {
                    stop();
                    if (callbacks.onComplete) callbacks.onComplete();
                    return;
                }
            }

            if (data.status === 'completed' || data.status === 'failed' || data.status === 'stopped') {
                stop();
                if (data.status === 'completed' && callbacks.onComplete) callbacks.onComplete();
                return;
            }
        } catch (e) {
            console.warn('Stream polling error:', e);
        }
        if (!stopped) {
            streamTimer = setTimeout(pollStream, 300);
        }
    };

    function stop() {
        stopped = true;
        if (statusTimer) clearTimeout(statusTimer);
        if (streamTimer) clearTimeout(streamTimer);
        state.isGenerating = false;
    }

    // 启动
    stopped = false;
    state.isGenerating = true;
    pollStatus();
    pollStream();

    return { stop };
}
