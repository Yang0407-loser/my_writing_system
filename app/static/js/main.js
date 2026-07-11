import { createApp, ref, reactive, computed, watch, nextTick, onMounted } from 'vue';
import * as API from './api.js';
import * as OT from './outline-tree.js';
import { countCjk, genId, subtreeWords, flatTree, treeToFlat, flatToTree, findParentAndIndex } from './utils.js';

const FLOW_NODES = [
  { id:'style', label:'风格分析', icon:'🎨' }, { id:'outline', label:'大纲生成', icon:'📋' },
  { id:'character_arcs', label:'角色弧线', icon:'👤' }, { id:'world_state', label:'世界状态', icon:'🌍' },
  { id:'writing', label:'写作', icon:'✍️' }, { id:'continuity', label:'承续检查', icon:'🔗' },
  { id:'review', label:'审阅', icon:'⭐' }, { id:'export', label:'导出', icon:'📦' },
];
const PHASE_MAP = {
  'analyzing_style':'style','planning':'outline','awaiting_outline_approval':'outline',
  'planning_character_arcs':'character_arcs','planning_world_state':'world_state',
  'writing':'writing','awaiting_section_confirm':'writing',
  'continuity_editing':'continuity','fixing':'continuity','reviewing':'review','completed':'export',
};

const PK = 'writer_v4_state';

