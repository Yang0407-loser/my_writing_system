import test from 'node:test';
import assert from 'node:assert/strict';
import * as restoration from '../../app/static/js/task-restoration-state.mjs';

test('active task snapshot wins when workspace and active identities differ', () => {
  assert.equal(typeof restoration.resolveRestorationSnapshot, 'function');
  const result = restoration.resolveRestorationSnapshot({
    requestedTaskId: 'workspace-1',
    workspaceStatus: {workspace_task_id: 'workspace-1', active_task_id: 'active-2', outline: [{title: 'workspace'}], draft: 'workspace draft'},
    activeStatus: {workspace_task_id: 'workspace-1', active_task_id: 'active-2', outline: [{title: 'active'}], draft: '雨落在旧站台。她终于等到回信。'},
  });
  assert.deepEqual(result, {
    workspaceTaskId: 'workspace-1', activeTaskId: 'active-2',
    outline: [{title: 'active'}], draft: '雨落在旧站台。她终于等到回信。',
    outlinePresent: true, draftPresent: true,
  });
});

test('an explicit empty draft is authoritative while a missing field falls back', () => {
  const workspaceStatus = {workspace_task_id: 'workspace-1', active_task_id: 'active-2', draft: 'old'};
  assert.equal(restoration.resolveRestorationSnapshot({workspaceStatus, activeStatus: {workspace_task_id: 'workspace-1', active_task_id: 'active-2', draft: ''}}).draft, '');
  assert.equal(restoration.resolveRestorationSnapshot({workspaceStatus, activeStatus: {workspace_task_id: 'workspace-1', active_task_id: 'active-2'}}).draft, 'old');
});

test('plain short completed draft hydrates exactly into the first subsection', () => {
  const result = restoration.buildRestoredDraftBlocks({
    flatOutline: [{section: 1, title: '雨夜', subsections: [{subsection: 1, title: '候信', target_words: 500}]}],
    draft: '雨落在旧站台。她终于等到回信。', countWords: text => text.length, defaultTargetWords: 2000,
  });
  assert.equal(result.ready, true);
  assert.equal(result.blocks[1].text, '雨落在旧站台。她终于等到回信。');
});

test('empty string is a valid durable draft, not a missing field', () => {
  const result = restoration.buildRestoredDraftBlocks({
    flatOutline: [{section: 1, title: '雨夜', subsections: [{subsection: 1, title: '候信'}]}],
    draft: '', countWords: text => text.length, defaultTargetWords: 2000,
  });
  assert.equal(result.ready, true);
  assert.equal(result.blocks[1].text, '');
});
