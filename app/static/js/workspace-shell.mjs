export const DEFAULT_WORKSPACE_ID = 'write';

export const WORKSPACE_DEFINITIONS = Object.freeze([
  Object.freeze({ id: 'write', label: '写作', description: '编辑正文、跟踪生成并发起定向修订' }),
  Object.freeze({ id: 'outline', label: '大纲', description: '组织章节、检查结构并管理写作队列' }),
  Object.freeze({ id: 'world', label: '世界', description: '维护人物、关系、地点、物品与支线' }),
  Object.freeze({ id: 'analysis', label: '分析', description: '检查连续性、事件、状态变化与质量信号' }),
  Object.freeze({ id: 'projects', label: '项目', description: '恢复长期项目并管理导出与归档' }),
]);

const WORKSPACES_BY_ID = new Map(
  WORKSPACE_DEFINITIONS.map((workspace) => [workspace.id, workspace]),
);

export function normalizeWorkspaceId(value) {
  return WORKSPACES_BY_ID.has(value) ? value : DEFAULT_WORKSPACE_ID;
}

export function workspaceMeta(value) {
  return WORKSPACES_BY_ID.get(normalizeWorkspaceId(value));
}
