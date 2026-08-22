const owns = (value, key) => value != null && Object.prototype.hasOwnProperty.call(value, key);
const hasStatusValue = (value, key) => owns(value, key) && value[key] !== null && value[key] !== undefined;

export function resolveRestorationSnapshot({requestedTaskId='', workspaceStatus=null, activeStatus=null}={}) {
  const workspace = workspaceStatus || {};
  const active = activeStatus || workspace;
  const workspaceTaskId = active.workspace_task_id || workspace.workspace_task_id || requestedTaskId;
  const activeTaskId = active.active_task_id || workspace.active_task_id || requestedTaskId;
  const outlineSource = hasStatusValue(active, 'outline') ? active : workspace;
  const draftSource = hasStatusValue(active, 'draft') ? active : workspace;
  return {
    workspaceTaskId,
    activeTaskId,
    outline: outlineSource.outline,
    draft: draftSource.draft,
    outlinePresent: owns(outlineSource, 'outline'),
    draftPresent: owns(draftSource, 'draft'),
  };
}

function draftSnapshots(draft) {
  if (draft === '') return [];
  if (typeof draft !== 'string') return null;
  try {
    const parsed = JSON.parse(draft);
    return Array.isArray(parsed) ? parsed : null;
  } catch (_) {
    return [{section: 1, subsection: 1, text: draft}];
  }
}

export function buildRestoredDraftBlocks({flatOutline, draft, countWords, defaultTargetWords}) {
  if (!Array.isArray(flatOutline) || !flatOutline.length || draft === undefined || draft === null) {
    return {ready:false, blocks:[]};
  }
  const snapshots = draftSnapshots(draft);
  if (snapshots === null) return {ready:false, blocks:[]};
  const byPosition = new Map(snapshots.map(item => [`${item.section}:${item.subsection}`, String(item.text ?? '')]));
  const blocks = [];
  for (const section of flatOutline) {
    blocks.push({type:'section', title:`第${section.section}节：${section.title || ''}`, text:'', wordCount:0, targetWords:0, section:section.section});
    for (const subsection of section.subsections || []) {
      const text = byPosition.get(`${section.section}:${subsection.subsection}`) || '';
      blocks.push({type:'subsection', title:subsection.title || '', text, wordCount:countWords(text), targetWords:subsection.target_words || defaultTargetWords, section:section.section, subsection:subsection.subsection});
    }
  }
  return {ready:true, blocks};
}
