const BASE = window.location.origin;
let _apiKey = '';
const DEFAULT_TIMEOUT_MS = 120000;
const DEFAULT_RETRIES = 0;
const RETRYABLE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

export function setApiKey(k) { _apiKey = k || ''; }

export class ApiError extends Error {
  constructor(message, { status=0, detail=null, retryable=false, url='', code='', fields=null, requestId='', retryAfter=null, cancelled=false, timedOut=false }={}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.retryable = retryable;
    this.url = url;
    this.code = code || '';
    this.fields = fields || null;
    this.requestId = requestId || '';
    this.retryAfter = retryAfter;
    this.cancelled = cancelled;
    this.timedOut = timedOut;
  }
}

function _hdrs(opts) {
  const h = { ...(opts.headers || {}) };
  if (_apiKey) h['X-API-Key'] = _apiKey;
  if (!h['X-Request-ID']) {
    h['X-Request-ID'] = typeof crypto?.randomUUID === 'function'
      ? crypto.randomUUID()
      : `writer-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }
  return h;
}

function _header(response, name) {
  return response.headers?.get?.(name) || '';
}

function _errorPayload(payload) {
  if (!payload || typeof payload !== 'object') return {detail: payload, code: '', fields: null};
  const raw = payload.detail ?? payload.error ?? payload;
  const detail = raw;
  const message = raw && typeof raw === 'object' && 'message' in raw ? raw.message : raw;
  const code = payload.code || (raw && typeof raw === 'object' ? raw.code : '') || '';
  const fields = payload.fields || (raw && typeof raw === 'object' ? raw.fields : null) || null;
  return {detail, message, code, fields};
}

function _retryDelay(attempt, retryAfter, baseDelay) {
  const serverDelay = Number(retryAfter);
  if (Number.isFinite(serverDelay) && serverDelay >= 0) return serverDelay * 1000;
  return Math.min(baseDelay * (2 ** attempt), 5000);
}

function _sleep(ms, signal) {
  if (!ms) return Promise.resolve();
  return new Promise((resolve, reject) => {
    let timer = setTimeout(done, ms);
    function done() {
      if (signal) signal.removeEventListener('abort', abort);
      resolve();
    }
    if (!signal) return;
    const abort = () => { clearTimeout(timer); signal.removeEventListener('abort', abort); reject(new DOMException('aborted', 'AbortError')); };
    if (signal.aborted) abort();
    else signal.addEventListener('abort', abort, {once:true});
  });
}

async function req(url, opts={}) {
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const method = String(opts.method || 'GET').toUpperCase();
  const retries = Math.max(0, Number(opts.retries ?? (RETRYABLE_METHODS.has(method) ? DEFAULT_RETRIES : 0)));
  const retryDelayMs = Math.max(0, Number(opts.retryDelayMs ?? 250));
  const retryOn = opts.retryOn || ((error) => error?.retryable === true);
  const externalSignal = opts.signal;
  let lastError = null;

  for (let attempt = 0; attempt <= retries; attempt += 1) {
    const controller = new AbortController();
    let timedOut = false;
    const timer = timeoutMs > 0 ? setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs) : null;
    const abortFromExternal = () => controller.abort();
    if (externalSignal) {
      if (externalSignal.aborted) controller.abort();
      else externalSignal.addEventListener('abort', abortFromExternal, {once:true});
    }
    const fetchOpts = {...opts, method, headers:_hdrs(opts), signal:controller.signal};
    delete fetchOpts.timeoutMs; delete fetchOpts.retries; delete fetchOpts.retryDelayMs; delete fetchOpts.retryOn;
    try {
      const r = await fetch(BASE + url, fetchOpts);
      const contentType = _header(r, 'content-type');
      let payload = null;
      if (r.status !== 204) {
        if (contentType.includes('application/json')) payload = await r.json();
        else payload = await r.text();
      }
      if (!r.ok) {
        const parsed = _errorPayload(payload);
        const message = typeof parsed.message === 'string'
          ? parsed.message
          : parsed.message ? JSON.stringify(parsed.message) : `请求失败 (${r.status})`;
        throw new ApiError(message, {
          status:r.status,
          detail:parsed.detail,
          code:parsed.code,
          fields:parsed.fields,
          retryable:r.status === 408 || r.status === 425 || r.status === 429 || r.status >= 500,
          retryAfter:_header(r, 'retry-after'),
          requestId:_header(r, 'x-request-id') || _header(r, 'x-correlation-id'),
          url,
        });
      }
      return payload;
    } catch (error) {
      if (error instanceof ApiError) lastError = error;
      else if (error?.name === 'AbortError') {
        const cancelled = Boolean(externalSignal?.aborted) && !timedOut;
        const knownTimeout = timedOut;
        lastError = new ApiError(cancelled ? '请求已取消' : (knownTimeout ? '请求超时' : '请求超时或已取消'), {
          retryable:!cancelled, cancelled, timedOut:knownTimeout, url,
        });
      } else {
        lastError = new ApiError(error?.message || '网络连接失败', {retryable:true, url});
      }
    } finally {
      if (timer) clearTimeout(timer);
      if (externalSignal) externalSignal.removeEventListener('abort', abortFromExternal);
    }
    if (!lastError || attempt >= retries || !retryOn(lastError, attempt)) throw lastError;
    try { await _sleep(_retryDelay(attempt, lastError.retryAfter, retryDelayMs), externalSignal); }
    catch (error) {
      throw new ApiError('请求已取消', {retryable:false, cancelled:true, url});
    }
  }
  throw lastError || new ApiError('请求失败', {url});
}
export { req, req as request };
function post(url,body,options={}){return req(url,{...options,method:'POST',headers:{'Content-Type':'application/json',...(options.headers||{})},body:JSON.stringify(body)})}
function put(url,body,options={}){return req(url,{...options,method:'PUT',headers:{'Content-Type':'application/json',...(options.headers||{})},body:JSON.stringify(body)})}

// Writing
export const createTask=()=>post('/tasks',{});
export const startWriting=(body,mode)=>req('/write?mode='+mode,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
export const getStatus=(id,options={})=>req('/status/'+id,{timeoutMs:15000,signal:options.signal});
export const getStream=(id,lastId,options={})=>req('/stream/'+id+'?last_id='+encodeURIComponent(lastId)+'&count=50',{timeoutMs:15000,signal:options.signal});
export const sendDecision=(id,phase,action,fb='')=>req('/tasks/'+id+'/decide?phase='+phase+'&action='+action+'&feedback='+encodeURIComponent(fb),{method:'POST'});
export const continueWriting=(id,body)=>req('/tasks/'+id+'/continue',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
export const reviseSubsection=(id,body)=>post('/tasks/'+id+'/revise',body);
export const patchDraftSubsection=(id,section,subsection,body)=>req('/tasks/'+id+'/draft/sections/'+section+'/'+subsection,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
export const getDraftVersions=(id,section,subsection)=>req('/tasks/'+id+'/draft/versions?section='+section+'&subsection='+subsection);
export const restoreDraftVersion=(id,versionId)=>req('/tasks/'+id+'/draft/versions/'+versionId+'/restore',{method:'POST'});
export const updateOutline=(id,outline)=>post('/tasks/'+id+'/update-outline',{outline});
export const deleteTask=(id)=>req('/tasks/'+id,{method:'DELETE'});
export const getWorkspace=(id)=>req('/tasks/'+id+'/workspace');
export const patchWorkspace=(id,body)=>req('/tasks/'+id+'/workspace',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
export const listProjects=(includeArchived=false)=>req('/projects?include_archived='+includeArchived);
export const archiveProject=(id,archived=true)=>req('/projects/'+id+'/archive',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({archived})});
export const createExport=(id,format='md',options={})=>post('/tasks/'+id+'/exports',{format},options);
export const listExports=(id)=>req('/tasks/'+id+'/exports');
export const exportDownloadUrl=(id,exportId)=>BASE+'/tasks/'+id+'/exports/'+exportId+'/download';
export const reviewContinuity=(id,options={})=>post('/tasks/'+id+'/review/continuity',{},options);
export const getTaskEvents=(id)=>req('/tasks/'+id+'/events');
export const analyzeTask=(id,options={})=>post('/tasks/'+id+'/analyze',{},options);
export const getStateFrame=(id,section,subsection,options={})=>req('/tasks/'+id+'/state-frame/'+section+'/'+subsection,options);

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
export const recordItemTransaction=(body)=>post('/api/items/transactions',body);

// Subplots
export const listSubplots=(tid='')=>req('/api/subplots'+(tid?'?task_id='+tid:''));
export const createSubplot=(s)=>post('/api/subplots',s);
export const updateSubplot=(id,s)=>req('/api/subplots/'+id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(s)});
export const deleteSubplot=(id)=>req('/api/subplots/'+id,{method:'DELETE'});
export const getSubplotHeatMap=(tid,total=50)=>req('/api/subplots/heat-map?task_id='+tid+'&total_chapters='+total);

// Map
export const fullMap=(tid)=>req('/api/map/full?task_id='+tid);
export const createMapEdge=(body)=>post('/api/map/edges',body);
export const getMapRoute=(tid)=>req('/api/map/route?task_id='+tid);
export const setMapRoute=(body)=>post('/api/map/route',body);


// Impact
export const changeImpact=(type,name,change)=>req('/api/impact?type='+type+'&name='+encodeURIComponent(name)+'&change='+encodeURIComponent(change));

// Projects (legacy — now task-scoped)
export const listHistory=()=>req('/tasks');
export const saveDraft=(id,draft)=>req('/tasks/'+id+'/draft',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({draft})});
export const getDraft=(id)=>req('/tasks/'+id+'/draft');
export const saveOutlineNodes=(id,nodes)=>post('/tasks/'+id+'/outline',{nodes});
export const getOutlineNodes=(id)=>req('/tasks/'+id+'/outline');
export const getOutlineBudgetAdvice=(id,body)=>post('/tasks/'+id+'/outline/budget-advice',body);
export const evaluateOutline=(id,body)=>post('/tasks/'+id+'/outline/evaluate',body);
export const previewArcProjection=(id,body)=>post('/tasks/'+id+'/outline/arc-projection/preview',body);
export const confirmArcProjection=(id,body)=>post('/tasks/'+id+'/outline/arc-projection/confirm',body);
export const getOutlineVersions=(id)=>req('/tasks/'+id+'/outline/versions');
export const restoreOutlineVersion=(id,vid)=>req('/tasks/'+id+'/outline/restore/'+vid,{method:'POST'});
export const stageDeleteNode=(id,body)=>post('/tasks/'+id+'/outline/delete-node',body);
export const undoDeleteNode=(id)=>req('/tasks/'+id+'/outline/undo-delete',{method:'POST'});
export const getUndoCount=(id)=>req('/tasks/'+id+'/outline/undo-count');
export const saveDraftBeacon=(id,draft)=>{
  const body = new Blob([JSON.stringify({draft})], {type:'application/json'});
  return navigator.sendBeacon('/tasks/'+id+'/draft/beacon', body);
};

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
