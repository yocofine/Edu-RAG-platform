<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { api, downloadUrl, previewUrl } from './api'
import ChatMessage from './components/ChatMessage.vue'
import ConversationHistory from './components/ConversationHistory.vue'
import UiIcon from './components/UiIcon.vue'

const nav = [
  ['chat', 'chat', '智能搜索'], ['files', 'files', '全部文件'], ['rules', 'rules', '规则通知'],
  ['scripts', 'scripts', '话术库'], ['analysis', 'analysis', 'AI分析中心'], ['logs', 'logs', '操作日志'],
  ['settings', 'settings', '系统设置'], ['members', 'members', '知识库成员'],
]
const state = reactive({ user: null, view: 'chat', sections: [], documents: [], recent: [], scripts: [], rules: [], jobs: [], logs: [], members: [], keywords: [], stats: {} })
const auth = reactive({ mode: 'login', username: '', password: '', error: '', busy: false })
const query = ref('')
const messages = ref([])
const conversations = ref([])
const activeConversationId = ref('')
const historyOpen = ref(false)
const historyLoading = ref(false)
const sending = ref(false)
const sidebarOpen = ref(false)
const uploadDialog = ref(null)
const previewDialog = ref(null)
const previewItem = ref(null)
const versionsDialog = ref(null)
const contentDialog = ref(null)
const passwordDialog = ref(null)
const versions = ref([])
const versionDocument = ref(null)
const selectedSection = ref('')
const selectedFileIds = ref([])
const fileSearch = ref('')
const contentSearch = ref('')
const activeContentGroup = ref('')
const sectionDialog = ref(null)
const moveDialog = ref(null)
const documentDialog = ref(null)
const confirmDialog = ref(null)
const sectionForm = reactive({ id: '', name: '', busy: false, error: '' })
const moveForm = reactive({ ids: [], sectionId: '', busy: false, error: '' })
const documentForm = reactive({ id: '', name: '', busy: false, error: '' })
const confirmForm = reactive({ title: '', message: '', busy: false, error: '' })
const logFilter = reactive({ operator: '', action: '' })
const editor = reactive({ kind: 'scripts', id: '', title: '', content: '', group_name: '默认分组', tags: '', busy: false, error: '', analysis: '' })
const passwordForm = reactive({ old: '', next: '', confirm: '', busy: false, error: '' })
const transcript = ref(null)
const upload = reactive({ file: null, sectionId: '', tags: '', busy: false, error: '' })
const undoSession = ref(null)
let undoTimer
let events
let confirmedAction = null

const pageTitle = computed(() => nav.find(item => item[0] === state.view)?.[2] || '教辅知识库')
const counts = computed(() => ({ files: state.stats.documents || 0, rules: state.stats.rules || 0, scripts: state.stats.scripts || 0 }))
const filteredDocuments = computed(() => {
  const keyword = fileSearch.value.trim().toLowerCase()
  return keyword ? state.documents.filter(item => `${item.name} ${item.tags} ${item.uploader}`.toLowerCase().includes(keyword)) : state.documents
})
const currentContent = computed(() => state.view === 'scripts' ? state.scripts : state.rules)
const contentGroups = computed(() => [...new Set(currentContent.value.map(item => item.group_name || '默认分组'))])
const filteredContent = computed(() => {
  const keyword = contentSearch.value.trim().toLowerCase()
  return currentContent.value.filter(item => (!activeContentGroup.value || item.group_name === activeContentGroup.value) && (!keyword || `${item.title} ${item.content} ${item.tags}`.toLowerCase().includes(keyword)))
})

function formatTime(value) {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(value))
}
function statusText(status) {
  return ({ queued: '等待处理', detecting: '正在检测', extracting_text: '提取文本', ocr_processing: 'OCR识别', mineru_parsing: '复杂解析', indexing: '建立索引', published: '已发布', failed: '处理失败', needs_review: '等待复核', interrupted: '任务中断' })[status] || status
}
function parsePayload(value) { try { return typeof value === 'string' ? JSON.parse(value) : (value || {}) } catch { return {} } }
function scrollEnd() { nextTick(() => transcript.value?.scrollTo({ top: transcript.value.scrollHeight, behavior: 'smooth' })) }

async function initialize() {
  const [sections, recent, stats, threads] = await Promise.all([api.sections(), api.recent(), api.stats(), api.conversations()])
  state.sections = sections.items; state.recent = recent.items; state.stats = stats; conversations.value = threads.items
  if (!upload.sectionId && state.sections.length) upload.sectionId = state.sections[0].id
  connectEvents()
}
async function authenticate() {
  auth.busy = true; auth.error = ''
  try {
    const result = auth.mode === 'login' ? await api.login(auth.username, auth.password) : await api.register(auth.username, auth.password)
    state.user = result.user; await initialize()
  } catch (error) { auth.error = error.message } finally { auth.busy = false }
}
async function logout() { await api.logout(); state.user = null; messages.value = []; events?.close() }

