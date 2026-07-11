// 工具函数
export function countCjk(t) { return (t.match(/[一-鿿]/g)||[]).length; }
export function genId() { return 'n_'+Date.now()+'_'+Math.random().toString(36).slice(2,8); }

export function subtreeWords(node) {
  if (!node.children || !node.children.length) return node.target_words || 0;
  return node.children.reduce((s, c) => s + subtreeWords(c), 0);
}

export function flatTree(nodes, collapsedOnly = false) {
  const r = [];
  function walk(ns, level) {
    for (const n of ns) {
      r.push({ node: n, level });
      if (n.children && n.children.length && !n.collapsed) walk(n.children, level + 1);
    }
  }
  walk(nodes, 1);
  return r;
}

export function findParentAndIndex(rootNodes, targetId, parentNode = null) {
  for (let i = 0; i < rootNodes.length; i++) {
    if (rootNodes[i].id === targetId) return { parent: rootNodes, index: i, parentNode };
    if (rootNodes[i].children) {
      const r = findParentAndIndex(rootNodes[i].children, targetId, rootNodes[i]);
      if (r) return r;
    }
  }
  return null;
}

export function treeToFlat(rootNodes, opts={}) {
  const onlyStatus = opts.onlyStatus || null;
  function leaves(node) {
    const ch = node.children || [];
    if (!ch.length) { const s = node.status || 'queued'; return (onlyStatus && s !== onlyStatus) ? [] : [node]; }
    let r = []; for (let c of ch) r.push(...leaves(c)); return r;
  }
  return rootNodes.map((root, si) => {
    const ls = leaves(root);
    if (!ls.length) return null;
    return { section: si+1, title: root.title||'', key_points: root.key_points||[], subsections: ls.map((leaf, li) => ({ subsection: li+1, title: leaf.title||'', description: leaf.description||'', key_points: leaf.key_points||[], target_words: leaf.target_words||2000, status: leaf.status||'queued' })) };
  }).filter(Boolean);
}

export function flatToTree(flat) {
  return flat.map((sec, si) => ({ id: genId(), title: sec.title||'第'+(si+1)+'节', key_points: sec.key_points||[], collapsed: false, children: (sec.subsections||[]).map(sub => ({ id: genId(), title: sub.title||'', description: sub.description||'', key_points: sub.key_points||[], target_words: sub.target_words||2000, status: sub.status||'queued' })) }));
}

export function escHtml(s) { if (!s) return ''; return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