export function createWriterApp() {
  return createApp({
    setup() {
      // ── Common ──
      const refineMode = ref(false);
      const taskId = ref(''); const statusText = ref('就绪'); const statusColor = ref('#888');
      const tokenUsage = ref(0);
      const awaitingConfirm = ref(false); const confirmPhase = ref('outline');
      const flowchartCollapsed = ref(false); const selectedNodeId = ref(null); const rawStatus = ref('');

      // ── Input ──
      const topic = ref(''); const worldSetting = ref(''); const storySynopsis = ref('');
      const referenceText = ref(''); const globalWordLimit = ref(3000); const mode = ref('celery');
      const genWorld = ref(false); const genSynopsis = ref(false);
      const apiKey = ref('');

      // ── Style ──
      const stylePresets = ['中性','热血','冷峻','治愈','压抑','紧迫','荒诞'];
      const styleProfile = ref({
        preset_name:'', style_brief:'', narrative_density:0.7,
        primary_emotion:'中性', emotion_intensity:50, emotion_subtlety:'含蓄',
        emotion_curve:'平稳', emotional_peaks:'均匀分布', catharsis_style:'渐进式',
        narrative_empathy:'适度共情', inner_monologue_ratio:0.2, show_vs_tell:'平衡',
        emotional_registry:'文学抒情', emotional_contrast:'渐进演变',
        short_sentence_ratio:0.3, medium_sentence_ratio:0.5, long_sentence_ratio:0.2,
        sentence_length_variance:'适度波动', sentence_pattern:'长短交替',
        complex_sentence_ratio:'平衡', paragraph_rhythm:'均匀块状', paragraph_length_avg:200,
        paragraph_opening_style:'混合', dialogue_ratio:0.3, dialogue_mixing:'混合',
        dialogue_tag_style:'稀疏标记', pacing:'中等', scene_transition:'过渡铺垫',
        tension_curve:'波浪起伏', metaphor_frequency:'适度', simile_metaphor_ratio:'平衡',
        personification:'适度', rhetorical_density:0.1, vocabulary_register:'文学化',
        vocabulary_richness:'中等', adjective_density:0.15, adverb_policy:'适度',
        sensory_density:'适度', sensory_spectrum:'视觉为主', color_use:'暖色调', imagery_domain:'自然',
      });
      const analyzingStyle = ref(false); const genBrief = ref(false);

      // ── Outline ──
      const outline = ref(OT.initOutlineDefaults());
      const showSplitPopup = ref(null); const splitRequirement = ref(''); const splitNumChildren = ref(3);
      const aiSplitting = ref(false); const showDescEdit = ref(null); const editingKeyPoints = ref(''); const editingDesc = ref(''); const undoCount = ref(0);
      const injectMenu = ref({node:null}); const injectForm = ref({new_items_str:'',new_characters_str:'',new_factions_str:'',new_locations_str:'',foreshadowing_plant_str:'',foreshadowing_resolve_str:''});
      const showImportModal = ref(false); const importText = ref(''); const importMaxDepth = ref(3);
      const importing = ref(false); const importError = ref('');

      // ── Draft ──
      const isGenerating = ref(false); const generatingBlockIdx = ref(-1);
      const completedSections = ref(0); const draftBlocks = ref([]); const taskDone = ref(false);

      // ── Characters ──
      const showCharModal = ref(false); const charTab = ref('library'); const editingChar = ref(null);
      const extractText = ref(''); const extracting = ref(false); const extractedChars = ref([]);
      const charForm = ref({ name:'',gender:'',age:'',personalityStr:'',strengthsStr:'',weaknessesStr:'',motivation:'',background:'',appearance:'',catchphrase:'',world_position:'',secret:'',previous_life:'',previous_world:'',preserved_knowledge:'',identity_conflict:'',plannedChapter:'' });
      const charFormOpen = ref({ basic:true, personality:true, motivation:true, hidden:false });
      const libraryChars = ref([]); const selectedCharIds = ref([]); const charSearch = ref('');

      // ── Sidebar sections ──
      const rules = ref([]); const foreshadowings = ref([]);
      const sideOpen = ref({ rules: true, foreshadow: true, items: false, chars: true, preview: true, audit: true });
      const rulesSearch = ref(''); const fsSearch = ref('');
      const aiDetectLog = ref([]); const sectionReviewStatus = ref([]);

      // ── Agent Status ──
      const agentStatus = ref('');  // 显示 agent 正在做什么
      const statusLabels = {world_setting:'正在抽取世界观方案...',protagonist:'正在设计主角人设...',outline:'正在规划大纲结构...',outline_refine:'正在完善章节细节...',writing:'正在构思正文方向...',subplot:'正在设计支线...',generic:'正在生成方案...'};

      // ── Modals ──
      const showCards = ref(false); const showDialogue = ref(false); const showReview = ref(false); const showCompleteModal = ref(false);
      const showRulesModal = ref(false); const showInspiration = ref(false);
      const showStyle = ref(false); const showForeshadow = ref(false);
      const showFSForm = ref(false); const fsForm = ref({name:'',description:'',plant_chapter:1,resolve_chapter:null,importance:5});
      const showMap = ref(false); const showSubplot = ref(false); const showItems = ref(false);
      const showFactions = ref(false); const factionsList = ref([]); const factionForm = ref({task_id:'',name:'',type:'宗门',leader_name:'',description:'',goal:'',strength:5,territory:''}); const editingFaction = ref(null);
      const showRelations = ref(false); const relationsList = ref([]);
      const relationForm = ref({task_id:'',character_a:'',character_b:'',relation_type:'盟友',direction:'positive',intensity:5,stages:[],current_stage:0,source:'manual',source_section:0,description:''});
      const editingRelation = ref(null); const relationPresets = ref({relation_types:[],directions:[]});
      const sideCollapsed = ref({left:false, right:false});
      const leftPanelWidth = ref(280); const rightPanelWidth = ref(280); const resizing = ref(null);
      const showTimeline = ref(false); const showAIDetect = ref(false); const showOutlineEval = ref(false); const showHistory = ref(false);
      const showOutlineVersions = ref(false); const outlineVersions = ref([]);
      const mapNodes = ref([]); const subplots = ref([]); const itemsList = ref([]); const timelineEvents = ref([]);
      const showSubplotForm = ref(false); const editingSubplot = ref(null); const selectedSubplot = ref(null);
      const showMapForm = ref(false); const mapForm = ref({name:'',type:'区域',description:'',atmosphere:''});
      const showItemForm = ref(false); const itemForm = ref({name:'',type:'weapon',rarity:'普通',description:'',plannedChapter:''});
      const subplotForm = ref({name:'',description:'',type:'character_arc',volume_start:1,volume_end:3,priority:5,pov:'protagonist',elements:[]});
      const detectText = ref(''); const detecting = ref(false); const detectResult = ref(null); const detectChapter = ref(0);
      const evalRange = ref({from:1,to:3}); const evalLoading = ref(false); const evalResult = ref(null);
      const historyList = ref([]); const historyLoading = ref(false); const selectedHistory = ref(null); const taskContent = ref(''); const taskContentLoading = ref(false);
      const editingRule = ref(undefined); const ruleForm = ref({name:'',content:'',type:'global',priority:5});
      const inspirations = ref([]); const inspCat = ref('world_setting');
      const inspCategories = [
        {key:'world_setting',label:'世界观'},{key:'protagonist',label:'主角设定'},
        {key:'plot_twist',label:'反转套路'},{key:'climax',label:'爽点模板'},
      ];
      const cards = ref([]); const currentStep = ref(''); const dialogueMsgs = ref([]); const dialogueInput = ref('');
      const reviewResults = ref([]); const reviewChapter = ref(1); const reviewLoading = ref(false);

      // ── Toast ──
      const toasts = ref([]);

      // ═══ Computed ═══
      const totalDraftWords = computed(() => draftBlocks.value.reduce((s,b) => s + (b.wordCount||0), 0));
      const totalSubsections = computed(() => { let c = 0; function w(ns) { for (let n of ns) { if (!n.children?.length) c++; else w(n.children); } } w(outline.value); return c; });
      const filteredChars = computed(() => { const q = charSearch.value.toLowerCase(); return libraryChars.value.filter(c => !q || c.name.toLowerCase().includes(q) || (c.personality||[]).some(p=>p.toLowerCase().includes(q))); });
      const selectedChars = computed(() => selectedCharIds.value.map(id => libraryChars.value.find(c => c.id===id)).filter(Boolean));
      const flatTreeItems = computed(() => flatTree(outline.value));
      const showOutlineDetail = ref(false);
      function openOutlinePreview() { showOutlineDetail.value = true; }
      const outlinePreviewText = computed(() => {
        try {
          let text = ''; let leafIdx = 0;
          const root = outline.value;
          if (!root || !Array.isArray(root)) return '(大纲为空)';
          function walk(ns, depth) {
            for (const n of ns||[]) {
              if (!n) continue;
              if (n.children?.length) {
                text += '\n' + '#'.repeat(Math.min(depth,3)) + ' ' + (n.title||'') + '\n';
                walk(n.children, depth + 1);
              } else {
                leafIdx++;
                text += '第' + leafIdx + '节 · ' + (n.title||'');
                text += ' (' + (n.target_words||0) + '字)';
                if ((n.key_points||[]).length) text += '\n  要点: ' + n.key_points.join(' · ');
                if (n.description) text += '\n  梗概: ' + n.description;
                text += '\n\n';
              }
            }
          }
          walk(root, 1);
          return text || '(大纲为空)';
        } catch(e) { return '(生成预览时出错)'; }
      });
      // 支线绑定映射: leafNodeId → [{subplot, element, chapterNum}]
      const subplotLeafBindings = {};
      function getSubplotTags(nodeId) {
        const bindings = subplotLeafBindings[nodeId];
        if (!bindings || !bindings.length) return '';
        const colors = {desire:'#c9a96e',obstacle:'#d4834a',action:'#5d8a5e',result:'#7ea8c9',surprise:'#c96a6a',twist:'#9a8ac9',ending:'#4a9a8a'};
        return bindings.map(b => `<span style="font-size:7px;padding:0 3px;border-radius:2px;color:#fff;background:${colors[b.type]||'#888'}" title="${b.name}·${b.label}">${b.label}</span>`).join('');
      }
      function refreshSubplotBindings() {
        for (const k of Object.keys(subplotLeafBindings)) delete subplotLeafBindings[k];
        const ol = outline.value; const sps = subplots.value;
        if (!ol?.length || !sps?.length) return;
        const nodeMap = {}; let leafIdx = 0;
        (function walk(ns){ for(const n of ns||[]){ if(!n.children?.length){leafIdx++;nodeMap[leafIdx]=n.id;}else walk(n.children);} })(ol);
        for (const sp of sps) {
          for (const el of (sp.elements||[])) {
            for (const ch of (el.chapter_binding||[])) {
              const nid = nodeMap[ch];
              if (nid) {
                if (!subplotLeafBindings[nid]) subplotLeafBindings[nid] = [];
                subplotLeafBindings[nid].push({name: sp.name, type: el.element_type, chapter: ch,
                  label: ({desire:'欲望',obstacle:'阻碍',action:'行动',result:'结果',surprise:'意外',twist:'转折',ending:'结局'})[el.element_type]||el.element_type});
              }
            }
          }
        }
      }
      const visibleDraftBlocks = computed(() => {
        const blocks = draftBlocks.value;
        if (!blocks.length) return [];
        // 显示所有块：已完成内容 + 节标题 + 当前生成中的空块
        return blocks.filter(b => {
          if (b.type==='section') return true;
          if (b.type==='subsection' && (b.wordCount>0||(b.text||'').length>0)) return true;
          if (generatingBlockIdx.value>=0 && b===blocks[generatingBlockIdx.value]) return true;
          return false;
        });
      });
      const filteredRules = computed(() => { const q = rulesSearch.value.toLowerCase(); return rules.value.filter(r => !q || r.name.toLowerCase().includes(q) || r.content.toLowerCase().includes(q)); });
      const filteredFS = computed(() => { const q = fsSearch.value.toLowerCase(); return foreshadowings.value.filter(f => !q || f.name.toLowerCase().includes(q)); });
      const queuedCount = computed(() => { let c=0; function w(ns){for(const n of ns){if(!n.children?.length){if((n.status||'queued')==='queued')c++}else w(n.children)}} w(outline.value); return c; });
      const draftCount = computed(() => { let c=0; function w(ns){for(const n of ns){if(!n.children?.length&&n.status==='draft')c++;else if(n.children)w(n.children)}} w(outline.value); return c; });
      const startBtnText = computed(() => isGenerating.value?'写作中...':(taskId.value&&!taskDone.value?'继续写作':'开始写作'));

      const nodeStates = computed(() => {
        if (!rawStatus.value || rawStatus.value === 'pending') return FLOW_NODES.map(n => ({...n, status:'waiting'}));
        if (rawStatus.value === 'completed') return FLOW_NODES.map(n => ({...n, status:'completed'}));
        if (rawStatus.value === 'failed'||rawStatus.value==='stopped') return FLOW_NODES.map(n => ({...n, status:rawStatus.value==='failed'?'error':'waiting'}));
        const cur = PHASE_MAP[rawStatus.value] || 'writing'; let found = false;
        return FLOW_NODES.map(n => { if (n.id===cur) { found=true; return {...n, status:rawStatus.value.includes('awaiting')?'awaiting':'running'}; } return {...n, status:found?'waiting':'completed'}; });
      });

      // ═══ Toast ═══
      function toast(msg, type='info', ms=2500) {
        const id = Date.now(); toasts.value.push({id,msg,type});
        setTimeout(() => { const i = toasts.value.findIndex(t=>t.id===id); if(i>=0) toasts.value.splice(i,1); }, ms);
      }

      // ═══ Style ═══
      async function applyStylePreset(name) { try{const d=await API.applyPreset(name);const p=d.style_profile||d;Object.keys(styleProfile.value).forEach(k=>{if(k in p)styleProfile.value[k]=p[k]});styleProfile.value.preset_name=name;toast('已应用风格: '+name,'success')}catch(e){toast('加载失败','error')} }
      async function analyzeStyle() { if(!referenceText.value.trim()){toast('请先填入参考文本','error');return} analyzingStyle.value=true; agentStatus.value='正在分析参考文本风格...'; try{const d=await API.analyzeStyle(referenceText.value);const p=d.style_profile||d;Object.keys(styleProfile.value).forEach(k=>{if(k in p)styleProfile.value[k]=p[k]});toast('风格提取完成','success')}catch(e){toast('提取失败','error')}finally{analyzingStyle.value=false;agentStatus.value=''} }
      async function genStyleBriefFn(){if(!styleProfile.value)return;genBrief.value=true;try{const d=await API.regenerateBrief(styleProfile.value);if(d.style_brief)styleProfile.value.style_brief=d.style_brief;toast('简报已生成','success')}catch(e){toast('简报生成失败','error')}finally{genBrief.value=false}}
      async function genWorldSettingFn() { if(!topic.value.trim()){toast('请先输入主题','error');return} genWorld.value=true; try{const d=await API.genWorldSetting(topic.value);worldSetting.value=d.world_setting||d.setting||'';toast('世界观已生成','success')}catch(e){toast('生成失败','error')}finally{genWorld.value=false} }
      async function genStorySynopsisFn() { if(!topic.value.trim())return; genSynopsis.value=true; try{const d=await API.genStorySynopsis(topic.value,worldSetting.value);storySynopsis.value=d.synopsis||d.story_synopsis||''}catch(e){}finally{genSynopsis.value=false} }

      // ═══ Characters ═══
      async function loadCharacters() { try{const d=await API.listCharacters();libraryChars.value=d.characters||d||[];if(!selectedCharIds.value.length&&libraryChars.value.length){selectedCharIds.value=[libraryChars.value[0].id]}}catch(e){} }
      function toggleChar(c) { const i=selectedCharIds.value.indexOf(c.id); if(i>=0)selectedCharIds.value.splice(i,1); else selectedCharIds.value.push(c.id); }
      function openCharModal(c) { charTab.value='form'; extractText.value=''; extractedChars.value=[]; charFormOpen.value={basic:true,personality:true,motivation:true,hidden:false}; if(c){editingChar.value=c;charForm.value={name:c.name||'',gender:c.gender||'',age:c.age||'',personalityStr:(c.personality||[]).join(','),strengthsStr:(c.strengths||[]).join(','),weaknessesStr:(c.weaknesses||[]).join(','),motivation:c.motivation||'',background:c.background||'',appearance:c.appearance||'',catchphrase:c.catchphrase||'',world_position:c.world_position||'',secret:c.secret||'',previous_life:c.previous_life||'',previous_world:c.previous_world||'',preserved_knowledge:c.preserved_knowledge||'',identity_conflict:c.identity_conflict||'',plannedChapter:''}}else{editingChar.value=null;charForm.value={name:'',gender:'',age:'',personalityStr:'',strengthsStr:'',weaknessesStr:'',motivation:'',background:'',appearance:'',catchphrase:'',world_position:'',secret:'',previous_life:'',previous_world:'',preserved_knowledge:'',identity_conflict:'',plannedChapter:''}} showCharModal.value=true; }
      // 辅助：根据章节标题查找大纲叶节点并注入元素
      function injectElementToChapter(titleHint, elementType, elementName) {
        if (!titleHint) return;
        const hint = titleHint.toLowerCase();
        function walk(ns) {
          for (const n of ns) {
            if (!n.children?.length && n.title.toLowerCase().includes(hint)) {
              if (!n.injections) n.injections = {};
              const key = elementType === 'character' ? 'new_characters' :
                elementType === 'faction' ? 'new_factions' :
                elementType === 'location' ? 'new_locations' : 'new_items';
              const arr = n.injections[key] || [];
              if (!arr.includes(elementName)) { arr.push(elementName); n.injections[key] = arr; }
              return true;
            }
            if (n.children && walk(n.children)) return true;
          }
          return false;
        }
        return walk(outline.value);
      }
      async function saveCharacter() { const f=charForm.value; const p={name:f.name,gender:f.gender,age:f.age,personality:f.personalityStr.split(/[,，]/).map(s=>s.trim()).filter(Boolean),strengths:f.strengthsStr.split(/[,，]/).map(s=>s.trim()).filter(Boolean),weaknesses:f.weaknessesStr.split(/[,，]/).map(s=>s.trim()).filter(Boolean),motivation:f.motivation,background:f.background,appearance:f.appearance||'',catchphrase:f.catchphrase||'',world_position:f.world_position||'',secret:f.secret||'',previous_life:f.previous_life||'',previous_world:f.previous_world||'',preserved_knowledge:f.preserved_knowledge||'',identity_conflict:f.identity_conflict||''}; try{if(editingChar.value)await API.updateCharacter(editingChar.value.id,p);else await API.createCharacter(p);if(f.plannedChapter){injectElementToChapter(f.plannedChapter,'character',f.name);saveOutlineFn()}showCharModal.value=false;loadCharacters();toast('角色已保存','success')}catch(e){toast('保存失败','error')} }
      async function doExtract() { extracting.value=true; agentStatus.value='AI 正在从文本中提取角色...'; try{const d=await API.extractCharacters(extractText.value);extractedChars.value=d.characters||d||[]}catch(e){toast('提取失败','error')}finally{extracting.value=false;agentStatus.value=''} }
      async function saveExtracted(i) { const c=extractedChars.value[i]; if(!c)return; try{await API.createCharacter(c);extractedChars.value.splice(i,1);loadCharacters()}catch(e){toast('保存失败','error')} }
      async function deleteCharFn(id){if(!confirm('删除此角色？'))return;try{await API.deleteCharacter(id);selectedCharIds.value=selectedCharIds.value.filter(x=>x!==id);loadCharacters();toast('已删除','info')}catch(e){toast('删除失败','error')}}

      // ═══ Outline Operations ═══
      function addChildNode(node) { OT.addChild(node); }
      function addRootSection() { OT.addRoot(outline.value); }
      async function removeTreeNode(node) {
        const info = findParentAndIndex(outline.value, node.id);
        if (!info) return;
        if (projectId.value) {
          try {
            await API.stageDeleteNode(projectId.value, {
              node: JSON.parse(JSON.stringify(node)),
              parent_id: info.parentNode?.id || '',
              index: info.index
            });
          } catch(e) { /* Redis 不可用时仍允许删除 */ }
        }
        // 将大纲树中排在此节点之后的所有叶节点标记为 draft（跨卷）
        let found = false;
        function markAfter(ns) {
          for (const n of ns) {
            if (found) {
              if (!n.children?.length) n.status = 'draft';
              if (n.children) markAfter(n.children);
            } else if (n.id === node.id) {
              found = true;
            } else if (n.children) {
              markAfter(n.children);
            }
          }
        }
        markAfter(outline.value);
        if (OT.removeNode(outline.value, node)) {
          undoCount.value++;
          toast('已删除 (可撤销) - 后续章节已设为草稿', 'info');
        }
      }
      async function saveOutlineFn() {
        if (!projectId.value) { await ensureProject(); }
        try {
          // 转换为 DB 格式: [{id, parent_id, title, ...}]，保留 id 和父子关系
          const flatNodes = [];
          function flatten(ns, parentId) {
            for (let i = 0; i < ns.length; i++) {
              const n = ns[i];
              flatNodes.push({
                id: n.id, parent_id: parentId, title: n.title || '',
                description: n.description || '', key_points: n.key_points || [],
                target_words: n.target_words || 2000, locked: n.locked || false,
                status: n.status || 'draft', injections: n.injections || {},
                sort_order: i,
              });
              if (n.children && n.children.length) flatten(n.children, n.id);
            }
          }
          flatten(outline.value, '');
          await API.saveOutlineNodes(projectId.value, flatNodes);
          // P10: 如果写作正在进行中，同步大纲到后端
          if (taskId.value && isGenerating.value) {
            try { await API.updateOutline(taskId.value, treeToFlat(outline.value, {})); } catch(e) {}
          }
          toast('大纲已保存', 'success');
        } catch(e) { toast('保存失败', 'error'); }
      }
      async function undoDeleteFn() {
        if (!projectId.value) return;
        try {
          const d = await API.undoDeleteNode(projectId.value);
          const entry = d.entry;
          if (!entry) return;
          const node = entry.node;
          const parentId = entry.parent_id;
          const idx = entry.index;
          if (parentId) {
            const info = findParentAndIndex(outline.value, parentId);
            if (info) {
              const targetParent = info.parent[info.index];
              if (targetParent) {
                if (!targetParent.children) targetParent.children = [];
                targetParent.children.splice(Math.min(idx, targetParent.children.length), 0, node);
              }
            }
          } else {
            outline.value.splice(Math.min(idx, outline.value.length), 0, node);
          }
          undoCount.value = Math.max(0, undoCount.value - 1);
          toast('已撤销删除', 'success');
        } catch(e) { toast('没有可撤销的删除', 'error'); }
      }
      async function loadOutlineFromProject() {
        if (!projectId.value) return;
        try {
          const d = await API.getOutlineNodes(projectId.value);
          if (d.tree && d.tree.length) {
            function initTree(ns) { for (const n of ns) { n.collapsed = false; if (n.children) initTree(n.children); } }
            initTree(d.tree);
            outline.value = d.tree;
            return;
          }
          if (d.nodes && d.nodes.length) {
            const tree = rebuildTreeFromNodes(d.nodes);
            if (tree.length) { outline.value = tree; return; }
          }
          // 服务端无数据：重置为默认，清除可能脏的本地缓存
          if (outline.value && outline.value.length > 0) {
            outline.value = OT.initOutlineDefaults();
            saveState();
          }
        } catch(e) { /* 项目无大纲数据 */ }
      }
      function rebuildTreeFromNodes(flatNodes) {
        const map = {};
        const roots = [];
        for (const n of flatNodes) {
          map[n.id] = { ...n, children: n.children || [], collapsed: false };
        }
        for (const n of flatNodes) {
          const pid = n.parent_id || '';
          if (pid && map[pid]) {
            const parent = map[pid];
            if (!parent.children) parent.children = [];
            parent.children.push(map[n.id]);
          } else {
            roots.push(map[n.id]);
          }
        }
        return roots.length ? roots : [OT.initOutlineDefaults()[0]];
      }
      function moveTreeNode(node,dir) { OT.moveNode(outline.value,node,dir); }
      function toggleLockNode(node) { const locked = OT.toggleLock(node); toast(locked?'已锁定':'已解锁', locked?'info':'success'); }
      const fillingKeyPoints = ref(false);
      async function aiFillKeyPoints(node) {
        if(!node.title || node.title==='新节点'){toast('请先输入节点标题','error');return}
        fillingKeyPoints.value = true;
        try {
          const parent = findParentNode(outline.value, node.id);
          const d = await API.fillKeyPoints({
            node_title: node.title,
            parent_title: parent?.title || '',
            topic: topic.value,
            genre: selectedGenre.value,
            world_setting: worldSetting.value,
          });
          if(d.key_points?.length) node.key_points = d.key_points;
          if(d.description) node.description = d.description;
          toast('要点已填充','success');
        } catch(e) { toast('填充失败','error'); }
        finally { fillingKeyPoints.value = false; }
      }
      function saveKeyPoints(node) {
        node.key_points = editingKeyPoints.value.split(/[,，]/).map(s=>s.trim()).filter(Boolean);
        node.description = editingDesc.value;
        showDescEdit.value = null;
        toast('要点已保存','success');
      }
      function findParentNode(nodes, childId) {
        for(const n of nodes) {
          if(n.children?.some(c=>c.id===childId)) return n;
          if(n.children){const r=findParentNode(n.children,childId);if(r)return r}
        }
        return null;
      }
      function scrollToOutlineNode(node) {
        // 如果是非叶节点（卷/章），找到第一个叶节点后代
        let target = node;
        while (target.children && target.children.length) {
          target = target.children[0];
        }
        // 在大纲树中定位该叶节点的 section/subsection 编号
        let leafIdx = 0;
        function findLeaf(ns, targetId) {
          for (const n of ns) {
            if (!n.children?.length) {
              leafIdx++;
              if (n.id === targetId) return true;
            } else if (n.children && findLeaf(n.children, targetId)) return true;
          }
          return false;
        }
        // 找到卷索引
        let secNum = 0;
        for (let vi = 0; vi < outline.value.length; vi++) {
          const vol = outline.value[vi];
          leafIdx = 0;
          if (findLeaf(vol.children || [], target.id)) { secNum = vi + 1; break; }
        }
        if (!secNum) return;
        const subNum = leafIdx;
        // 滚动到对应的草稿块
        const el = document.getElementById('draft-sub-' + secNum + '-' + subNum);
        if (el) {
          el.scrollIntoView({behavior:'smooth', block:'center'});
          el.style.transition = 'background 0.3s';
          el.style.background = 'rgba(201,169,110,0.1)';
          setTimeout(() => { el.style.background = ''; }, 1500);
        }
      }
      function openInjectMenu(e, node) {
        injectMenu.value = {node};
        if (plotCardNode.value !== node) clearPlotCards();
        // 从已有 injections 回填
        const inj = node.injections || {};
        injectForm.value = {
          new_items_str: (inj.new_items||[]).join(', '),
          new_characters_str: (inj.new_characters||[]).join(', '),
          new_factions_str: (inj.new_factions||[]).join(', '),
          new_locations_str: (inj.new_locations||[]).join(', '),
          foreshadowing_plant_str: (inj.foreshadowing_plant||[]).join(', '),
          foreshadowing_resolve_str: (inj.foreshadowing_resolve||[]).join(', '),
        };
      }
      // F1: 剧情抽卡
      function buildPlotCardContext(node, subType) {
        const parent = findParentNode(outline.value, node.id);
        const sibNodes = [];
        if (parent && parent.children) {
          for (const c of parent.children) {
            if (c.id !== node.id) sibNodes.push(c);
          }
        }
        const sibTitles = sibNodes.map(c=>c.title).join(', ');
        const sibDescs = sibNodes.map(c=>c.title+': '+(c.description||'').slice(0,80)).join('; ');
        return {
          sub_type: subType,
          topic: topic.value,
          node_title: node.title,
          node_key_points: (node.key_points||[]).join(', '),
          parent_volume: parent?.title || '',
          sibling_titles: sibTitles,
          sibling_descriptions: sibDescs,
          existing_chars: libraryChars.value.map(c=>c.name).join(', '),
          existing_factions: factionsList.value.map(f=>f.name).join(', '),
          existing_locations: mapNodes.value.map(n=>n.name).join(', '),
          existing_foreshadowings: foreshadowings.value.map(f=>f.name).join(', '),
          world_setting: worldSetting.value,
          story_synopsis: storySynopsis.value,
          genre: selectedGenre.value,
          adopted_plot_cards: (node._plotCards||[]).map(c=>c.title).join(', '),
        };
      }
      // ── 剧情抽卡（独立展示，不走通用卡片弹窗）──
      const plotCards = ref([]); const drawingPlotCard = ref(false);
      const plotCardNode = ref(null); const plotCardSubType = ref('');
      async function drawPlotCard(subType, node) {
        drawingPlotCard.value = true; plotCards.value = [];
        plotCardNode.value = node; plotCardSubType.value = subType;
        agentStatus.value = '正在生成剧情方案...';
        try {
          const ctx = buildPlotCardContext(node, subType);
          const d = await API.drawCards('plot_development', ctx, 4);
          plotCards.value = d.cards || [];
          if (!plotCards.value.length) toast('抽卡结果为空，请重试', 'error');
        } catch(e) { toast('抽卡失败: '+e.message, 'error'); }
        finally { agentStatus.value = ''; drawingPlotCard.value = false; }
      }
      async function adoptPlotCard(i) {
        // 兼容旧路径：如果 plotCards 为空，从 cards（通用弹窗）取值
        let c = plotCards.value[i];
        let node = plotCardNode.value;
        let subType = plotCardSubType.value;
        if (!c && cards.value[i]) { c = cards.value[i]; node = cards.value._plotNode; subType = cards.value._plotSubType; }
        if (!node) return;
        // 记录采纳历史
        if (!node._plotCards) node._plotCards = [];
        node._plotCards.push({type:subType, title:c.title, summary:c.summary});
        // 写入对应元素库
        const pid = projectId.value || taskId.value;
        try {
          if (subType === 'meet_character' && c.content?.character) {
            await API.createCharacter({...c.content.character, task_id: pid});
            await loadCharacters();
          } else if (subType === 'next_station' && c.content?.location_name) {
            await API.createMapNode({name:c.content.location_name, type:c.content.location_type||'location', description:c.content.description||'', atmosphere:c.content.atmosphere||'', task_id: pid});
            if (typeof loadMap === 'function') loadMap();
          } else if (subType === 'conflict' && c.content?.faction_or_event) {
            await API.createFaction({...c.content.faction_or_event, task_id: pid});
            loadFactions();
          }
        } catch(e) { /* 元素库写入失败不阻塞 */ }
        // 在目标节点后插入剧情节点
        const info = findParentAndIndex(outline.value, node.id);
        if (info) {
          const newNode = {id:genId(), title: c.title||'剧情节点', description: c.summary||'', key_points: [], target_words: globalWordLimit.value, status: 'draft'};
          info.parent.splice(info.index + 1, 0, newNode);
        }
        if (plotCards.value.length > 0 && plotCards.value[i] === c) {
          plotCards.value.splice(i, 1);
          if (plotCards.value.length === 0) { plotCardNode.value = null; plotCardSubType.value = ''; }
        } else {
          // 来自通用弹窗
          cards.value.forEach(cc=>cc.is_adopted=false); c.is_adopted=true;
          showCards.value = false;
        }
        // 自动填入注入元素表单
        const inj = injectForm.value;
        if (subType === 'meet_character' && c.content?.character?.name) {
          const name = c.content.character.name;
          inj.new_characters_str = inj.new_characters_str ? inj.new_characters_str + ', ' + name : name;
        } else if (subType === 'next_station' && c.content?.location_name) {
          const name = c.content.location_name;
          inj.new_locations_str = inj.new_locations_str ? inj.new_locations_str + ', ' + name : name;
        } else if (subType === 'conflict' && c.content?.faction_or_event?.name) {
          const name = c.content.faction_or_event.name;
          inj.new_factions_str = inj.new_factions_str ? inj.new_factions_str + ', ' + name : name;
        } else if (subType === 'opportunity') {
          if (c.content?.item_name) {
            const name = c.content.item_name;
            inj.new_items_str = inj.new_items_str ? inj.new_items_str + ', ' + name : name;
          }
          if (c.content?.foreshadowing_hint) {
            const hint = c.content.foreshadowing_hint;
            inj.foreshadowing_plant_str = inj.foreshadowing_plant_str ? inj.foreshadowing_plant_str + '; ' + hint : hint;
          }
        }
        toast('已采纳: ' + c.title, 'success');
      }
      function clearPlotCards() { plotCards.value = []; plotCardNode.value = null; plotCardSubType.value = ''; }

      function applyInjection(node) {
        const f = injectForm.value;
        const parse = s => s.split(/[,，]/).map(x=>x.trim()).filter(Boolean);
        node.injections = {
          new_items: parse(f.new_items_str),
          new_characters: parse(f.new_characters_str),
          new_factions: parse(f.new_factions_str),
          new_locations: parse(f.new_locations_str),
          foreshadowing_plant: parse(f.foreshadowing_plant_str),
          foreshadowing_resolve: parse(f.foreshadowing_resolve_str),
        };
        injectMenu.value = {node:null};
        toast('元素已注入到: '+node.title, 'success');
      }
      function collapseAll() { OT.collapseAll(outline.value); }
      function expandAll() { OT.expandAll(outline.value); }
      function queueAll() { function w(ns){for(const n of ns){if(!n.children?.length)n.status='queued';else w(n.children)}} w(outline.value); toast('全部章节已加入队列','success'); }
      function dequeueAll() { function w(ns){for(const n of ns){if(!n.children?.length)n.status='draft';else w(n.children)}} w(outline.value); toast('全部章节已移出队列','info'); }
      function toggleLeafStatus(node) { if(node.status==='done')node.status='queued';else if(node.status==='queued')node.status='draft';else node.status='queued';/* 自动同步大纲到后端，让 Writer 轮询检测 */if(taskId.value&&projectId.value){const flatNodes=[];function f(ns,p){for(let i=0;i<ns.length;i++){const n=ns[i];flatNodes.push({id:n.id,parent_id:p,title:n.title||'',description:n.description||'',key_points:n.key_points||[],target_words:n.target_words||2000,locked:n.locked||false,status:n.status||'draft',injections:n.injections||{},sort_order:i});if(n.children)f(n.children,n.id)}}f(outline.value,'');API.saveOutlineNodes(projectId.value,flatNodes).catch(()=>{});API.updateOutline(taskId.value,treeToFlat(outline.value,{})).catch(()=>{})}}
      function leafStatusIcon(s) { return s==='done'?'✅':(s==='draft'?'⚪':'🟡'); }
      function leafStatusColor(s) { return s==='done'?'var(--green)':(s==='draft'?'var(--muted)':'var(--gold)'); }
      function leafStatusTitle(s) { return s==='done'?'已完成(点击重新入队)':(s==='draft'?'点击加入写作队列':'点击移出写作队列'); }
      async function aiSplitNodeFn(node) { aiSplitting.value=true; agentStatus.value='AI 正在拆分大纲节点...'; try{// 收集同级节点信息，避免拆分后内容重叠
        const parent = findParentNode(outline.value, node.id);
        const sibs = (parent?.children||[]).filter(c=>c.id!==node.id);
        const sibTitles = sibs.map(c=>c.title).join(', ');
        const sibContent = sibs.map(c=>c.title+': '+(c.description||'').slice(0,60)).join('; ');
        const d=await API.aiSplitNode({topic:topic.value,node_title:node.title,node_description:node.description||'',node_key_points:node.key_points||[],split_requirement:splitRequirement.value,world_setting:worldSetting.value,story_synopsis:storySynopsis.value,num_children:splitNumChildren.value,target_words_per_child:Math.floor((node.target_words||2000)/Math.max(1,splitNumChildren.value)),parent_target_words:node.target_words||2000,sibling_titles:sibTitles,sibling_content:sibContent});const children=(d.children||d.outline||d||[]).map((c,ci)=>({id:genId(),title:c.title||c.name||('子节点'+(ci+1)),description:c.description||'',key_points:c.key_points||[],target_words:c.target_words||Math.floor((node.target_words||2000)/splitNumChildren.value)}));if(children.length){node.children=children;node.collapsed=false;showSplitPopup.value=null;toast("拆分完成","success");saveOutlineFn()}}catch(e){toast('拆分失败','error')}finally{aiSplitting.value=false;agentStatus.value=''} }
      async function doImportOutline() { importing.value=true;importError.value=''; try{const d=await API.importOutline(importText.value,topic.value,worldSetting.value,storySynopsis.value,importMaxDepth.value);const p=d.outline||d||[];if(p.length){outline.value=flatToTree(p);showImportModal.value=false;importText.value='';toast('导入完成','success')}else importError.value='未能解析大纲结构'}catch(e){importError.value='导入失败: '+e.message}finally{importing.value=false} }

      // ═══ Writing ═══
      function resetWriting() { taskId.value='';statusText.value='就绪';statusColor.value='#888';tokenUsage.value=0;draftBlocks.value=[];isGenerating.value=false;generatingBlockIdx.value=-1;completedSections.value=0;taskDone.value=false;rawStatus.value=''; }
      let pollTimers = { status:null, stream:null };
      function stopPolling() { isGenerating.value=false; clearTimeout(pollTimers.status); clearTimeout(pollTimers.stream); }

      async function startWriting() {
        if(!topic.value.trim()&&!referenceText.value.trim()){toast('请输入主题或参考文本','error');return}
        // 检测续写场景: 有旧 taskId, 未完成, 有排队章节
        const hasContent = draftBlocks.value.some(b=>b.type==='subsection'&&b.wordCount>0);
        const isResuming = taskId.value && !taskDone.value && hasContent && queuedCount.value > 0;
        stopPolling();
        if(!isResuming){ resetWriting(); }
        statusText.value='提交中...';
        await ensureProject();
        try{
          if(isResuming){
            const flat=treeToFlat(outline.value, {});
            const d=await API.continueWriting(taskId.value, {additional_outline:flat, target_words_per_section:globalWordLimit.value, mode:mode.value});
            taskId.value=d.task_id; statusText.value='续写中...';
            beginPolling(flat, true); // keepExisting=true: 保留已写正文
          } else {
            // 非续写但 draftBlocks 有内容：不清除，直接复用
            if(!draftBlocks.value.length||!draftBlocks.value.some(b=>b.type==='subsection'&&b.text)){
              resetWriting();
            }
            const flat=treeToFlat(outline.value, {});
            const chars=selectedCharIds.value.map(id=>libraryChars.value.find(c=>c.id===id)).filter(Boolean);
            const d=await API.startWriting({topic:topic.value,reference_text:referenceText.value||topic.value,target_words_per_section:globalWordLimit.value,characters:chars,world_setting:worldSetting.value,story_synopsis:storySynopsis.value,style_profile:styleProfile.value,outline:flat},mode.value);
            taskId.value=d.task_id; statusText.value='生成中...'; beginPolling(flat);
          }
        }catch(e){statusText.value='提交失败';statusColor.value='#f44336';toast('提交失败','error')}
      }

      function beginPolling(flatOutline, keepExisting=false) {
        stopPolling(); isGenerating.value=true;
        if (!keepExisting) {
          draftBlocks.value=[];
          if(flatOutline&&flatOutline.length){for(const sec of flatOutline){draftBlocks.value.push({type:'section',title:'第'+sec.section+'节：'+(sec.title||''),text:'',wordCount:0,targetWords:0});for(const sub of(sec.subsections||[])){draftBlocks.value.push({type:'subsection',title:sub.title||'',text:'',wordCount:0,targetWords:sub.target_words||2000,section:sec.section,subsection:sub.subsection})}}}
        }
        // 辅助: 根据 section/subsection 在大纲树中定位叶节点
        function markLeafStatus(section,subsection,status){
          const vols=outline.value;if(section<1||section>vols.length)return;
          function findLeaf(ns,targetIdx){let idx=0;for(const n of ns){if(!n.children?.length){idx++;if(idx===targetIdx){n.status=status;return true}}else if(n.children&&findLeaf(n.children,targetIdx))return true}return false}
          findLeaf(vols[section-1].children||[],subsection);
        }
        let lastId='0-0'; let stopped=false;
        function stop(){stopped=true;stopPolling()}
        function findBlock(sec,sub){return draftBlocks.value.findIndex(b=>b.section==sec&&b.subsection==sub)}
        let _pollCnt=0;
        async function ps(){if(stopped)return;try{const d=await API.getStatus(taskId.value);rawStatus.value=d.status;statusText.value=d.progress||d.status;if(d.token_usage!=null)tokenUsage.value=d.token_usage;if(d.outline?.length&&!outline.value.length)outline.value=flatToTree(d.outline);// 每10轮刷新世界元素
        _pollCnt++;if(_pollCnt%5===0){loadCharacters();loadFactions();loadMap();loadItems();loadTimeline();loadForeshadowings();loadSubplots();loadRelations()}if(_pollCnt%10===0&&projectId.value){try{const snaps=draftBlocks.value.filter(b=>b.type==='subsection'&&b.text).map(b=>({section:b.section,subsection:b.subsection,text:b.text.slice(0,3000)}));if(snaps.length)API.saveDraft(projectId.value,JSON.stringify(snaps)).catch(()=>{})}catch(e){}}// 每10轮保存草稿(结构化)
        // 刷新恢复：draftBlocks 空时从后端拉取已写正文
        if(!draftBlocks.value.length&&d.draft&&d.draft.length>100){draftBlocks.value=[{type:'section',title:'已写内容',text:'',wordCount:0,targetWords:0},{type:'subsection',title:'',text:d.draft,wordCount:countCjk(d.draft),targetWords:globalWordLimit.value,section:0,subsection:0}];statusText.value='已恢复 '+countCjk(d.draft)+' 字';saveState()}if(d.constraints){}if(d.status==='completed'){stop();statusText.value='完成';statusColor.value='#4caf50';taskDone.value=true;saveState();loadForeshadowings();loadCharacters();loadFactions();loadMap();loadItems();loadSubplots();loadRelations();showCompleteModal.value=true;loadReviewResults();return}
        if(d.ai_detect_log?.length)aiDetectLog.value=d.ai_detect_log;
        if(d.section_reviews?.length)sectionReviewStatus.value=d.section_reviews;
        if(d.status==='failed'||d.status==='error'){stop();statusText.value='失败';statusColor.value='#f44336';return}if(d.status==='stopped'){stop();return}if(d.status==='awaiting_queue'){stop();statusText.value='等待排队 - 勾选大纲节点后继续';statusColor.value='var(--gold)';isGenerating.value=false;taskDone.value=false;return}awaitingConfirm.value=d.status?.includes('awaiting')||false;if(awaitingConfirm.value)confirmPhase.value=d.status?.includes('outline')?'outline':'section'}catch(e){}if(!stopped)pollTimers.status=setTimeout(ps,1000)}
        async function pstr(){if(stopped)return;try{const d=await API.getStream(taskId.value,lastId);lastId=d.last_id;for(const[,evt]of(d.events||[])){if(stopped)break;if(evt.event==='section_start'||evt==='section_start'){const i=findBlock(evt.section,evt.subsection);if(i>=0){generatingBlockIdx.value=i;draftBlocks.value[i].text=''}}else if(evt.event==='token'||evt==='token'){const i=findBlock(evt.section,evt.subsection);if(i>=0){draftBlocks.value[i].text+=(evt.token||evt.data||'');draftBlocks.value[i].wordCount=countCjk(draftBlocks.value[i].text)}}else if(evt.event==='section_end'||evt==='section_end'){const i=findBlock(evt.section,evt.subsection);if(i>=0){draftBlocks.value[i].text=evt.text||evt.data||draftBlocks.value[i].text;draftBlocks.value[i].wordCount=countCjk(draftBlocks.value[i].text)}markLeafStatus(evt.section,evt.subsection,'done');generatingBlockIdx.value=-1;completedSections.value=Math.max(completedSections.value,evt.section||0)}else if(evt.event==='done'||evt==='done'){stop();taskDone.value=true;saveState();loadForeshadowings();loadCharacters();loadFactions();loadMap();loadItems();loadSubplots();loadRelations();showCompleteModal.value=true;loadReviewResults();return}}if(d.status==='completed'||d.status==='failed'){stop();return}}catch(e){}if(!stopped)pollTimers.stream=setTimeout(pstr,300)}
        ps(); pstr();
      }

      async function sendDecisionFn(action,feedback=''){try{await API.sendDecision(taskId.value,confirmPhase.value,action,feedback);awaitingConfirm.value=false}catch(e){toast('操作失败','error')}}
      async function stopWriting(){if(taskId.value){try{await sendDecisionFn('stop')}catch(e){}}stopPolling();isGenerating.value=false;taskDone.value=false;statusText.value='已停止';statusColor.value='#888'}

      // ═══ Cards + Chain-constrained Wizard (P8: 题材+全覆盖) ═══
      const selectedGenre = ref('');
      const wizardSteps = ['题材', '世界观', '主角', '配角', '势力', '地图', '大纲'];
      const wizardStepKeys = ['genre', 'world_setting', 'protagonist', 'supporting_characters', 'factions_card', 'locations_card', 'outline'];
      const wizardStep = ref(0); // 0=题材选择
      const adoptedCards = ref({});
      const outlinePreview = ref(null);
      const _outlineBackup = ref(null); // 预览前的原始大纲备份

      function previewOutlineCard(card, idx) {
        if (outlinePreview.value === idx) {
          // 再次点击取消预览，恢复原始大纲
          outline.value = _outlineBackup.value || OT.initOutlineDefaults();
          outlinePreview.value = null;
          _outlineBackup.value = null;
          return;
        }
        // 备份当前大纲
        _outlineBackup.value = JSON.parse(JSON.stringify(outline.value));
        // 解析卡片中的卷章结构
        const vols = card?.content?.volumes || card?.content?.structure ? [{title:'大纲方案',chapters:[{title:card.content.structure?.slice(0,40)||'大纲预览',summary:card.content.structure||''}]}] : card?.content?.volumes || [];
        if (vols.length) {
          outline.value = vols.map((vol, vi) => ({
            id: genId(), title: vol.title || '第' + (vi + 1) + '卷', collapsed: false, locked: false,
            children: (vol.chapters || []).map((ch, ci) => ({
              id: genId(), title: ch.title || '第' + (ci + 1) + '章',
              description: ch.summary || '', key_points: ch.key_events || [],
              target_words: globalWordLimit.value
            }))
          }));
        }
        outlinePreview.value = idx;
      }

      function stepLabel(s){return ({genre:'题材选择',world_setting:'世界观设定',protagonist:'主角设定',supporting_characters:'配角设计',factions_card:'势力格局',locations_card:'地图设计',outline:'大纲规划',outline_refine:'大纲完善',writing:'正文方向',subplot:'支线故事'})[s]||s}

      /** 构建链式约束: 收集前面步骤已采纳的卡片 */
      function buildChainContext(step){
        const ctx = {topic:topic.value, world_setting:worldSetting.value, story_synopsis:storySynopsis.value};
        if(selectedGenre.value) ctx.genre = selectedGenre.value;
        const wi = wizardStepKeys.indexOf(step);
        for (let i=0; i<wi; i++){
          const prev = wizardStepKeys[i];
          const card = adoptedCards.value[prev];
          if (card) {
            ctx['constraint_'+prev] = JSON.stringify(card.content);
            if (prev==='world_setting' && card.content?.world_setting) ctx.world_setting = card.content.world_setting;
          }
        }
        return ctx;
      }

      function selectGenre(g){
        selectedGenre.value=g; wizardStep.value=1;
        adoptedCards.value.genre = {title:g, content:{genre:g}};
        toast('已选题材: '+g,'success');
        drawCardsFn('world_setting');
      }
      async function drawCardsFn(step,extraCtx={}){
        currentStep.value=step; cards.value=[]; showCards.value=true;
        agentStatus.value = statusLabels[step]||statusLabels.generic;
        const wi = wizardStepKeys.indexOf(step);
        if(wi>=0) wizardStep.value = wi+1;
        const ctx = {...buildChainContext(step), ...extraCtx};
        try{const d=await API.drawCards(step,ctx,4);cards.value=d.cards||[]}catch(e){toast('抽卡失败','error')}
        finally{agentStatus.value=''}
      }

      async function adoptCard(i){
        const c=cards.value[i]; if(!c) return;
        if(currentStep.value==='plot_development'){await adoptPlotCard(i);return} // 兼容旧路径（通用弹窗）
        cards.value.forEach(cc=>cc.is_adopted=false); c.is_adopted=true;
        const step = currentStep.value;
        adoptedCards.value[step] = c;
        await adoptToProject(step, c);
        nextWizardStep();
      }

      async function adoptToProject(step, c){
        const pid = projectId.value;
        if(step==='world_setting'&&c.content){
          worldSetting.value = c.content.world_setting||'';
          if(pid) { try{await API.saveWorldSetting(pid, c.content.world_setting||'')}catch(e){} }
          toast('世界观已应用: '+c.title,'success');
        } else if(step==='protagonist'&&c.content){
          const name = c.content.name||'主角';
          const ch = {name, gender:c.content.gender||'男', age:c.content.age||'18', personality:[c.content.personality||''].filter(Boolean), motivation:c.content.motivation||'', background:c.content.background||'', appearance:'', catchphrase:'', strengths:[c.content.golden_finger||''].filter(Boolean), weaknesses:[c.content.weakness||''].filter(Boolean)};
          try{
            // 冲突检测: 检查同名角色
            const existing = libraryChars.value.find(x=>x.name===name);
            if(existing && !confirm(`角色"${name}"已存在。\n[确定] 替换  [取消] 保留原角色`)){
              toast('保留原角色, 未采纳新卡片','info'); return;
            }
            if(existing) await API.deleteCharacter(existing.id);
            await API.createCharacter(ch);
            loadCharacters();
            toast('主角已创建: '+name,'success');
          }catch(e){toast('角色创建失败: '+e.message,'error')}
        } else if(step==='outline'&&c.content){
          const vols = c.content.volumes||[];
          if(vols.length){
            // 结构化解析: volumes → 大纲树节点
            const newOutline = vols.map((vol,vi)=>({
              id:genId(), title:vol.title||'第'+(vi+1)+'卷', collapsed:false, locked:false,
              children: (vol.chapters||[]).map((ch,ci)=>({
                id:genId(), title:ch.title||'第'+(ci+1)+'章', description:ch.summary||'',
                key_points:ch.key_events||[], target_words:globalWordLimit.value, status:'queued'
              }))
            }));
            outline.value = newOutline;
            // 持久化到项目
            if(pid){
              const flatNodes = [];
              function flatten(ns, parentId=''){ for(let i=0;i<ns.length;i++){ const n=ns[i]; flatNodes.push({id:n.id,parent_id:parentId,title:n.title,description:n.description||'',key_points:n.key_points||[],target_words:n.target_words||2000,locked:n.locked||false,sort_order:i}); if(n.children) flatten(n.children,n.id); } }
              flatten(newOutline);
              try{await API.saveOutlineNodes(pid, flatNodes)}catch(e){}
            }
            if(!storySynopsis.value.trim()&&c.summary) storySynopsis.value = c.summary.slice(0,300);
            if(!topic.value.trim()) topic.value = vols[0]?.title || '';
            toast('大纲已应用: '+vols.length+'卷','success');
          } else if(c.content.structure){
            outline.value = [{id:genId(),title:'大纲方案',collapsed:false,children:[{id:genId(),title:(c.content.structure||'').slice(0,50),description:c.content.structure||'',target_words:globalWordLimit.value}]}];
            toast('大纲已应用 (旧格式)','success');
          }
        } else if(step==='writing'&&c.content){
          // 正文方向卡片: 记录开篇方式、核心场景、角色互动
          const dir = c.content;
          const summary = [
            dir.opening ? '开篇: '+dir.opening : '',
            dir.key_scene ? '核心场景: '+dir.key_scene : '',
            dir.character_interaction ? '角色互动: '+dir.character_interaction : '',
          ].filter(Boolean).join('; ');
          worldSetting.value = (worldSetting.value||'') + '\n\n【正文方向】\n' + (c.title||'') + '\n' + summary;
          if(pid) { try{await API.saveWorldSetting(pid, worldSetting.value)}catch(e){} }
          toast('正文方向已应用: '+c.title,'success');
        } else if(step==='supporting_characters'&&c.content){
          const chars = c.content.characters||[];
          for(const ch of chars){
            try{
              await API.createCharacter({
                name:ch.name||'配角', gender:ch.gender||'?', age:ch.age||'?',
                personality:[ch.personality||''].filter(Boolean),
                motivation:ch.relationship_to_protagonist||'', background:ch.arc_hint||''
              });
              toast('配角已创建: '+(ch.name||'配角'),'success');
            }catch(e){}
          }
          await loadCharacters();
        } else if(step==='locations_card'&&c.content){
          const nodes = c.content.nodes||[];
          for(const n of nodes){
            try{
              await API.createMapNode({name:n.name, type:n.type||'location', description:n.description||'', atmosphere:n.atmosphere||'', task_id:pid||taskId.value});
              toast('地点已创建: '+n.name,'success');
            }catch(e){}
          }
          if(typeof loadMap==='function') loadMap();
        } else if(step==='factions_card'&&c.content){
          const factions = c.content.factions||[];
          for(const f of factions){
            try{if(pid){await API.createFaction({...f,task_id:pid});toast('势力已创建: '+f.name,'success')}}catch(e){}
          }
        } else if(step==='subplot'&&c.content){
          const sp = c.content;
          try{
            await API.createSubplot({...sp, task_id: pid||taskId.value,
              elements: (sp.elements||[]).map(e=>({...e, chapter_binding: e.chapter_binding||[]}))
            });
            if(typeof loadSubplots==='function') loadSubplots();
            toast('支线已创建: '+(sp.name||c.title),'success');
          }catch(e){toast('支线创建失败','error')}
        } else {
          toast('已采纳: '+c.title,'success');
        }
      }

      // ═══ Project ═══
      const projectId = ref('');
      async function ensureProject(){
        if(projectId.value) return;
        try{const d=await API.createProject(topic.value||'未命名项目');projectId.value=d.id;toast('项目已创建','info',1500)}catch(e){}
      }
      async function loadProjectData(){
        if(!projectId.value) return;
        try{
          const d = await API.getProject(projectId.value);
          if(d.world_setting) worldSetting.value = d.world_setting;
          if(d.story_synopsis) storySynopsis.value = d.story_synopsis;
        }catch(e){}
        await loadOutlineFromProject();
      }

      function nextWizardStep(){
        const wi = wizardStepKeys.indexOf(currentStep.value);
        if(wi>=0 && wi<6){
          const next = wizardStepKeys[wi+1];
          wizardStep.value = wi+2;
          drawCardsFn(next);
        } else {
          showCards.value = false;
          // 向导完成，自动补全主题和梗概
          if(!topic.value.trim()){
            const wsCard = adoptedCards.value['world_setting'];
            if(wsCard?.content?.core_conflict) topic.value = wsCard.content.core_conflict.slice(0,40);
            else if(wsCard?.title) topic.value = wsCard.title;
          }
          if(!storySynopsis.value.trim()){
            const outlineCard = adoptedCards.value['outline'];
            if(outlineCard?.summary) storySynopsis.value = outlineCard.summary.slice(0,200);
          }
          toast('抽卡引导完成','success');
        }
      }

      async function modifyCard(i){const ins=prompt('修改要求：');if(!ins)return;try{const d=await API.redrawCard(currentStep.value,{topic:topic.value,world_setting:worldSetting.value},i,ins);if(d.card)cards.value[i]=d.card}catch(e){toast('修改失败','error')}}
      function skipCards(){nextWizardStep()}

      // ═══ Dialogue ═══
      async function sendDialogue(){const msg=dialogueInput.value.trim();if(!msg)return;dialogueMsgs.value.push({role:'user',content:msg});dialogueInput.value='';try{const d=await API.dialogueChat({topic:topic.value,chapter:completedSections.value+1,world_setting:worldSetting.value},msg);dialogueMsgs.value.push({role:'ai',content:d.reply||''})}catch(e){dialogueMsgs.value.push({role:'ai',content:'抱歉, AI 暂时无法回复'})}}

      // ═══ Rules & FS ═══
      async function loadRules(){try{const d=await API.listRules();rules.value=d.rules||[]}catch(e){}}
      async function loadForeshadowings(){try{let tid=taskId.value||projectId.value;if(!tid)return;let d=await API.listForeshadowings(tid);if(!(d.foreshadowings||[]).length&&projectId.value&&tid===projectId.value){d=await API.listForeshadowings(taskId.value||'')}foreshadowings.value=d.foreshadowings||[]}catch(e){}}
      const reviewTab = ref('section');
      async function loadReviewResults(){if(!taskId.value)return;reviewLoading.value=true;try{const d=await API.getStatus(taskId.value);if(d.review){const r=d.review;const secReviews=r.section_reviews||[];const sr=secReviews.find(s=>s.section===reviewChapter.value)||secReviews[reviewChapter.value-1]||{};const dims=[];if(sr.scores&&Object.keys(sr.scores).length>0){const labels={pace:'节奏',dialogue:'对话',description:'描写',tension:'张力',character_voice:'人物声音'};for(const[k,v]of Object.entries(sr.scores)){dims.push({key:k,label:labels[k]||k,score:v})}}reviewResults.value={global_score:r.global_score,chapter_scores:r.chapter_scores||[],tension_curve:r.tension_curve||'',pacing_issues:r.pacing_issues||[],style_adherence:r.style_adherence||'',subplot_health:r.subplot_health||[],character_arc_health:r.character_arc_health||[],top_3_actions:r.top_3_actions||[],global_strength:r.strength||'',global_weakness:r.weakness||'',global_suggestion:r.suggestion||'',character_consistency:r.character_consistency||'',character_arc_progress:r.character_arc_progress||'',section_reviews:secReviews,volume_title:sr.volume_title||'',leaf_titles:sr.leaf_titles||[],dimensions:dims,section_score:sr.score,highlight:sr.highlight||{},lowlight:sr.lowlight||{},consistency_notes:sr.consistency_notes||'',improvement:sr.improvement||'',rewrite_target:sr.rewrite_target||''}}else{reviewResults.value={}}reviewChapter.value=completedSections.value||1}catch(e){reviewResults.value={}}finally{reviewLoading.value=false}}
      async function createFSFn(){const f=fsForm.value;if(!f.name.trim()){toast('名称不能为空','error');return};try{await API.createForeshadowing({...f,task_id:taskId.value});fsForm.value={name:'',description:'',plant_chapter:1,resolve_chapter:null,importance:5};showFSForm.value=false;loadForeshadowings();toast('伏笔已创建','success')}catch(e){toast('创建失败','error')}}
      async function saveRuleFn(){const f=ruleForm.value;if(!f.name.trim()||!f.content.trim()){toast('名称和内容不能为空','error');return};try{if(editingRule.value){await API.updateRule(editingRule.value.id,f);toast('规则已更新','success')}else{await API.createRule(f);toast('规则已创建','success')};editingRule.value=undefined;loadRules()}catch(e){toast('保存失败','error')}}
      async function deleteRuleFn(id){if(!confirm('确定删除此规则?'))return;try{await API.deleteRule(id);loadRules();toast('已删除','info')}catch(e){toast('删除失败','error')}}
      async function exportRules(){try{const d=await API.req('/api/rules/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(null)});const blob=new Blob([d.json],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='rules.json';a.click();toast('已导出','success')}catch(e){toast('导出失败','error')}}
      async function importRulesFile(e){const file=e.target.files[0];if(!file)return;try{const text=await file.text();await API.req('/api/rules/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({json_str:text,on_conflict:'skip'})});loadRules();toast('导入完成','success')}catch(e){toast('导入失败','error')}}

      // ═══ Inspiration ═══
      async function loadInspirations(){try{const d=await API.req('/api/cards/inspirations');inspirations.value=d.inspirations||{}}catch(e){}}
      const filteredInspirations = computed(() => {const items=inspirations.value[inspCat.value];return Array.isArray(items)?items:[]});
      function useInspiration(item){drawCardsFn('world_setting');showInspiration.value=false;toast('已应用灵感: '+item.name,'info')}

      // ═══ Map/Subplot/Items/Timeline/AI Detect/Outline Eval ═══
      async function loadMap(){try{const tid=taskId.value||projectId.value;if(!tid)return;const d=await API.fullMap(tid);mapNodes.value=d.nodes||[]}catch(e){}}
      async function createMapNodeFn(){const f=mapForm.value;if(!f.name.trim()){toast('名称不能为空','error');return};try{await API.createMapNode({...f,task_id:taskId.value||projectId.value});if(f.plannedChapter){injectElementToChapter(f.plannedChapter,'location',f.name);saveOutlineFn()}mapForm.value={name:'',type:'区域',description:'',atmosphere:'',plannedChapter:''};showMapForm.value=false;loadMap();toast('地点已创建','success')}catch(e){toast('创建失败','error')}}
      async function loadSubplots(){try{const tid=taskId.value||projectId.value;if(!tid)return;const d=await API.listSubplots(tid);subplots.value=d.subplots||[];refreshSubplotBindings()}catch(e){}}
      async function saveSubplotFn(){const f=subplotForm.value;if(!f.name.trim()){toast('名称不能为空','error');return};f.task_id=taskId.value||projectId.value;f.elements=(f.elements||[]).map(e=>({element_type:e.element_type,name:e.name,description:e.description,chapter_binding:(e._chStr||'').split(/[,，]/).map(s=>parseInt(s.trim())).filter(Boolean)}));try{if(editingSubplot.value){await API.updateSubplot(editingSubplot.value.id,f);toast('支线已更新','success')}else{await API.createSubplot(f);toast('支线已创建','success')};showSubplotForm.value=false;editingSubplot.value=null;subplotForm.value={name:'',description:'',type:'character_arc',volume_start:1,volume_end:3,priority:5,elements:[]};loadSubplots()}catch(e){toast('保存失败','error')}}
      async function deleteSubplotFn(id){if(!confirm('删除此支线？'))return;try{await API.deleteSubplot(id);loadSubplots();toast('已删除','info')}catch(e){toast('删除失败','error')}}
      // ── 支线抽卡（独立UI）──
      const subplotCards = ref([]); const drawingSubplotCards = ref(false);
      async function drawSubplotCardsFn(){
        drawingSubplotCards.value=true; subplotCards.value=[];
        // 确保相关数据已加载
        if(!libraryChars.value.length) await loadCharacters();
        if(!factionsList.value.length) await loadFactions();
        if(!subplots.value.length) await loadSubplots();
        try{
          const chars = libraryChars.value.map(c=>c.name+((c.personality||[]).length?'('+c.personality.slice(0,2).join(',')+')':'')).join('; ');
          const facs = factionsList.value.map(f=>f.name+'('+(f.type||'')+')').join('; ');
          const existingNames = subplots.value.map(s=>s.name+':'+(s.description||'').slice(0,40)).join('; ');
          const outlineSummary = outline.value.map(v=>{
            const chTitles = (v.children||[]).map(c=>c.title).join(', ');
            return v.title + (chTitles?' ['+chTitles+']':'');
          }).join('; ');
          const ctx = {
            topic:topic.value, world_setting:worldSetting.value, genre:selectedGenre.value,
            characters: chars, factions: facs, existing_subplots: existingNames,
            outline_summary: outlineSummary,
          };
          const d = await API.drawCards('subplot', ctx, 4);
          subplotCards.value = d.cards || d || [];
          if(!subplotCards.value.length) toast('抽卡结果为空，请重试','error');
        }catch(e){toast('支线抽卡失败: '+e.message,'error')}
        finally{drawingSubplotCards.value=false}
      }
      async function adoptSubplotCardFn(i){
        const c = subplotCards.value[i]; if(!c) return;
        const sp = c.content || c;
        try{
          await API.createSubplot({
            name: sp.name||'', description: sp.description||'', type: sp.type||'character_arc',
            volume_start: sp.volume_start||1, volume_end: sp.volume_end||3,
            priority: sp.priority||5, pov: sp.pov||'protagonist',
            task_id: taskId.value||projectId.value,
            elements: (sp.elements||[]).map(e=>({element_type:e.element_type||'desire',name:e.name||'',description:e.description||'',chapter_binding:e.chapter_binding||[]}))
          });
          subplotCards.value.splice(i,1);
          loadSubplots();
          toast('支线已创建: '+(sp.name||c.title),'success');
        }catch(e){toast('采纳失败: '+e.message,'error')}
      }
      async function loadItems(){try{const tid=taskId.value||projectId.value;if(!tid)return;const d=await API.charInventory('');itemsList.value=d.items||[]}catch(e){}}
      async function createItemFn(){const f=itemForm.value;if(!f.name.trim()){toast('名称不能为空','error');return};try{await API.createItem({...f,task_id:taskId.value||projectId.value});if(f.plannedChapter){injectElementToChapter(f.plannedChapter,'item',f.name);saveOutlineFn()}itemForm.value={name:'',type:'weapon',rarity:'普通',description:'',plannedChapter:''};showItemForm.value=false;loadItems();toast('物品已创建','success')}catch(e){toast('创建失败','error')}}
      async function loadTimeline(){try{const tid=taskId.value||projectId.value;if(!tid)return;const d=await API.req('/api/experience?task_id='+tid);timelineEvents.value=d.events||[]}catch(e){}}
      async function loadHistory(){historyLoading.value=true;try{const d=await API.listProjects();historyList.value=d.projects||[]}catch(e){}finally{historyLoading.value=false}}
      async function resumeProject(pid){
        if(!pid) return;
        // 清除当前写作状态
        taskId.value=''; draftBlocks.value=[]; isGenerating.value=false; taskDone.value=false;
        generatingBlockIdx.value=-1; completedSections.value=0;
        projectId.value = pid;
        try{
          const d = await API.getProject(pid);
          if(d.world_setting) worldSetting.value = d.world_setting;
          if(d.story_synopsis) storySynopsis.value = d.story_synopsis;
          if(d.name && d.name!=='未命名项目') topic.value = d.name;
        }catch(e){}
        await loadOutlineFromProject();
        await loadCharacters();
        await loadFactions();
        try{loadSubplots();loadRelations();loadForeshadowings()}catch(e){}
        // 恢复草稿正文
        try{
          const dd = await API.getDraft(pid);
          if(dd.draft && dd.draft.startsWith('[')) {
            const snaps = JSON.parse(dd.draft);
            const flat = treeToFlat(outline.value, {});
            draftBlocks.value = [];
            for(const sec of flat){
              draftBlocks.value.push({type:'section',title:'第'+sec.section+'节：'+(sec.title||''),text:'',wordCount:0,targetWords:0,section:sec.section});
              for(const sub of(sec.subsections||[])){
                const snap = snaps.find(s=>s.section===sec.section&&s.subsection===sub.subsection);
                const txt = snap?snap.text:'';
                draftBlocks.value.push({type:'subsection',title:sub.title||'',text:txt,wordCount:countCjk(txt),targetWords:sub.target_words||2000,section:sec.section,subsection:sub.subsection});
              }
            }
          }
        }catch(e){}
        showHistory.value = false;
        toast('项目已加载','success');
      }
      async function deleteProjectFn(pid){
        if(!confirm('确定删除此项目？所有大纲和设定将丢失。')) return;
        try{await API.deleteProject(pid);loadHistory();toast('已删除','info')}catch(e){toast('删除失败','error')}
      }
      async function loadOutlineVersions(){if(!projectId.value)return;try{const d=await API.getOutlineVersions(projectId.value);outlineVersions.value=d.versions||[]}catch(e){}}
      async function restoreOutlineVersion(vid){if(!projectId.value||!confirm('恢复此版本将覆盖当前大纲，确定？'))return;try{const d=await API.restoreOutlineVersion(projectId.value,vid);outline.value=OT.initOutlineDefaults();await loadOutlineFromProject();showOutlineVersions.value=false;toast('大纲已恢复','success')}catch(e){toast('恢复失败','error')}}
      async function loadFactions(){try{const d=await API.listFactions(taskId.value||'');factionsList.value=(d.factions||[]).map(f=>({...f,members_str:(f.members||[]).join(', ')}))}catch(e){}}
      async function saveFactionFn(){const f=factionForm.value;if(!f.name.trim()){toast('名称不能为空','error');return};f.task_id=taskId.value;f.members=(f.members_str||'').split(/[,，]/).map(s=>s.trim()).filter(Boolean);try{if(editingFaction.value){await API.updateFaction(editingFaction.value.id,f);toast('势力已更新','success')}else{await API.createFaction(f);toast('势力已创建','success')};if(f.plannedChapter){injectElementToChapter(f.plannedChapter,'faction',f.name);saveOutlineFn()}editingFaction.value=null;loadFactions()}catch(e){toast('保存失败','error')}}
      async function deleteFactionFn(id){if(!confirm('确定删除此势力?'))return;try{await API.deleteFaction(id);loadFactions();toast('已删除','info')}catch(e){toast('删除失败','error')}}
      // ── Character Relations ──
      async function loadRelations(){try{const d=await API.listRelations(taskId.value||'');relationsList.value=d.relations||[]}catch(e){}}
      async function loadRelationPresets(){try{const d=await API.getRelationPresets();relationPresets.value=d}catch(e){}}
      function openRelationForm(r){if(r){editingRelation.value=r;const stages=r.stages&&r.stages.length?r.stages:[{stage:'',section:1,trigger:'',status:'pending'}];relationForm.value={task_id:r.task_id,character_a:r.character_a,character_b:r.character_b,relation_type:r.relation_type,direction:r.direction,intensity:r.intensity,stages:JSON.parse(JSON.stringify(stages)),current_stage:r.current_stage||0,source:r.source||'manual',source_section:r.source_section||0,description:r.description||''};}else{editingRelation.value=null;relationForm.value={task_id:taskId.value,character_a:'',character_b:'',relation_type:'盟友',direction:'positive',intensity:5,stages:[{stage:'',section:1,trigger:'',status:'pending'}],current_stage:0,source:'manual',source_section:0,description:''};}showRelations.value=true;if(!relationPresets.value.relation_types.length)loadRelationPresets();loadRelations();}
      function addRelationStage(){relationForm.value.stages.push({stage:'',section:1,trigger:'',status:'pending'});}
      function removeRelationStage(i){if(relationForm.value.stages.length>1)relationForm.value.stages.splice(i,1);}
      async function saveRelation(){const f=relationForm.value;if(!f.character_a.trim()||!f.character_b.trim()){toast('请选择两个角色','error');return}f.task_id=taskId.value;try{if(editingRelation.value){await API.updateRelation(editingRelation.value.id,f);toast('关系已更新','success')}else{await API.createRelation(f);toast('关系已创建','success')}editingRelation.value=null;loadRelations()}catch(e){toast('保存失败: '+e.message,'error')}}
      async function deleteRelation(id){if(!confirm('确定删除此关系?'))return;try{await API.deleteRelation(id);loadRelations();toast('已删除','info')}catch(e){toast('删除失败','error')}}
      function autoFillDetectText(){detectChapter.value=0;if(draftBlocks.value.length){const blocks=draftBlocks.value.filter(b=>b.type==='subsection');detectText.value=blocks.map(b=>b.text).join('\n\n').slice(0,8000)}}
      function autoFillDetectByChapter(){if(!detectChapter.value){detectText.value='';return}const [sec,sub]=String(detectChapter.value).split('-').map(Number);const b=draftBlocks.value.find(x=>x.section===sec&&x.subsection===sub);detectText.value=b?b.text||'':'未找到对应正文'}
      async function runAIDetect(){if(!detectText.value.trim()){toast('请粘贴文本','error');return}detecting.value=true;agentStatus.value='正在检测AI痕迹...';try{const d=await API.detectAI(detectText.value);detectResult.value=d}catch(e){toast('检测失败','error')}finally{detecting.value=false;agentStatus.value=''}}
      async function runOutlineEval(){evalLoading.value=true;agentStatus.value='正在评估大纲逻辑...';try{const d=await API.req('/api/analysis/evaluate?task_id='+taskId.value+'&from='+evalRange.value.from+'&to='+evalRange.value.to,{method:'POST'});evalResult.value=d}catch(e){toast('评估失败','error')}finally{evalLoading.value=false;agentStatus.value=''}}

      // ═══ Persistence ═══
      function saveState(){try{const s={taskId:taskId.value,projectId:projectId.value,topic:topic.value,worldSetting:worldSetting.value,storySynopsis:storySynopsis.value,referenceText:referenceText.value,apiKey:apiKey.value,styleProfile:styleProfile.value,outline:outline.value,globalWordLimit:globalWordLimit.value,mode:mode.value,selectedCharIds:selectedCharIds.value,draftSnap:draftBlocks.value.map(b=>({...b,text:(b.text||'').slice(0,2000)})),savedAt:Date.now()};localStorage.setItem(PK,JSON.stringify(s))}catch(e){}}
      function restoreSession(){try{const r=localStorage.getItem(PK);if(!r)return false;const s=JSON.parse(r);if(Date.now()-(s.savedAt||0)>24*60*60*1000){localStorage.removeItem(PK);return false}if(s.projectId)projectId.value=s.projectId;if(s.taskId)taskId.value=s.taskId;if(s.topic)topic.value=s.topic;if(s.worldSetting)worldSetting.value=s.worldSetting;if(s.storySynopsis)storySynopsis.value=s.storySynopsis;if(s.referenceText)referenceText.value=s.referenceText;if(s.apiKey)apiKey.value=s.apiKey;if(s.styleProfile)styleProfile.value=s.styleProfile;if(s.outline?.length)outline.value=s.outline;if(s.globalWordLimit)globalWordLimit.value=s.globalWordLimit;if(s.mode)mode.value=s.mode;if(s.selectedCharIds)selectedCharIds.value=s.selectedCharIds;if(s.draftSnap?.length){draftBlocks.value=s.draftSnap;taskDone.value=true}return!!s.taskId}catch(e){return false}}
      function resetAll(){
        stopPolling();
        // 先清空 reactive 状态，beforeunload 即使触发也只保存空数据
        taskId.value=''; projectId.value=''; draftBlocks.value=[];
        outline.value=[]; worldSetting.value=''; storySynopsis.value='';
        topic.value=''; selectedCharIds.value=[];
        try{localStorage.removeItem(PK)}catch(e){}
        location.href = location.origin + location.pathname;
      }

      // ═══ Keyboard ═══
      function onKeydown(e){if(!cards.value.length)return;const n=parseInt(e.key);if(n>=1&&n<=cards.value.length)adoptCard(n-1);else if(e.key==='r'||e.key==='R')drawCardsFn(currentStep.value);else if(e.key==='s'||e.key==='S')skipCards()}
      // Panel resize
      function startResize(side, e){resizing.value=side;e.preventDefault();document.body.style.cursor='col-resize';document.body.style.userSelect='none'}
      function onMouseMove(e){if(!resizing.value)return;if(resizing.value==='left'){leftPanelWidth.value=Math.max(180,Math.min(500,e.clientX))}else{rightPanelWidth.value=Math.max(180,Math.min(500,window.innerWidth-e.clientX))}}
      function onMouseUp(){resizing.value=null;document.body.style.cursor='';document.body.style.userSelect=''}

      // ═══ Init ═══
      onMounted(async()=>{await loadCharacters();await loadRules();const restored=restoreSession();API.setApiKey(apiKey.value);if(projectId.value){await loadProjectData();try{const d=await API.getUndoCount(projectId.value);undoCount.value=d.count||0}catch(e){}}if(restored&&taskId.value){statusText.value='恢复连接中...';beginPolling(null, true)}setInterval(saveState,5000);window.addEventListener('beforeunload',()=>{saveState();if(projectId.value){const snaps=draftBlocks.value.filter(b=>b.type==='subsection'&&b.text).map(b=>({section:b.section,subsection:b.subsection,text:b.text.slice(0,3000)}));if(snaps.length){navigator.sendBeacon('/api/projects/'+projectId.value+'/draft',JSON.stringify({draft:JSON.stringify(snaps)}))}}});document.addEventListener('keydown',onKeydown);document.addEventListener('mousemove',onMouseMove);document.addEventListener('mouseup',onMouseUp)});
      watch(apiKey,(v)=>API.setApiKey(v));

      // Word sync
      let _sw=false;watch(globalWordLimit,(v)=>{if(_sw)return;_sw=true;const leaves=[];function w(ns){for(let n of ns){if(!n.children?.length)leaves.push(n);else w(n.children)}}w(outline.value);if(leaves.length){const each=Math.floor(v/leaves.length);leaves.forEach(l=>l.target_words=each)}nextTick(()=>_sw=false)});
      // F2: 大纲变化时同步节标题到中心面板
      watch(outline,()=>{const flat=treeToFlat(outline.value,{});if(!flat.length)return;let si=0;for(const b of draftBlocks.value){if(b.type==='section'&&si<flat.length){b.title='第'+flat[si].section+'节：'+(flat[si].title||'');si++}}},{deep:true});
      watch(generatingBlockIdx,(idx)=>{if(idx>=0){nextTick(()=>{const b=draftBlocks.value[idx];if(b){const el=document.getElementById('draft-sub-'+b.section+'-'+b.subsection);if(el)el.scrollIntoView({behavior:'smooth',block:'center'})}})}});

      return { refineMode,taskId,statusText,statusColor,awaitingConfirm,confirmPhase,flowchartCollapsed,selectedNodeId,rawStatus,
        topic,worldSetting,storySynopsis,referenceText,globalWordLimit,mode,apiKey,genWorld,genSynopsis,
        stylePresets,styleProfile,analyzingStyle,genBrief,
        outline,showSplitPopup,splitRequirement,splitNumChildren,aiSplitting,showDescEdit,editingKeyPoints,editingDesc,showImportModal,importText,importMaxDepth,importing,importError,undoCount,injectMenu,injectForm,
        tokenUsage,isGenerating,generatingBlockIdx,completedSections,draftBlocks,taskDone,
        showCharModal,charTab,editingChar,extractText,extracting,extractedChars,charForm,charFormOpen,libraryChars,selectedCharIds,charSearch,
        filteredChars,selectedChars,totalDraftWords,totalSubsections,nodeStates,flatTreeItems,visibleDraftBlocks,queuedCount,draftCount,startBtnText,showOutlineDetail,openOutlinePreview,outlinePreviewText,
        rules,foreshadowings,sideOpen,rulesSearch,fsSearch,filteredRules,filteredFS,aiDetectLog,sectionReviewStatus,
        showCards,showDialogue,showReview,showCompleteModal,cards,currentStep,dialogueMsgs,dialogueInput,reviewResults,reviewChapter,reviewLoading,reviewTab,loadReviewResults,
        toasts,
        subtreeWords, addChildNode,addRootSection,removeTreeNode,moveTreeNode,toggleLockNode,collapseAll,expandAll,queueAll,dequeueAll,toggleLeafStatus,leafStatusIcon,leafStatusColor,leafStatusTitle,openInjectMenu,applyInjection,saveKeyPoints,aiFillKeyPoints,fillingKeyPoints,scrollToOutlineNode,
        applyStylePreset,analyzeStyle,genStyleBriefFn,genWorldSettingFn,genStorySynopsisFn,
        loadCharacters,toggleChar,openCharModal,saveCharacter,doExtract,saveExtracted,deleteCharFn,
        aiSplitNodeFn,doImportOutline,saveOutlineFn,undoDeleteFn,loadProjectData,
        startWriting,stopWriting,resetAll,sendDecisionFn,
        drawCardsFn,adoptCard,modifyCard,skipCards,nextWizardStep,stepLabel,wizardStep,wizardSteps,adoptedCards,selectedGenre,selectGenre,outlinePreview,previewOutlineCard,drawPlotCard,adoptPlotCard,projectId,ensureProject,plotCards,drawingPlotCard,plotCardNode,plotCardSubType,clearPlotCards,
        agentStatus,
        sendDialogue,loadRules,loadForeshadowings,
        showRulesModal,showInspiration,showStyle,showForeshadow,showFSForm,fsForm,
        editingRule,ruleForm,filteredInspirations,inspCat,inspCategories,
        saveRuleFn,deleteRuleFn,exportRules,importRulesFile,loadInspirations,useInspiration,createFSFn,
        showMap,showSubplot,showItems,showTimeline,showAIDetect,showOutlineEval,showFactions,showHistory,showOutlineVersions,outlineVersions,showMapForm,mapForm,showItemForm,itemForm,showSubplotForm,subplotForm,editingSubplot,selectedSubplot,factionsList,factionForm,editingFaction,sideCollapsed,leftPanelWidth,rightPanelWidth,resizing,startResize,
        mapNodes,subplots,itemsList,timelineEvents,detectText,detecting,detectResult,detectChapter,evalRange,evalLoading,evalResult,historyList,historyLoading,selectedHistory,taskContent,taskContentLoading,
        loadMap,loadSubplots,loadItems,loadTimeline,loadHistory,loadOutlineVersions,restoreOutlineVersion,createMapNodeFn,createItemFn,saveSubplotFn,deleteSubplotFn,autoFillDetectText,autoFillDetectByChapter,runAIDetect,runOutlineEval,loadFactions,saveFactionFn,deleteFactionFn,resumeProject,deleteProjectFn,
        subplotCards,drawingSubplotCards,drawSubplotCardsFn,adoptSubplotCardFn,
        showRelations,relationsList,relationForm,editingRelation,relationPresets,loadRelations,openRelationForm,addRelationStage,removeRelationStage,saveRelation,deleteRelation,
        toast,
      };
    }
  });
}
