# 角色弧小规模确认 UI

## 结论

已在现有大纲编辑器中增加“弧线”入口和独立确认弹窗，状态为
`thin_review_ui_ready_not_promoted`。

交互复核后又统一了两个规划入口：“篇幅”点击后立即打开包含全部小节建议
和事件结构的总览弹窗；“弧线”点击后也立即打开弹窗并显示加载状态，不再
等接口返回后才给用户反馈。静态资源带版本号以避免浏览器继续使用旧缓存。

本功能只展示 `decision` 与 `state_transition` 两类候选，供作者将其确认
为：

- 柔性角色弧推进；
- 硬角色状态转变；
- 普通剧情事件（不是角色弧）。

确认结果只写入任务 Blackboard 的
`character_arc_projection_review` 独立工件，不写
`character_arcs`、EventGraph、checkpoint 或 Writer messages，
`production_effect=false`。

## 交互边界

1. 作者必须先在“篇幅 → 事件结构”中确认来源事件；
2. 事件必须显式包含已选角色姓名，系统不根据“他/她”猜测 actor；
3. 硬转变必须补齐转变前状态、触发因素、转变后状态、正文可观察证据和
   必要性理由；
4. 普通动作、对话、场景切换和观察性素材不进入这张小规模审阅表；
5. 大纲事件文本或角色来源变化后，已有确认会按既有投影契约变为
   stale/superseded，而不是静默沿用。

## 工程实现

- 新增只读 preview API 和单候选 confirm API；
- confirm 时重新根据当前大纲和角色构建投影，并校验 projection ID、
  event text hash、事件作者确认状态和硬转变字段；
- 每次确认后重新计算角色级和章节级 projection hash；
- 兼容 Blackboard 将字典序列化为 JSON 字符串的真实读写行为；
- 前端继续使用现有单页 Vue 文件和 modal 样式，没有提前做组件化重构。

## 已知限制

- 当前独立工件只存于任务 Blackboard，没有新增数据库或 checkpoint
  持久化；
- 固定真实案例原有事件仍是 proposed，且关键句存在代词，因此打开弹窗
  可能没有可确认候选；这代表证据不足，不是自动补猜的理由；
- 本轮没有验证确认后的 V2 Character Arc Planner、Writer 或 EventGraph
  行为，也不授权生产晋级。

## 验证

- 角色弧投影 unit/integration、确认 API、Blackboard JSON 往返和相邻篇幅
  建议入口：19 项通过；
- `main.js`、`api.js` 语法检查通过；
- 受影响 Python 模块 compileall 通过；
- Writer/LLM 调用数为 0。

下一步只应让作者在一个真实大纲中确认少量明确决策/状态变化，观察这套
字段是否易用。未取得真实确认数据前，不接入 CharacterManager、Writer
或 EventGraph。
