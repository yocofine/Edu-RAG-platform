<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { api, downloadUrl, previewUrl } from './api'

const nav = [
  ['chat', '💬', '智能搜索'], ['files', '📁', '全部文件'], ['rules', '📢', '规则通知'],
  ['scripts', '💬', '话术库'], ['analysis', '🤖', 'AI分析中心'], ['logs', '📋', '操作日志'],
  ['settings', '⚙️', '系统设置'], ['members', '👥', '知识库成员'],
]
const state = reactive({ user: null, view: 'chat', sections: [], documents: [], recent: [], scripts: [], rules: [], jobs: [], logs: [], members: [], stats: {} })
const auth = reactive({ mode: 'login', username: '', password: '', error: '', busy: false })
const query = ref('')
const busy = ref(false)
const answer = ref('')
const citations = ref([])
const fileResults = ref([])
const showResults = ref(false)
const showUpload = ref(false)
const showPreview = ref(false)
const previewItem = ref(null)
const upload = reactive({ file: null, sectionId: '', tags: '', busy: false, error: '' })
const editor = reactive({ show: false, kind: 'scripts', id: '', title: '', content: '', group_name: '默认分组', tags: '', busy: false, error: '' })
const review = reactive({ show: false, documentId: '', versionId: '', fileName: '', content: '', report: null, busy: false, error: '' })
let events

const pageTitle = computed(() => nav.find(item => item[0] === state.view)?.[2] || '教辅知识库')
const counts = computed(() => ({ files: state.stats.documents || 0, rules: state.stats.rules || 0, scripts: state.stats.scripts || 0 }))

function formatTime(value) {
  if (!value) return '-'
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value))
}

function statusText(status) {
  const map = { queued: '等待处理', detecting: '正在检测', extracting_text: '提取文本', ocr_processing: 'OCR识别', mineru_parsing: 'MinerU解析', indexing: '建立索引', published: '已发布', failed: '处理失败', needs_review: '等待复核', interrupted: '任务中断' }
  return map[status] || status
}

function plainText(html) {
  const element = document.createElement('div')
  element.innerHTML = html || ''
  return element.textContent || ''
}

async function initialize() {
  const [sections, recent, stats] = await Promise.all([api.sections(), api.recent(), api.stats()])
  state.sections = sections.items
  state.recent = recent.items
  state.stats = stats
  if (!upload.sectionId && state.sections.length) upload.sectionId = state.sections[0].id
  connectEvents()
}

async function authenticate() {
  auth.busy = true; auth.error = ''
  try {
    const result = auth.mode === 'login' ? await api.login(auth.username, auth.password) : await api.register(auth.username, auth.password)
    state.user = result.user
    await initialize()
  } catch (error) { auth.error = error.message } finally { auth.busy = false }
}

async function logout() {
  await api.logout(); state.user = null
  if (events) events.close()
}

function connectEvents() {
  if (events) events.close()
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  events = new WebSocket(`${protocol}//${location.host}/api/v1/events/ws`)
  events.onopen = () => events.send('ready')
  events.onmessage = async event => {
    const payload = JSON.parse(event.data)
    if (['document_uploaded', 'document_published', 'ingestion_status_changed'].includes(payload.type)) {
      state.recent = (await api.recent()).items
      state.stats = await api.stats()
    }
  }
}

async function changeView(view) {
  state.view = view
  try {
    if (view === 'files') state.documents = (await api.documents()).items
    if (view === 'scripts') state.scripts = (await api.scripts()).items
    if (view === 'rules') state.rules = (await api.rules()).items
    if (view === 'analysis') state.jobs = (await api.jobs()).items
    if (view === 'logs' && state.user.role === 'admin') state.logs = (await api.logs()).items
    if (view === 'members' && state.user.role === 'admin') state.members = (await api.members()).items
  } catch (error) { answer.value = error.message }
}

async function sendQuery() {
  if (!query.value.trim() || busy.value) return
  busy.value = true; answer.value = ''; citations.value = []; fileResults.value = []
  try {
    const result = await api.chat(query.value.trim())
    if (result.type === 'file_results') {
      fileResults.value = result.items
      showResults.value = true
    } else {
      answer.value = result.answer
      citations.value = result.citations || []
    }
  } catch (error) { answer.value = error.message } finally { busy.value = false }
}

