export function createTaskRestorationStreamGate({ deferred = false } = {}) {
  let ready = !deferred;
  let lastId = '0-0';

  return {
    isReady() {
      return ready;
    },

    cursor() {
      return lastId;
    },

    recordCursor(nextLastId) {
      if (!ready || !nextLastId) return;
      lastId = nextLastId;
    },

    releaseAfterHydration() {
      if (ready) return false;
      ready = true;
      lastId = '0-0';
      return true;
    },
  };
}

export function buildRestoredDraftBlocks({
  flatOutline,
  draft,
  countWords = text => String(text).length,
  defaultTargetWords = 2000,
} = {}) {
  if (!Array.isArray(flatOutline) || flatOutline.length === 0) return { ready: false, blocks: [] };

  let snapshots;
  try {
    snapshots = Array.isArray(draft)
      ? draft
      : typeof draft === 'string' && draft.trim() === ''
        ? []
        : JSON.parse(draft);
  } catch {
    return { ready: false, blocks: [] };
  }
  if (!Array.isArray(snapshots)) return { ready: false, blocks: [] };

  const subsectionKeys = new Set();
  for (const section of flatOutline) {
    for (const subsection of section?.subsections || []) {
      subsectionKeys.add(`${section.section}:${subsection.subsection}`);
    }
  }
  if (subsectionKeys.size === 0) return { ready: false, blocks: [] };

  const snapshotsByKey = new Map();
  for (const snapshot of snapshots) {
    if (!snapshot || typeof snapshot !== 'object') return { ready: false, blocks: [] };
    const key = `${snapshot.section}:${snapshot.subsection}`;
    if (!subsectionKeys.has(key)) return { ready: false, blocks: [] };
    snapshotsByKey.set(key, snapshot);
  }

  const blocks = [];
  for (const section of flatOutline) {
    blocks.push({
      type: 'section',
      title: `第${section.section}节：${section.title || ''}`,
      text: '',
      wordCount: 0,
      targetWords: 0,
      section: section.section,
    });
    for (const subsection of section.subsections || []) {
      const snapshot = snapshotsByKey.get(`${section.section}:${subsection.subsection}`);
      const text = typeof snapshot?.text === 'string' ? snapshot.text : '';
      blocks.push({
        type: 'subsection',
        title: subsection.title || '',
        text,
        wordCount: countWords(text),
        targetWords: subsection.target_words || defaultTargetWords,
        section: section.section,
        subsection: subsection.subsection,
      });
    }
  }
  return { ready: true, blocks };
}

export async function runDurableHydration({
  isCurrent,
  load,
  apply,
  wait = delay => new Promise(resolve => setTimeout(resolve, delay)),
  initialRetryMs = 500,
  maxRetryMs = 5000,
} = {}) {
  let attempt = 0;
  while (isCurrent()) {
    let result = null;
    try {
      result = await load(attempt);
    } catch {
      result = null;
    }
    if (!isCurrent()) return false;
    if (result?.ready) {
      apply(result.blocks);
      return true;
    }
    const delay = Math.min(maxRetryMs, initialRetryMs * (2 ** Math.min(attempt, 5)));
    attempt += 1;
    await wait(delay);
  }
  return false;
}
