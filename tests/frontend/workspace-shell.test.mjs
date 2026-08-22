import test from 'node:test';
import assert from 'node:assert/strict';

import {
  DEFAULT_WORKSPACE_ID,
  WORKSPACE_DEFINITIONS,
  normalizeWorkspaceId,
  workspaceMeta,
} from '../../app/static/js/workspace-shell.mjs';


test('workspace shell exposes the five stable author workspaces', () => {
  assert.equal(DEFAULT_WORKSPACE_ID, 'write');
  assert.deepEqual(
    WORKSPACE_DEFINITIONS.map(({ id, label }) => ({ id, label })),
    [
      { id: 'write', label: '写作' },
      { id: 'outline', label: '大纲' },
      { id: 'world', label: '世界' },
      { id: 'analysis', label: '分析' },
      { id: 'projects', label: '项目' },
    ],
  );
});


test('workspace restoration rejects unknown or empty workspace ids', () => {
  assert.equal(normalizeWorkspaceId('analysis'), 'analysis');
  assert.equal(normalizeWorkspaceId('missing'), DEFAULT_WORKSPACE_ID);
  assert.equal(normalizeWorkspaceId(''), DEFAULT_WORKSPACE_ID);
  assert.equal(normalizeWorkspaceId(null), DEFAULT_WORKSPACE_ID);
});


test('workspace metadata is stable for the dynamic inspector', () => {
  assert.deepEqual(workspaceMeta('outline'), {
    id: 'outline',
    label: '大纲',
    description: '组织章节、检查结构并管理写作队列',
  });
  assert.equal(workspaceMeta('unknown').id, DEFAULT_WORKSPACE_ID);
});