async function uploadFile() {
  if (!upload.file || !upload.sectionId) return
  upload.busy = true; upload.error = ''
  const form = new FormData()
  form.append('file', upload.file); form.append('section_id', upload.sectionId); form.append('tags', upload.tags)
  try {
    await api.upload(form)
    showUpload.value = false; upload.file = null; upload.tags = ''
    state.recent = (await api.recent()).items
  } catch (error) { upload.error = error.message } finally { upload.busy = false }
}

function openPreview(item) { previewItem.value = item; showPreview.value = true }
function download(item) { window.open(downloadUrl(item.id, item.version_id), '_blank') }

function openEditor(kind, item = null) {
  Object.assign(editor, { show: true, kind, id: item?.id || '', title: item?.title || '', content: plainText(item?.content || ''), group_name: item?.group_name || (kind === 'rules' ? '通知规则' : '默认分组'), tags: item?.tags || '', busy: false, error: '' })
}

async function saveEditor() {
  editor.busy = true; editor.error = ''
  try {
    await api.saveContent(editor.kind, { id: editor.id || undefined, title: editor.title, content: editor.content, group_name: editor.group_name, tags: editor.tags })
    editor.show = false
    await changeView(editor.kind)
  } catch (error) { editor.error = error.message } finally { editor.busy = false }
}

async function deleteContent(kind, item) {
  if (!confirm(`确认删除“${item.title}”吗？`)) return
  await api.deleteContent(kind, item.id)
  await changeView(kind)
}

async function openReview(job) {
  Object.assign(review, { show: true, documentId: job.document_id, versionId: job.document_version_id, fileName: job.file_name, content: '', report: null, busy: true, error: '' })
  try {
    const [parsed, quality] = await Promise.all([api.parsed(job.document_id, job.document_version_id), api.quality(job.document_id, job.document_version_id)])
    review.content = parsed.content; review.report = quality
  } catch (error) { review.error = error.message } finally { review.busy = false }
}

async function saveReview(publish = false) {
  review.busy = true; review.error = ''
  try {
    await api.saveParsed(review.documentId, review.versionId, review.content)
    if (publish) await api.publishParsed(review.documentId, review.versionId)
    review.show = false; await changeView('analysis')
  } catch (error) { review.error = error.message } finally { review.busy = false }
}

async function retryJob(job) { await api.retryJob(job.id); await changeView('analysis') }

onMounted(async () => {
  try { state.user = (await api.me()).user; await initialize() } catch {}
})
onBeforeUnmount(() => events?.close())
</script>

