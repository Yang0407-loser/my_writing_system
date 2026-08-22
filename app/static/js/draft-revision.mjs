function splitLines(value) {
  return String(value ?? '').replace(/\r\n/g, '\n').split('\n');
}

export function buildLineDiff(before, after) {
  const left = splitLines(before);
  const right = splitLines(after);
  const table = Array.from({length:left.length + 1}, () =>
    Array(right.length + 1).fill(0));

  for (let i = left.length - 1; i >= 0; i -= 1) {
    for (let j = right.length - 1; j >= 0; j -= 1) {
      table[i][j] = left[i] === right[j]
        ? table[i + 1][j + 1] + 1
        : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }

  const rows = [];
  let i = 0;
  let j = 0;
  while (i < left.length || j < right.length) {
    if (i < left.length && j < right.length && left[i] === right[j]) {
      rows.push({type:'unchanged', text:left[i]});
      i += 1;
      j += 1;
    } else if (i < left.length && (j >= right.length || table[i + 1][j] >= table[i][j + 1])) {
      rows.push({type:'removed', text:left[i]});
      i += 1;
    } else {
      rows.push({type:'added', text:right[j]});
      j += 1;
    }
  }
  return rows;
}

export function summarizeRevision(before, after) {
  const rows = buildLineDiff(before, after);
  return {
    addedLines: rows.filter(row => row.type === 'added').length,
    removedLines: rows.filter(row => row.type === 'removed').length,
    characterDelta: String(after ?? '').length - String(before ?? '').length,
  };
}