function connectEvents() {
  events?.close()
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  events = new WebSocket(`${protocol}//${location.host}/api/v1/events/ws`)
  events.onopen = () => events.send('ready')
  events.onmessage = async event => {
    const payload = JSON.parse(event.data)
    if (['document_uploaded', 'document_published', 'ingestion_status_changed'].includes(payload.type)) {
      state.recent = (await api.recent()).items; state.stats = await api.stats()
    }
  }
}

async function changeView(view) {
  state.view = view; sidebarOpen.value = false
  if (view === 'scripts' || view === 'rules') { activeContentGroup.value = ''; contentSearch.value = '' }
  try {
    if (view === 'files') await loadDocuments()
    if (view === 'scripts') state.scripts = (await api.scripts()).items
    if (view === 'rules') state.rules = (await api.rules()).items
    if (view === 'analysis') state.jobs = (await api.jobs()).items
    if (view === 'logs' && state.user.role === 'admin') await loadLogs()
    if (view === 'members' && state.user.role === 'admin') state.members = (await api.members()).items
  } catch (error) { messages.value.push({ role: 'assistant', content: error.message, state: 'error' }) }
}

async function loadDocuments() { state.documents = (await api.documents(selectedSection.value)).items; selectedFileIds.value = [] }
async function selectSection(id) { selectedSection.value = id; await loadDocuments() }
function createSection() { Object.assign(sectionForm, { id: '', name: '', error: '' }); sectionDialog.value?.showModal() }
function renameSection(item) { Object.assign(sectionForm, { id: item.id, name: item.name, error: '' }); sectionDialog.value?.showModal() }
async function saveSection() {
  if (!sectionForm.name.trim()) { sectionForm.error = '请输入板块名称'; return }
  sectionForm.busy = true; sectionForm.error = ''
  try { sectionForm.id ? await api.renameSection(sectionForm.id, sectionForm.name.trim()) : await api.createSection(sectionForm.name.trim()); state.sections = (await api.sections()).items; sectionDialog.value?.close() }
  catch (error) { sectionForm.error = error.message } finally { sectionForm.busy = false }
}
function askConfirm(title, message, action) { Object.assign(confirmForm, { title, message, error: '' }); confirmedAction = action; confirmDialog.value?.showModal() }
async function runConfirmedAction() { confirmForm.busy = true; confirmForm.error = ''; try { await confirmedAction?.(); confirmDialog.value?.close() } catch (error) { confirmForm.error = error.message } finally { confirmForm.busy = false } }
function deleteSection(item) { askConfirm('删除板块', `确定删除板块“${item.name}”吗？板块中存在文件时不会执行删除。`, async () => { await api.deleteSection(item.id); if (selectedSection.value === item.id) selectedSection.value = ''; state.sections = (await api.sections()).items; await loadDocuments() }) }
function toggleFile(id) { selectedFileIds.value = selectedFileIds.value.includes(id) ? selectedFileIds.value.filter(item => item !== id) : [...selectedFileIds.value, id] }
function renameFile(item) { Object.assign(documentForm, { id: item.id, name: item.name, error: '' }); documentDialog.value?.showModal() }
async function saveDocumentName() {
  if (!documentForm.name.trim()) { documentForm.error = '请输入文件名'; return }
  documentForm.busy = true; documentForm.error = ''
  try { await api.updateDocument(documentForm.id, { name: documentForm.name.trim() }); documentDialog.value?.close(); await loadDocuments() }
  catch (error) { documentForm.error = error.message } finally { documentForm.busy = false }
}
function moveFiles(ids) { Object.assign(moveForm, { ids: [...ids], sectionId: state.sections.find(item => item.id !== selectedSection.value)?.id || '', error: '' }); moveDialog.value?.showModal() }
async function confirmMove() {
  if (!moveForm.sectionId) { moveForm.error = '请选择目标板块'; return }
  moveForm.busy = true; moveForm.error = ''
  try { await api.batchMoveDocuments(moveForm.ids, moveForm.sectionId); moveDialog.value?.close(); await loadDocuments(); state.recent = (await api.recent()).items }
  catch (error) { moveForm.error = error.message } finally { moveForm.busy = false }
}
function deleteFiles(ids) { if (ids.length) askConfirm('删除文件', `确定将 ${ids.length} 个文件移入回收站吗？`, async () => { await api.batchDeleteDocuments(ids); await loadDocuments(); state.recent = (await api.recent()).items }) }
async function showVersions(item) { versionDocument.value = item; versions.value = (await api.versions(item.id)).items; versionsDialog.value?.showModal() }
async function rollback(item) { await api.rollbackVersion(versionDocument.value.id, item.version_id); versionsDialog.value?.close(); await loadDocuments() }