<template>
  <div v-if="!state.user" class="auth-page">
    <form class="auth-card" @submit.prevent="authenticate">
      <div class="brand-logo">📚</div><h1>教辅知识库</h1><p>公司内部知识检索与协作平台</p>
      <label>用户名<input v-model="auth.username" autocomplete="username" required /></label>
      <label>密码<input v-model="auth.password" type="password" autocomplete="current-password" minlength="8" required /></label>
      <div v-if="auth.error" class="error">{{ auth.error }}</div>
      <button class="primary full" :disabled="auth.busy">{{ auth.busy ? '请稍候…' : auth.mode === 'login' ? '登录' : '注册' }}</button>
      <button type="button" class="link" @click="auth.mode = auth.mode === 'login' ? 'register' : 'login'">{{ auth.mode === 'login' ? '没有账号？立即注册' : '已有账号？返回登录' }}</button>
    </form>
  </div>

  <div v-else class="shell">
    <aside class="sidebar">
      <div class="brand"><span>📚</span><strong>教辅知识库</strong></div>
      <small>主菜单</small>
      <button v-for="item in nav.slice(0, 6)" :key="item[0]" :class="{ active: state.view === item[0] }" @click="changeView(item[0])">
        <span>{{ item[1] }}</span>{{ item[2] }}<b v-if="counts[item[0]]">{{ counts[item[0]] }}</b>
      </button>
      <small>设置</small>
      <button :class="{ active: state.view === 'settings' }" @click="changeView('settings')">⚙️ 系统设置</button>
      <small>管理</small>
      <button :class="{ active: state.view === 'members' }" @click="changeView('members')">👥 知识库成员</button>
      <div class="sidebar-footer"><span class="avatar">{{ state.user.username[0] }}</span><div><strong>{{ state.user.username }}</strong><small>{{ state.user.role === 'admin' ? '管理员' : '普通成员' }}</small></div><button class="logout" @click="logout">退出</button></div>
    </aside>

    <main>
      <header><h2>{{ pageTitle }}</h2><div><button class="outline" @click="showUpload = true">📤 上传文件</button><button class="text" @click="changeView(state.view)">🔄 刷新</button></div></header>

      <section v-if="state.view === 'chat'" class="chat-layout">
        <div class="chat-main">
          <div class="welcome" v-if="!answer"><div class="spark">✨</div><h1>AI 智能知识检索</h1><p>输入自然语言，大模型将为你查找文件或检索知识库</p><div class="suggestions"><button @click="query='帮我找一下最新上传的文件'">📄 最新文件</button><button @click="query='总结最近的规则通知'">📢 规则总结</button><button @click="query='知识库中有哪些资料'">📚 知识检索</button></div></div>
          <article v-else class="answer-card"><h3>智能回答</h3><p>{{ answer }}</p><div class="citations" v-if="citations.length"><b>参考来源</b><span v-for="source in citations" :key="JSON.stringify(source)">{{ source.file_name || source.source || '知识库资料' }}</span></div></article>
          <form class="search-box" @submit.prevent="sendQuery"><input v-model="query" placeholder="输入关键词或自然语言问题…" /><button :disabled="busy">{{ busy ? '…' : '➤' }}</button></form>
        </div>
        <aside class="recent-panel"><div class="panel-title"><h3>最近上传</h3><span>{{ state.recent.length }}</span></div><div v-if="!state.recent.length" class="empty">暂无上传记录</div><button v-for="item in state.recent" :key="item.version_id" class="recent-item" @click="openPreview(item)"><span class="file-icon">📄</span><div><strong :title="item.name">{{ item.name }}</strong><small>{{ formatTime(item.uploaded_at) }}</small><small>{{ item.uploader }} · {{ statusText(item.ingestion_status) }}</small></div></button></aside>
      </section>

      <section v-else class="content-page">
        <div v-if="state.view === 'files'" class="grid"><article v-for="item in state.documents" :key="item.id" class="card" @click="openPreview(item)"><span class="big-icon">📄</span><h3>{{ item.name }}</h3><p>{{ item.section_name }} · v{{ item.version_no }}</p><small>{{ item.uploader }} · {{ formatTime(item.uploaded_at) }}</small></article></div>
        <div v-else-if="state.view === 'scripts' || state.view === 'rules'"><div class="page-actions"><button class="primary" @click="openEditor(state.view)">＋ 新建{{ state.view === 'scripts' ? '话术' : '规则' }}</button></div><div class="grid"><article v-for="item in state.view === 'scripts' ? state.scripts : state.rules" :key="item.id" class="card"><h3>{{ item.title }}</h3><p>{{ plainText(item.content) }}</p><small>{{ item.owner }} · {{ formatTime(item.updated_at) }}</small><div class="card-actions"><button @click="openEditor(state.view,item)">编辑</button><button @click="deleteContent(state.view,item)">删除</button></div></article></div></div>
        <table v-else-if="state.view === 'analysis'"><thead><tr><th>文件</th><th>解析器</th><th>状态</th><th>进度</th><th>操作</th></tr></thead><tbody><tr v-for="item in state.jobs" :key="item.id"><td>{{ item.file_name }}</td><td>{{ item.parser || '-' }}</td><td>{{ statusText(item.status) }}</td><td>{{ item.progress }}%</td><td><button v-if="item.status==='needs_review'" @click="openReview(item)">复核</button><button v-if="['failed','interrupted','published'].includes(item.status)" @click="retryJob(item)">{{ item.status === 'published' ? '重新解析' : '重试' }}</button><span v-if="item.error_message" :title="item.error_message"> ⚠️</span></td></tr></tbody></table>
        <table v-else-if="state.view === 'logs'"><thead><tr><th>时间</th><th>操作人</th><th>操作</th><th>对象</th></tr></thead><tbody><tr v-for="item in state.logs" :key="item.id"><td>{{ formatTime(item.created_at) }}</td><td>{{ item.operator }}</td><td>{{ item.action }}</td><td>{{ item.target_name }}</td></tr></tbody></table>
        <table v-else-if="state.view === 'members'"><thead><tr><th>用户名</th><th>角色</th><th>加入时间</th><th>状态</th></tr></thead><tbody><tr v-for="item in state.members" :key="item.id"><td>{{ item.username }}</td><td>{{ item.role }}</td><td>{{ formatTime(item.created_at) }}</td><td>{{ item.disabled ? '已停用' : '正常' }}</td></tr></tbody></table>
        <div v-else-if="state.view === 'settings'" class="settings-card"><h3>系统配置</h3><p>模型、解析阈值和生产密钥由服务器环境变量管理，页面仅展示配置状态，绝不返回密钥明文。</p><dl><dt>意图识别</dt><dd>大模型结构化分类</dd><dt>文档解析</dt><dd>原生提取 / OCR / MinerU智能分流</dd><dt>向量检索</dt><dd>BGE-M3 + Milvus BM25</dd></dl></div>
      </section>
    </main>

    <div v-if="showUpload" class="modal-mask" @click.self="showUpload=false"><form class="modal" @submit.prevent="uploadFile"><h2>上传文件</h2><label>选择文件<input type="file" accept=".pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.csv,.txt,.md,.jpg,.jpeg,.png" @change="upload.file=$event.target.files[0]" required /></label><label>所属板块<select v-model="upload.sectionId"><option v-for="section in state.sections" :value="section.id">{{ section.name }}</option></select></label><label>标签<input v-model="upload.tags" placeholder="多个标签用逗号分隔" /></label><div v-if="upload.error" class="error">{{ upload.error }}</div><footer><button type="button" @click="showUpload=false">取消</button><button class="primary" :disabled="upload.busy">{{ upload.busy ? '正在上传…' : '开始上传' }}</button></footer></form></div>

    <div v-if="showResults" class="modal-mask" @click.self="showResults=false"><div class="modal result-modal"><h2>找到 {{ fileResults.length }} 个文件</h2><div v-if="!fileResults.length" class="empty">没有找到符合条件的文件</div><article v-for="item in fileResults" :key="item.version_id" class="result-row"><span>📄</span><div><strong>{{ item.name }}</strong><small>{{ item.section_name }} · {{ item.uploader }} · {{ formatTime(item.uploaded_at) }}</small></div><button @click="openPreview(item)">预览</button><button @click="download(item)">下载</button></article><footer><button @click="showResults=false">关闭</button></footer></div></div>

    <div v-if="showPreview && previewItem" class="modal-mask" @click.self="showPreview=false"><div class="modal preview-modal"><h2>{{ previewItem.name }}</h2><iframe :src="previewUrl(previewItem.id, previewItem.version_id)"></iframe><footer><button @click="download(previewItem)">下载原件</button><button @click="showPreview=false">关闭</button></footer></div></div>

    <div v-if="editor.show" class="modal-mask" @click.self="editor.show=false"><form class="modal" @submit.prevent="saveEditor"><h2>{{ editor.id ? '编辑' : '新建' }}{{ editor.kind==='scripts' ? '话术' : '规则' }}</h2><label>标题<input v-model="editor.title" required /></label><label>分组<input v-model="editor.group_name" required /></label><label>标签<input v-model="editor.tags" /></label><label>正文<textarea v-model="editor.content" rows="10" required></textarea></label><div v-if="editor.error" class="error">{{ editor.error }}</div><footer><button type="button" @click="editor.show=false">取消</button><button class="primary" :disabled="editor.busy">保存</button></footer></form></div>

    <div v-if="review.show" class="modal-mask" @click.self="review.show=false"><div class="modal review-modal"><h2>解析复核：{{ review.fileName }}</h2><p v-if="review.report">状态：{{ statusText(review.report.status) }}</p><textarea v-model="review.content" rows="24"></textarea><div v-if="review.error" class="error">{{ review.error }}</div><footer><button @click="review.show=false">取消</button><button @click="saveReview(false)" :disabled="review.busy">保存</button><button class="primary" @click="saveReview(true)" :disabled="review.busy">保存并发布</button></footer></div></div>
  </div>
</template>
