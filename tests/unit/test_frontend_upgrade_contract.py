from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_loaded_frontend_tracks_workspace_and_active_task_separately():
    main_js = (ROOT / "app/static/js/main.js").read_text(encoding="utf-8")

    assert "const taskId = ref(''); const workspaceTaskId = ref('');" in main_js
    assert "task_id:workspaceTaskId.value" in main_js
    assert "if(d.new_task_id)" in main_js
    assert "API.saveDraftBeacon" in main_js
    assert "API.evaluateOutline" in main_js
    assert "API.createExport" in main_js
    assert "switchWorkspace" in main_js
    assert "API.getStateFrame" in main_js
    assert "API.recordItemTransaction" in main_js
    assert "apiKey:apiKey.value" not in main_js
    assert "taskDone:taskDone.value" in main_js
    assert "API.saveWorldSetting" not in main_js
    assert "/api/analysis/evaluate" not in main_js


def test_loaded_template_exposes_statuses_evaluation_and_exports():
    template = (ROOT / "app/static/index.html").read_text(encoding="utf-8")

    assert "{{saveStatusText}}" in template
    assert "{{connectionStatusText}}" in template
    assert "{{projectionStatusText}}" in template
    assert 'showOutlineEval=true' in template
    assert "exportDraft('md')" in template
    assert "exportDraft('txt')" in template
    assert "exportDraft('json')" in template
    assert "故事世界" in template
    assert "质量与状态分析" in template
    assert "项目与导出" in template
    assert "写后分析" in template
    assert "https://unpkg.com" not in template
    assert "/static/vendor/vue.esm-browser.prod.js" in template


def test_loaded_frontend_exposes_connection_retry_control():
    template = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    main_js = (ROOT / "app/static/js/main.js").read_text(encoding="utf-8")

    assert "'/static/js/main.js?v=20260815c'" in template
    assert 'v-if="connectionRetryAvailable"' in template
    assert '@click="retryConnection"' in template
    assert 'createTaskConnectionController' in main_js
    assert 'createTaskPollingSession' in main_js
    assert 'beginPolling(null,true,{manualRetry:true,deferStream})' in main_js


def test_loaded_frontend_preserves_runtime_availability_across_manual_retry():
    main_js = (ROOT / "app/static/js/main.js").read_text(encoding="utf-8")

    assert "let lastRuntimeAvailable = true;" in main_js
    assert "createTaskConnectionController({initialRuntimeAvailable:lastRuntimeAvailable})" in main_js
    assert "lastRuntimeAvailable = d.runtime_available !== false;" in main_js
    assert "lastRuntimeAvailable = status.runtime_available !== false;" in main_js


def test_loaded_frontend_prioritizes_restored_connection_before_auxiliary_hydration():
    main_js = (ROOT / "app/static/js/main.js").read_text(encoding="utf-8")

    mounted = main_js[main_js.index("onMounted(async()=>") :]
    assert mounted.index("await initTaskSession") < mounted.index("void loadCharacters()")
    assert mounted.index("applyRestoredTaskState") < mounted.index("void hydrateTaskSession")
    assert "return {status:null,loadFailed:true};" in main_js


def test_loaded_frontend_retires_and_guards_each_task_restoration():
    main_js = (ROOT / "app/static/js/main.js").read_text(encoding="utf-8")

    assert "let taskRestorationGeneration = 0;" in main_js
    assert "function beginTaskRestoration()" in main_js
    assert "function isCurrentTaskRestoration(generation)" in main_js

    resume = main_js[main_js.index("async function resumeTask") : main_js.index("async function deleteTaskFn")]
    assert resume.index("const generation=beginTaskRestoration();") < resume.index("taskId.value = tid;")
    assert "isCurrentTaskRestoration(generation)" in resume

    project = main_js[main_js.index("async function openProject") : main_js.index("async function archiveProjectFn")]
    assert project.index("const generation=beginTaskRestoration();") < project.index("workspaceTaskId.value=project.workspace_task_id;")
    assert "isCurrentTaskRestoration(generation)" in project


