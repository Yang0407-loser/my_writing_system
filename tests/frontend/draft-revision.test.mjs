import test from 'node:test';
import assert from 'node:assert/strict';

import {
  buildLineDiff,
  summarizeRevision,
} from '../../app/static/js/draft-revision.mjs';


test('line diff keeps unchanged context and marks replacements', () => {
  assert.deepEqual(buildLineDiff('第一行\n旧冲突\n结尾', '第一行\n新冲突\n结尾'), [
    { type: 'unchanged', text: '第一行' },
    { type: 'removed', text: '旧冲突' },
    { type: 'added', text: '新冲突' },
    { type: 'unchanged', text: '结尾' },
  ]);
});


test('revision summary reports line and character movement', () => {
  assert.deepEqual(summarizeRevision('短句', '更长的句子'), {
    addedLines: 1,
    removedLines: 1,
    characterDelta: 3,
  });
});