function openContentEditor(kind, item = null) {
  Object.assign(editor, { kind, id: item?.id || '', title: item?.title || '', content: item?.content || '', group_name: item?.group_name || (kind === 'scripts' ? '默认分组' : '通知规则'), tags: item?.tags || '', error: '', analysis: '' })
  contentDialog.value?.showModal()
}
function createContentGroup(kind) { activeContentGroup.value = ''; openContentEditor(kind); nextTick(() => document.querySelector('.editor-dialog input')?.focus()) }
async function saveEditor() {
  if (!editor.title.trim() || !editor.content.trim()) { editor.error = '标题和内容不能为空'; return }
  editor.busy = true; editor.error = ''
  try {
    await api.saveContent(editor.kind, { id: editor.id, title: editor.title, content: editor.content, group_name: editor.group_name, tags: editor.tags })
    state[editor.kind] = (await api[editor.kind]()).items; contentDialog.value?.close()
  } catch (error) { editor.error = error.message } finally { editor.busy = false }
}
async function analyzeEditor() { editor.analysis = '分析中…'; try { editor.analysis = (await api.analyzeContent(editor.content)).analysis } catch (error) { editor.analysis = error.message } }
function deleteContent(kind, item) { askConfirm('删除内容', `确定删除“${item.title}”吗？`, async () => { await api.deleteContent(kind, item.id); state[kind] = (await api[kind]()).items }) }
async function copyContent(item) { await navigator.clipboard.writeText(item.content.replace(/<[^>]+>/g, ' ')) }
async function loadLogs() { state.logs = (await api.logs(logFilter.operator, logFilter.action)).items }
function clearLogs() { askConfirm('清空日志', '确定清空全部操作日志吗？此操作不可撤销。', async () => { await api.clearLogs(); await loadLogs() }) }
async function retryJob(item) { await api.retryJob(item.id); state.jobs = (await api.jobs()).items }
async function setRole(item, role) { await api.setMemberRole(item.id, role); state.members = (await api.members()).items }
async function loadKeywords() { state.keywords = (await api.keywordCloud()).items }
function openPassword() { Object.assign(passwordForm, { old: '', next: '', confirm: '', error: '' }); passwordDialog.value?.showModal() }
async function changePassword() {
  if (passwordForm.next !== passwordForm.confirm) { passwordForm.error = '两次输入的新密码不一致'; return }
  passwordForm.busy = true; passwordForm.error = ''
  try { await api.changePassword(passwordForm.old, passwordForm.next); passwordDialog.value?.close() }
  catch (error) { passwordForm.error = error.message } finally { passwordForm.busy = false }
}

async function refreshConversations() { conversations.value = (await api.conversations()).items }
async function newConversation() {
  const result = await api.createConversation()
  activeConversationId.value = result.item.id; messages.value = []; historyOpen.value = false
  await refreshConversations(); nextTick(() => document.querySelector('.composer-input')?.focus())
}
async function selectConversation(id) {
  historyLoading.value = true
  try {
    const result = await api.conversationMessages(id)
    activeConversationId.value = id
    messages.value = result.items.map(item => {
      const payload = parsePayload(item.payload)
      return { ...item, files: payload.items || [], citations: payload.citations || [], metrics: payload.metrics || null, state: payload.type === 'error' ? 'error' : 'default' }
    })
    historyOpen.value = false; scrollEnd()
  } finally { historyLoading.value = false }
}
async function renameConversation(id, title) { await api.renameConversation(id, title); await refreshConversations() }
async function deleteConversation(id) {
  const removed = conversations.value.find(item => item.id === id)
  await api.deleteConversation(id)
  conversations.value = conversations.value.filter(item => item.id !== id)
  if (activeConversationId.value === id) { activeConversationId.value = ''; messages.value = [] }
  undoSession.value = removed
  clearTimeout(undoTimer); undoTimer = setTimeout(() => { undoSession.value = null }, 7000)
}
async function undoDelete() {
  if (!undoSession.value) return
  await api.restoreConversation(undoSession.value.id)
  undoSession.value = null; await refreshConversations()
}