def test_loaded_frontend_centralizes_restored_task_polling_decision():
    main_js = (ROOT / "app/static/js/main.js").read_text(encoding="utf-8")

    assert "const RESTORE_POLLING_INACTIVE_STATUSES = new Set(['completed','failed','error','stopped','draft']);" in main_js
    assert "function applyRestoredTaskState(restoredSession, options=" in main_js
    assert "restoredSession.loadFailed||!RESTORE_POLLING_INACTIVE_STATUSES.has(restoredSession.status?.status)" in main_js
    assert main_js.count("applyRestoredTaskState(restoredSession") >= 4


def test_loaded_frontend_cleans_up_lifecycle_and_uses_current_release_keys():
    template = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    main_js = (ROOT / "app/static/js/main.js").read_text(encoding="utf-8")

    assert "'/static/js/main.js?v=20260815c'" in template
    assert 'href="/static/styles/base.css?v=20260815b"' in template
    assert "import * as API from './api.js?v=20260815b';" in main_js
    assert "from './task-restoration-stream.mjs?v=20260815c';" in main_js
    assert "onUnmounted" in main_js
    assert "clearInterval(autosaveInterval);" in main_js
    assert "window.removeEventListener('beforeunload',onBeforeUnload);" in main_js
    assert "window.removeEventListener('unhandledrejection',onUnhandledRejection);" in main_js
    assert "document.removeEventListener('keydown',onKeydown);" in main_js
    assert "document.removeEventListener('mousemove',onMouseMove);" in main_js
    assert "document.removeEventListener('mouseup',onMouseUp);" in main_js


def test_loaded_frontend_defers_durable_restore_stream_until_hydration():
    main_js = (ROOT / "app/static/js/main.js").read_text(encoding="utf-8")

    assert "createTaskRestorationStreamGate" in main_js
    assert "options={manualRetry:false,deferStream:false}" in main_js
    assert "createTaskRestorationStreamGate({deferred:options.deferStream})" in main_js
    assert "streamGate.isReady()" in main_js
    assert "streamGate.cursor()" in main_js
    assert "streamGate.recordCursor(d.last_id)" in main_js
    assert "buildRestoredDraftBlocks" in main_js
    assert "runDurableHydration" in main_js
    assert "function restoreDurableDraftBlocks(generation, capturedWorkspaceId)" in main_js
    assert main_js.count("applyRestoredTaskState(restoredSession,{deferStream:true})") == 2
    assert main_js.count("void restoreDurableDraftBlocks(generation, capturedWorkspaceId)") == 2
    assert "releaseRestoredTaskStream(generation)" in main_js
    assert "workspaceTaskId.value===capturedWorkspaceId" in main_js
    assert "function waitForRestoredStatus(generation)" in main_js
    assert main_js.count("restoredSession.loadFailed&&!await waitForRestoredStatus(generation)") == 2
    assert "activePollingSession.resolveStatusReady?.(false);" in main_js
    assert "session.resolveStatusReady(true)" in main_js
    mounted = main_js[main_js.index("onMounted(async()=>") :]
    assert "applyRestoredTaskState(restoredSession)" in mounted


def test_loaded_frontend_does_not_resume_mount_work_after_unmount():
    main_js = (ROOT / "app/static/js/main.js").read_text(encoding="utf-8")

    assert "let lifecycleActive = true;" in main_js
    mounted = main_js[main_js.index("onMounted(async()=>") : main_js.index("onUnmounted(")]
    assert mounted.index("await initTaskSession") < mounted.index("if(!lifecycleActive||!isCurrentTaskRestoration(generation))return;")
    assert mounted.index("if(!lifecycleActive||!isCurrentTaskRestoration(generation))return;") < mounted.index("void loadCharacters()")
    unmounted = main_js[main_js.index("onUnmounted(") :]
    assert unmounted.index("lifecycleActive=false;") < unmounted.index("stopPolling()")
