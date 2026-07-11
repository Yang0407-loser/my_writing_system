// 大纲树操作 (从 main.js 的 Vue setup 中调用)
import { genId, findParentAndIndex, flatTree } from './utils.js';

export function initOutlineDefaults() {
  return [
    { id: genId(), title: '第一卷', collapsed: false, locked: false,
      children: [{ id: genId(), title: '第1章', description: '', target_words: 2000, status: 'queued' }] }
  ];
}

export function addChild(node) {
  if (!node.children) node.children = [];
  node.children.push({ id: genId(), title: '新节点', description: '', target_words: 2000, status: 'queued' });
}

export function addRoot(outline) {
  outline.push({ id: genId(), title: '新卷', collapsed: false,
    children: [{ id: genId(), title: '新章', description: '', target_words: 2000, status: 'queued' }] });
}

export function removeNode(outline, node) {
  const info = findParentAndIndex(outline, node.id);
  if (!info) return false;
  info.parent.splice(info.index, 1);
  return true;
}

export function moveNode(outline, node, dir) {
  const info = findParentAndIndex(outline, node.id);
  if (!info) return;
  const newIdx = info.index + dir;
  if (newIdx < 0 || newIdx >= info.parent.length) return;
  info.parent.splice(info.index, 1);
  info.parent.splice(newIdx, 0, node);
}

export function toggleLock(node) {
  node.locked = !node.locked;
  return node.locked;
}

export function collapseAll(outline) {
  function c(ns) { for (let n of ns) { n.collapsed = true; if (n.children) c(n.children); } }
  c(outline);
}

export function expandAll(outline) {
  function c(ns) { for (let n of ns) { n.collapsed = false; if (n.children) c(n.children); } }
  c(outline);
}

// 收集某个大纲节点的所有已锁定祖先, 用于后端约束注入
export function collectLockedAncestors(outline, nodeId) {
  const ancestors = [];
  function find(nodes, path) {
    for (const n of nodes) {
      if (n.id === nodeId) { ancestors.push(...path); return true; }
      if (n.children && find(n.children, [...path, n])) return true;
    }
    return false;
  }
  find(outline, []);
  return ancestors.filter(a => a.locked);
}