async function sendQuery() {
  const content = query.value.trim()
  if (!content || sending.value) return
  query.value = ''; sending.value = true
  messages.value.push({ id: `local-${Date.now()}`, role: 'user', content, created_at: new Date().toISOString() })
  const pending = { id: `pending-${Date.now()}`, role: 'assistant', content: '', created_at: new Date().toISOString(), state: 'loading' }
  messages.value.push(pending); scrollEnd()
  try {
    const index = messages.value.indexOf(pending)
    await api.chatStream(content, activeConversationId.value || null, event => {
      const message = messages.value[index]
      if (event.type === 'session') activeConversationId.value = event.session_id
      else if (event.type === 'status') message.status = event.message
      else if (event.type === 'intent') message.intent = event.intent
      else if (event.type === 'token') {
        message.state = 'streaming'
        message.content += event.content || ''
        scrollEnd()
      } else if (event.type === 'files') message.files = event.items || []
      else if (event.type === 'citations') message.citations = event.items || []
      else if (event.type === 'error') { message.state = 'error'; message.content = event.message }
      else if (event.type === 'end') {
        if (event.answer) message.content = event.answer
        message.metrics = { processing_time: event.processing_time, token_usage: event.token_usage }
        message.state = 'default'
      }
    })
    await refreshConversations()
  } catch (error) {
    const index = messages.value.indexOf(pending)
    messages.value[index] = { ...pending, content: error.message, state: 'error' }
  } finally { sending.value = false; scrollEnd() }
}

function openUpload() { upload.error = ''; uploadDialog.value?.showModal() }
async function uploadFile() {
  if (!upload.file || !upload.sectionId) return
  upload.busy = true; upload.error = ''
  const form = new FormData(); form.append('file', upload.file); form.append('section_id', upload.sectionId); form.append('tags', upload.tags)
  try { await api.upload(form); uploadDialog.value?.close(); upload.file = null; upload.tags = ''; state.recent = (await api.recent()).items }
  catch (error) { upload.error = error.message } finally { upload.busy = false }
}
function normalizedDocument(item) {
  const metadata = item?.metadata || {}
  return {
    ...item,
    id: item?.id || item?.document_id || metadata.document_id,
    version_id: item?.version_id || metadata.document_version_id,
    name: item?.name || item?.file_name || metadata.file_name || metadata.source || '知识库资料',
    page_number: Number(item?.page_number || metadata.page_number || 0) || null,
  }
}
function openPreview(item) {
  const target = normalizedDocument(item)
  if (!target.id || !target.version_id) return
  previewItem.value = target
  previewDialog.value?.showModal()
}
function previewFrameUrl(item) {
  if (!item?.id || !item?.version_id) return ''
  const url = previewUrl(item.id, item.version_id)
  return item.page_number ? `${url}#page=${item.page_number}` : url
}
function download(item) {
  const target = normalizedDocument(item)
  if (target.id && target.version_id) window.open(downloadUrl(target.id, target.version_id), '_blank')
}

onMounted(async () => { try { state.user = (await api.me()).user; await initialize() } catch {} })
onBeforeUnmount(() => { events?.close(); clearTimeout(undoTimer) })
</script>

