const BASE = window.location.origin;
let _apiKey = '';

export function setApiKey(k) { _apiKey = k || ''; }

function _hdrs(opts) {
  const h = { ...(opts.headers || {}) };
  if (_apiKey) h['X-API-Key'] = _apiKey;
  return h;
}

async function req(url, opts={}) {
  const r = await fetch(BASE + url, { ...opts, headers: _hdrs(opts) });
  if (!r.ok) { const t = await r.text(); throw new Error(t); }
  return r.json();
}
export { req };
function post(url,body){return req(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})}
function put(url,body){return req(url,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})}

// Writing
export const startWriting=(body,mode)=>req('/write?mode='+mode,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
export const getStatus=(id)=>req('/status/'+id);
export const getStream=(id,lastId)=>req('/stream/'+id+'?last_id='+encodeURIComponent(lastId)+'&count=50');
export const sendDecision=(id,phase,action,fb='')=>req('/tasks/'+id+'/decide?phase='+phase+'&action='+action+'&feedback='+encodeURIComponent(fb),{method:'POST'});
export const continueWriting=(id,body)=>req('/tasks/'+id+'/continue',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
export const updateOutline=(id,outline)=>post('/tasks/'+id+'/update-outline',{outline});

// Generate
export const genWorldSetting=(topic)=>post('/api/generate/world-setting',{topic});
export const genStorySynopsis=(topic,ws)=>post('/api/generate/story-synopsis',{topic,world_setting:ws});
export const aiSplitNode=(body)=>post('/api/generate/split-node',body);
export const importOutline=(text,topic,ws,ss,depth=3)=>post('/api/generate/import-outline',{text,topic,world_setting:ws,story_synopsis:ss,max_depth:depth});
export const fillKeyPoints=(body)=>post('/api/generate/fill-key-points',body);

// Style
export const applyPreset=(name)=>post('/api/style/preset',{preset_name:name});
export const analyzeStyle=(text)=>post('/api/style/analyze',{reference_text:text});
export const regenerateBrief=(profile)=>post('/api/style/brief',{style_profile:profile});

// Characters
export const listCharacters=()=>req('/api/characters?limit=100');
export const createCharacter=(c)=>post('/api/characters',c);
export const updateCharacter=(id,c)=>put('/api/characters/'+id,c);
export const deleteCharacter=(id)=>req('/api/characters/'+id,{method:'DELETE'});
export const extractCharacters=(text)=>post('/api/characters/extract',{text});

// Cards
export const drawCards=(step,ctx,num=4,reqt='')=>post('/api/cards/draw',{step,context:ctx,num_cards:num,user_requirement:reqt});
export const redrawCard=(step,ctx,idx,fb)=>post('/api/cards/redraw',{step,context:ctx,card_index:idx,user_feedback:fb});

// Rules
export const listRules=()=>req('/api/rules');
export const createRule=(r)=>post('/api/rules',r);
export const updateRule=(id,r)=>put('/api/rules/'+id,r);
export const deleteRule=(id)=>req('/api/rules/'+id,{method:'DELETE'});

// Foreshadowing
export const listForeshadowings=(tid='')=>req('/api/foreshadowings'+(tid?'?task_id='+tid:''));
export const createForeshadowing=(f)=>post('/api/foreshadowings',f);

// Dialogue
export const dialogueChat=(ctx,msg)=>post('/api/dialogue/chat',{session_context:ctx,user_message:msg});

// AI Detect
export const detectAI=(text)=>post('/api/ai-detect/analyze',{text});

// Items
export const charInventory=(cid)=>req('/api/items/inventory/'+cid);

// Subplots
export const listSubplots=(tid='')=>req('/api/subplots'+(tid?'?task_id='+tid:''));
export const createSubplot=(s)=>post('/api/subplots',s);
export const updateSubplot=(id,s)=>req('/api/subplots/'+id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(s)});
export const deleteSubplot=(id)=>req('/api/subplots/'+id,{method:'DELETE'});

// Map
export const fullMap=(tid)=>req('/api/map/full?task_id='+tid);

// History
export const listHistory=()=>req('/tasks?limit=30');

// Impact
export const changeImpact=(type,name,change)=>req('/api/impact?type='+type+'&name='+encodeURIComponent(name)+'&change='+encodeURIComponent(change));

// Projects
export const createProject=(name='未命名项目')=>post('/api/projects',{name});
export const listProjects=()=>req('/api/projects');
export const getProject=(id)=>req('/api/projects/'+id);
export const deleteProject=(id)=>req('/api/projects/'+id,{method:'DELETE'});
export const saveDraft=(id,draft)=>req('/api/projects/'+id+'/draft',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({draft})});
export const getDraft=(id)=>req('/api/projects/'+id+'/draft');
export const saveWorldSetting=(id,text)=>req('/api/projects/'+id+'/world-setting',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});
export const saveOutlineNodes=(id,nodes)=>post('/api/projects/'+id+'/outline',{nodes});
export const getOutlineNodes=(id)=>req('/api/projects/'+id+'/outline');
export const stageDeleteNode=(id,body)=>post('/api/projects/'+id+'/outline/delete-node',body);
export const undoDeleteNode=(id)=>req('/api/projects/'+id+'/outline/undo-delete',{method:'POST'});
export const getUndoCount=(id)=>req('/api/projects/'+id+'/outline/undo-count');
export const getOutlineVersions=(id)=>req('/api/projects/'+id+'/outline/versions');
export const restoreOutlineVersion=(id,vid)=>req('/api/projects/'+id+'/outline/restore/'+vid,{method:'POST'});

// Factions
export const listFactions=(tid='')=>req('/api/factions?task_id='+tid);
export const createFaction=(f)=>post('/api/factions',f);
export const updateFaction=(id,f)=>req('/api/factions/'+id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(f)});
export const deleteFaction=(id)=>req('/api/factions/'+id,{method:'DELETE'});
export const addFactionMember=(fid,body)=>post('/api/factions/'+fid+'/members',body);
export const removeFactionMember=(fid,name)=>req('/api/factions/'+fid+'/members/'+encodeURIComponent(name),{method:'DELETE'});
export const setFactionRelation=(body)=>post('/api/factions/relations',body);
export const getFactionRelations=(tid='')=>req('/api/factions/relations/list?task_id='+tid);

// Map nodes (P12)
export const createMapNode=(body)=>post('/api/map/nodes',body);
// Items
export const createItem=(body)=>post('/api/items',body);

// Character Relations
export const listRelations=(tid='')=>req('/api/character-relations'+(tid?'?task_id='+tid:''));
export const createRelation=(r)=>post('/api/character-relations',r);
export const updateRelation=(id,r)=>req('/api/character-relations/'+id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(r)});
export const deleteRelation=(id)=>req('/api/character-relations/'+id,{method:'DELETE'});
export const advanceRelationStage=(id,stageIdx,status='done')=>req('/api/character-relations/'+id+'/advance-stage?stage_index='+stageIdx+'&status='+status,{method:'POST'});
export const getRelationPresets=()=>req('/api/character-relations/presets');