<template>
  <div v-if="!state.user" class="auth-page">
    <form class="auth-panel" :data-state="auth.error ? 'error' : auth.busy ? 'loading' : 'default'" @submit.prevent="authenticate">
      <div class="brand-lockup"><span class="brand-mark"><UiIcon name="book" :size="22" /></span><div><strong>教辅知识库</strong><small>INTERNAL KNOWLEDGE</small></div></div>
      <div class="auth-copy"><h1>{{ auth.mode === 'login' ? '登录工作台' : '创建内部账号' }}</h1><p>检索文件、话术和规则，历史会话仅对你可见。</p></div>
      <label>用户名<input v-model="auth.username" autocomplete="username" required /></label>
      <label>密码<input v-model="auth.password" type="password" autocomplete="current-password" minlength="8" required /></label>
      <p v-if="auth.error" class="form-error" role="alert">{{ auth.error }}</p>
      <button class="button button--primary button--full" :disabled="auth.busy">{{ auth.busy ? '正在验证…' : auth.mode === 'login' ? '登录知识库' : '创建账号' }}</button>
      <button type="button" class="text-action" @click="auth.mode = auth.mode === 'login' ? 'register' : 'login'">{{ auth.mode === 'login' ? '创建新账号' : '返回登录' }}</button>
    </form>
  </div>

  <div v-else class="app-shell" :data-view="state.view">
    <button class="mobile-menu icon-button" aria-label="打开导航" @click="sidebarOpen=true"><UiIcon name="menu" /></button>
    <aside class="app-nav" :class="{ open: sidebarOpen }">
      <div class="brand-lockup"><span class="brand-mark"><UiIcon name="book" :size="20" /></span><div><strong>教辅知识库</strong><small>KNOWLEDGE DESK</small></div></div>
      <nav aria-label="主导航">
        <button v-for="item in nav" :key="item[0]" :class="{ active: state.view === item[0] }" @click="changeView(item[0])"><UiIcon :name="item[1]" /><span>{{ item[2] }}</span><b v-if="counts[item[0]]">{{ counts[item[0]] }}</b></button>
      </nav>
      <div class="identity-strip"><span class="user-avatar">{{ state.user.username.slice(0,1).toUpperCase() }}</span><div><strong>{{ state.user.username }}</strong><small>{{ state.user.role === 'admin' ? '管理员' : '普通成员' }}</small></div><button class="mini-action" @click="logout">退出</button></div>
    </aside>
    <button v-if="sidebarOpen" class="nav-scrim" aria-label="关闭导航" @click="sidebarOpen=false"></button>

    <main class="workspace" :data-view="state.view">
      <header class="workspace-head"><div><span class="mono-label">EDU RAG / {{ state.view.toUpperCase() }}</span><h1>{{ pageTitle }}</h1></div><div class="head-actions"><button v-if="state.view==='chat'" class="button button--primary" @click="newConversation"><UiIcon name="plus" />新会话</button></div></header>

      <section v-if="state.view === 'chat'" class="conversation-workbench">
        <div class="conversation-column">
          <div ref="transcript" class="transcript" aria-live="polite">
            <div v-if="!messages.length" class="conversation-empty">
              <span class="empty-mark"><UiIcon name="assistant" :size="26" /></span>
              <h2>从资料开始，不从猜测开始。</h2>
              <p>询问知识库内容，或直接说“帮我找最新上传的文件”。</p>
              <div class="prompt-list"><button @click="query='帮我找一下最新上传的文件'">查找最新文件</button><button @click="query='总结最近的规则通知'">总结规则通知</button><button @click="query='知识库中有哪些资料'">浏览知识范围</button></div>
            </div>
            <ChatMessage v-for="message in messages" :key="message.id" :message="message" @preview="openPreview" @download="download" />
          </div>
          <form class="composer" :data-state="sending ? 'loading' : 'default'" @submit.prevent="sendQuery">
            <label for="assistant-query">发送给知识助手</label>
            <div class="composer-row"><textarea id="assistant-query" v-model="query" class="composer-input" rows="1" placeholder="例如：帮我找最新上传的培训资料" :disabled="sending" @keydown.enter.exact.prevent="sendQuery"></textarea><button class="send-button" :disabled="sending || !query.trim()" aria-label="发送消息"><UiIcon name="send" /></button></div>
            <small>Enter发送 · Shift + Enter换行 · 回答仅依据已发布资料</small>
          </form>
        </div>
        <aside class="context-rail">
          <section class="activity-rail"><header><div><span class="mono-label">LIVE ACTIVITY</span><h2>最近上传</h2></div><span class="count-readout">{{ state.recent.length }}</span></header><div v-if="!state.recent.length" class="empty-state compact"><UiIcon name="files" /><strong>暂无上传记录</strong><p>上传后的文件会实时出现在这里。</p></div><button v-for="item in state.recent" :key="item.version_id" class="activity-item" @click="openPreview(item)"><UiIcon name="files" /><div><strong :title="item.name">{{ item.name }}</strong><small>{{ item.uploader }} · {{ formatTime(item.uploaded_at) }}</small><span :data-status="item.ingestion_status">{{ statusText(item.ingestion_status) }}</span></div></button></section>
          <ConversationHistory :open="historyOpen" :sessions="conversations" :active-id="activeConversationId" :loading="historyLoading" @close="historyOpen=false" @new="newConversation" @select="selectConversation" @rename="renameConversation" @delete="deleteConversation" />
        </aside>
      </section>

      <section v-else class="content-workbench">
        <div v-if="state.view === 'files'" class="manage-layout">
          <aside class="section-manager"><button :class="{ active: !selectedSection }" @click="selectSection('')">全部文件 <b>{{ state.stats.documents || 0 }}</b></button><div v-for="section in state.sections" :key="section.id" class="section-row"><button :class="{ active: selectedSection === section.id }" @click="selectSection(section.id)">{{ section.name }}</button><button class="mini-action" @click="renameSection(section)">改名</button><button class="mini-action danger" @click="deleteSection(section)">删除</button></div><button class="button button--secondary button--full" @click="createSection"><UiIcon name="plus" />新建板块</button></aside>
          <div class="manage-surface"><div class="manage-toolbar"><input v-model="fileSearch" class="manage-search" placeholder="搜索文件名、标签或上传者" /><span>已选择 {{ selectedFileIds.length }} 项</span><button class="button button--secondary" :disabled="!selectedFileIds.length" @click="moveFiles(selectedFileIds)">移动</button><button class="button button--secondary" :disabled="!selectedFileIds.length" @click="deleteFiles(selectedFileIds)">批量删除</button><button class="button button--primary" @click="openUpload"><UiIcon name="upload" />上传文件</button></div><div class="file-grid"><article v-for="item in filteredDocuments" :key="item.version_id" class="file-tile" :data-selected="selectedFileIds.includes(item.id)"><label class="file-check"><input type="checkbox" :checked="selectedFileIds.includes(item.id)" @change="toggleFile(item.id)" />选择</label><button class="file-open" @click="openPreview(item)"><UiIcon name="files" :size="24" /><strong>{{ item.name }}</strong><span>{{ item.section_name }} · v{{ item.version_no }}</span><small>{{ item.uploader }} · {{ formatTime(item.uploaded_at) }}</small></button><div class="tile-actions"><button class="mini-action" @click="renameFile(item)">重命名</button><button class="mini-action" @click="moveFiles([item.id])">移动</button><button class="mini-action" @click="showVersions(item)">版本</button><button class="mini-action danger" @click="deleteFiles([item.id])">删除</button></div></article></div></div>
        </div>
        <div v-else-if="state.view === 'scripts' || state.view === 'rules'" class="content-manage-layout"><aside class="content-group-nav"><button :class="{ active: !activeContentGroup }" @click="activeContentGroup=''">全部 <b>{{ currentContent.length }}</b></button><button v-for="group in contentGroups" :key="group" :class="{ active: activeContentGroup === group }" @click="activeContentGroup=group">{{ group }} <b>{{ currentContent.filter(item => item.group_name === group).length }}</b></button></aside><div class="manage-surface"><div class="manage-toolbar"><input v-model="contentSearch" class="manage-search" :placeholder="`搜索${state.view === 'scripts' ? '话术' : '规则'}…`" /><button class="button button--secondary" @click="createContentGroup(state.view)"><UiIcon name="plus" />新建分组内容</button><button class="button button--primary" @click="openContentEditor(state.view)"><UiIcon name="plus" />新建{{ state.view === 'scripts' ? '话术' : '规则' }}</button></div><section v-for="group in contentGroups.filter(name => !activeContentGroup || name === activeContentGroup)" :key="group" class="content-group-section"><header><h2>{{ group }}</h2><span>{{ filteredContent.filter(item => item.group_name === group).length }} 条</span></header><div class="record-grid"><article v-for="item in filteredContent.filter(item => item.group_name === group)" :key="item.id"><div><h3>{{ item.title }}</h3><p>{{ item.content.replace(/<[^>]+>/g,' ') }}</p><small>{{ item.tags || '无标签' }} · {{ item.owner }} · {{ formatTime(item.updated_at) }}</small></div><div class="record-actions"><button class="mini-action" @click="copyContent(item)">复制</button><button class="mini-action" @click="openContentEditor(state.view,item)">编辑</button><button class="mini-action danger" @click="deleteContent(state.view,item)">删除</button></div></article></div></section></div></div>
        <div v-else-if="state.view === 'analysis'" class="manage-surface"><div class="manage-toolbar"><strong>入库任务</strong><button class="button button--secondary" @click="changeView('analysis')">刷新</button></div><table class="data-table"><thead><tr><th>文件</th><th>解析器</th><th>状态</th><th>进度</th><th>操作</th></tr></thead><tbody><tr v-for="item in state.jobs" :key="item.id"><td>{{ item.file_name }}</td><td>{{ item.parser || '—' }}</td><td>{{ statusText(item.status) }}</td><td>{{ item.progress }}%</td><td><button class="mini-action" :disabled="!['failed','needs_review','interrupted','published'].includes(item.status)" @click="retryJob(item)">{{ item.status === 'published' ? '重新解析' : '重试' }}</button></td></tr></tbody></table></div>
        <div v-else-if="state.view === 'logs'" class="manage-surface"><div class="manage-toolbar"><input v-model="logFilter.operator" placeholder="搜索操作人" @keyup.enter="loadLogs" /><select v-model="logFilter.action" @change="loadLogs"><option value="">全部操作</option><option>创建</option><option>修改</option><option>删除</option><option>移动</option><option>上传</option></select><button class="button button--secondary" @click="loadLogs">筛选</button><button class="button button--secondary" @click="clearLogs">清空日志</button></div><table class="data-table"><thead><tr><th>时间</th><th>操作人</th><th>操作</th><th>类型</th><th>对象</th></tr></thead><tbody><tr v-for="item in state.logs" :key="item.id"><td>{{ formatTime(item.created_at) }}</td><td>{{ item.operator }}</td><td>{{ item.action }}</td><td>{{ item.target_type }}</td><td>{{ item.target_name }}</td></tr></tbody></table></div>
        <div v-else-if="state.view === 'members'" class="manage-surface"><table class="data-table"><thead><tr><th>用户名</th><th>角色</th><th>加入时间</th><th>状态</th></tr></thead><tbody><tr v-for="item in state.members" :key="item.id"><td>{{ item.username }}{{ item.id === state.user.id ? '（我）' : '' }}</td><td><select :value="item.role" :disabled="item.id === state.user.id" @change="setRole(item,$event.target.value)"><option value="user">普通用户</option><option value="admin">管理员</option></select></td><td>{{ formatTime(item.created_at) }}</td><td>{{ item.disabled ? '已停用' : '正常' }}</td></tr></tbody></table></div>
        <div v-else class="settings-sheet"><div class="manage-toolbar"><h2>运行配置</h2><button class="button button--secondary" @click="openPassword">修改密码</button></div><dl><dt>意图识别</dt><dd>大模型结构化分类</dd><dt>文档解析</dt><dd>原生文本 / 表格；复杂图片等待复核</dd><dt>检索</dt><dd>BGE-M3 + Milvus BM25</dd><dt>会话</dt><dd>按用户隔离并持久化</dd></dl><div class="keyword-panel"><div class="manage-toolbar"><h2>知识关键词</h2><button class="button button--secondary" @click="loadKeywords">生成关键词云</button></div><div class="keyword-list"><span v-for="item in state.keywords" :key="item.word">{{ item.word }} · {{ item.weight }}</span></div></div></div>
      </section>
    </main>

    <button class="history-trigger" :aria-expanded="historyOpen" @click="historyOpen=!historyOpen"><UiIcon name="history" /><span>历史会话</span><b>{{ conversations.length }}</b></button>
    <div v-if="undoSession" class="undo-toast" role="status"><span>会话已移除</span><button @click="undoDelete">撤销</button></div>

    <dialog ref="uploadDialog" class="dialog"><form method="dialog" @submit.prevent="uploadFile"><header><div><span class="mono-label">DOCUMENT INGEST</span><h2>上传文件</h2></div><button type="button" class="icon-button" aria-label="关闭" @click="uploadDialog.close()"><UiIcon name="close" /></button></header><label>选择文件<input type="file" accept=".pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.csv,.txt,.md,.xmind,.jpg,.jpeg,.png" required @change="upload.file=$event.target.files[0]" /></label><label>所属板块<select v-model="upload.sectionId"><option v-for="section in state.sections" :key="section.id" :value="section.id">{{ section.name }}</option></select></label><label>标签<input v-model="upload.tags" placeholder="多个标签使用逗号分隔" /></label><p v-if="upload.error" class="form-error" role="alert">{{ upload.error }}</p><footer><button type="button" class="button button--secondary" @click="uploadDialog.close()">取消</button><button class="button button--primary" :disabled="upload.busy">{{ upload.busy ? '正在上传…' : '上传并入库' }}</button></footer></form></dialog>
    <dialog ref="previewDialog" class="dialog preview-dialog"><header><div><h2>{{ previewItem?.name }}</h2><small v-if="previewItem?.page_number">定位到第 {{ previewItem.page_number }} 页</small></div><button class="icon-button" aria-label="关闭预览" @click="previewDialog.close()"><UiIcon name="close" /></button></header><iframe v-if="previewItem" :src="previewFrameUrl(previewItem)" :title="`预览 ${previewItem.name}${previewItem.page_number ? `第 ${previewItem.page_number} 页` : ''}`"></iframe><footer><button class="button button--secondary" @click="download(previewItem)"><UiIcon name="download" />下载原件</button><button class="button button--primary" @click="previewDialog.close()">关闭预览</button></footer></dialog>
    <dialog ref="sectionDialog" class="dialog compact-dialog"><form method="dialog" @submit.prevent="saveSection"><header><div><span class="mono-label">SECTION</span><h2>{{ sectionForm.id ? '重命名板块' : '新建板块' }}</h2></div><button type="button" class="icon-button" aria-label="关闭" @click="sectionDialog.close()"><UiIcon name="close" /></button></header><label>板块名称<input v-model="sectionForm.name" maxlength="120" autofocus required /></label><p v-if="sectionForm.error" class="form-error">{{ sectionForm.error }}</p><footer><button type="button" class="button button--secondary" @click="sectionDialog.close()">取消</button><button class="button button--primary" :disabled="sectionForm.busy">{{ sectionForm.busy ? '保存中' : '保存' }}</button></footer></form></dialog>
    <dialog ref="documentDialog" class="dialog compact-dialog"><form method="dialog" @submit.prevent="saveDocumentName"><header><div><span class="mono-label">DOCUMENT</span><h2>重命名文件</h2></div><button type="button" class="icon-button" aria-label="关闭" @click="documentDialog.close()"><UiIcon name="close" /></button></header><label>文件名<input v-model="documentForm.name" maxlength="255" required /></label><small>需要保留原文件扩展名。</small><p v-if="documentForm.error" class="form-error">{{ documentForm.error }}</p><footer><button type="button" class="button button--secondary" @click="documentDialog.close()">取消</button><button class="button button--primary" :disabled="documentForm.busy">确认修改</button></footer></form></dialog>
    <dialog ref="moveDialog" class="dialog compact-dialog"><form method="dialog" @submit.prevent="confirmMove"><header><div><span class="mono-label">MOVE DOCUMENTS</span><h2>移动文件</h2></div><button type="button" class="icon-button" aria-label="关闭" @click="moveDialog.close()"><UiIcon name="close" /></button></header><label>目标板块<select v-model="moveForm.sectionId" required><option value="" disabled>请选择板块</option><option v-for="section in state.sections" :key="section.id" :value="section.id">{{ section.name }}</option></select></label><p>将移动 {{ moveForm.ids.length }} 个文件。</p><p v-if="moveForm.error" class="form-error">{{ moveForm.error }}</p><footer><button type="button" class="button button--secondary" @click="moveDialog.close()">取消</button><button class="button button--primary" :disabled="moveForm.busy">确认移动</button></footer></form></dialog>
    <dialog ref="confirmDialog" class="dialog compact-dialog"><form method="dialog" @submit.prevent="runConfirmedAction"><header><div><span class="mono-label">CONFIRM ACTION</span><h2>{{ confirmForm.title }}</h2></div><button type="button" class="icon-button" aria-label="关闭" @click="confirmDialog.close()"><UiIcon name="close" /></button></header><p>{{ confirmForm.message }}</p><p v-if="confirmForm.error" class="form-error">{{ confirmForm.error }}</p><footer><button type="button" class="button button--secondary" @click="confirmDialog.close()">取消</button><button class="button button--primary" :disabled="confirmForm.busy">{{ confirmForm.busy ? '处理中' : '确认' }}</button></footer></form></dialog>
    <dialog ref="versionsDialog" class="dialog"><header><div><span class="mono-label">VERSION HISTORY</span><h2>{{ versionDocument?.name }}</h2></div><button class="icon-button" aria-label="关闭" @click="versionsDialog.close()"><UiIcon name="close" /></button></header><div class="version-list"><article v-for="item in versions" :key="item.version_id"><div><strong>版本 {{ item.version_no }}</strong><small>{{ item.uploader }} · {{ formatTime(item.uploaded_at) }}</small></div><span>{{ statusText(item.ingestion_status) }}</span><button class="button button--secondary" :disabled="item.version_id === versionDocument?.version_id" @click="rollback(item)">回滚到此版本</button></article></div></dialog>
    <dialog ref="contentDialog" class="dialog editor-dialog"><form method="dialog" @submit.prevent="saveEditor"><header><div><span class="mono-label">CONTENT EDITOR</span><h2>{{ editor.id ? '编辑' : '新建' }}{{ editor.kind === 'scripts' ? '话术' : '规则' }}</h2></div><button type="button" class="icon-button" aria-label="关闭" @click="contentDialog.close()"><UiIcon name="close" /></button></header><label>标题<input v-model="editor.title" maxlength="255" required /></label><label>分组<input v-model="editor.group_name" maxlength="120" /></label><label>标签<input v-model="editor.tags" placeholder="多个标签使用逗号分隔" /></label><label>内容<textarea v-model="editor.content" rows="10" required></textarea></label><div v-if="editor.analysis" class="analysis-note">{{ editor.analysis }}</div><p v-if="editor.error" class="form-error">{{ editor.error }}</p><footer><button type="button" class="button button--secondary" :disabled="editor.busy || !editor.content" @click="analyzeEditor">AI 检查</button><button type="button" class="button button--secondary" @click="contentDialog.close()">取消</button><button class="button button--primary" :disabled="editor.busy">{{ editor.busy ? '保存中' : '保存内容' }}</button></footer></form></dialog>
    <dialog ref="passwordDialog" class="dialog"><form method="dialog" @submit.prevent="changePassword"><header><h2>修改密码</h2><button type="button" class="icon-button" aria-label="关闭" @click="passwordDialog.close()"><UiIcon name="close" /></button></header><label>原密码<input v-model="passwordForm.old" type="password" required /></label><label>新密码<input v-model="passwordForm.next" type="password" minlength="8" required /></label><label>确认新密码<input v-model="passwordForm.confirm" type="password" minlength="8" required /></label><p v-if="passwordForm.error" class="form-error">{{ passwordForm.error }}</p><footer><button type="button" class="button button--secondary" @click="passwordDialog.close()">取消</button><button class="button button--primary" :disabled="passwordForm.busy">确认修改</button></footer></form></dialog>
  </div>
</template>
