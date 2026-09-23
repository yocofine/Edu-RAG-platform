const API_BASE = import.meta.env.VITE_API_BASE || '/api/v1'

function cookie(name) {
  const item = document.cookie.split('; ').find(row => row.startsWith(`${name}=`))
  return item ? decodeURIComponent(item.split('=').slice(1).join('=')) : ''
}

export async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) }
  if (!(options.body instanceof FormData)) headers['Content-Type'] = 'application/json'
  if (!['GET', 'HEAD'].includes((options.method || 'GET').toUpperCase())) {
    headers['X-CSRF-Token'] = cookie('csrf_token')
  }
  const response = await fetch(API_BASE + path, { ...options, headers, credentials: 'include' })
  const contentType = response.headers.get('content-type') || ''
  const data = contentType.includes('json') ? await response.json() : await response.blob()
  if (!response.ok) throw new Error(data.detail || data.message || '请求失败')
  return data
}

export async function streamRequest(path, body, onEvent) {
  const response = await fetch(API_BASE + path, {
    method: 'POST', credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': cookie('csrf_token') },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(data.detail || data.message || '请求失败')
  }
  if (!response.body) throw new Error('当前浏览器不支持流式响应')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) if (line.trim()) onEvent(JSON.parse(line))
    if (done) break
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer))
}

export const api = {
  me: () => request('/auth/me'),
  login: (username, password) => request('/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) }),
  register: (username, password) => request('/auth/register', { method: 'POST', body: JSON.stringify({ username, password }) }),
  logout: () => request('/auth/logout', { method: 'POST' }),
  changePassword: (oldPassword, newPassword) => request('/auth/password', { method: 'PUT', body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }) }),
  sections: () => request('/sections'),
  createSection: name => request('/sections', { method: 'POST', body: JSON.stringify({ name }) }),
  renameSection: (id, name) => request(`/sections/${id}`, { method: 'PUT', body: JSON.stringify({ name }) }),
  deleteSection: id => request(`/sections/${id}`, { method: 'DELETE' }),
  reorderSections: order => request('/sections/order/reorder', { method: 'PUT', body: JSON.stringify({ order }) }),
  documents: sectionId => request('/documents' + (sectionId ? `?section_id=${encodeURIComponent(sectionId)}` : '')),
  recent: () => request('/documents/recent?limit=10'),
  upload: form => request('/documents/upload', { method: 'POST', body: form }),
  updateDocument: (id, data) => request(`/documents/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteDocument: id => request(`/documents/${id}`, { method: 'DELETE' }),
  batchDeleteDocuments: ids => request('/documents/batch-delete', { method: 'POST', body: JSON.stringify({ ids }) }),
  batchMoveDocuments: (ids, sectionId) => request('/documents/batch-move', { method: 'POST', body: JSON.stringify({ ids, section_id: sectionId }) }),
  versions: id => request(`/documents/${id}/versions`),
  rollbackVersion: (id, versionId) => request(`/documents/${id}/versions/${versionId}/rollback`, { method: 'POST' }),
  chat: (query, sessionId) => request('/chat', { method: 'POST', body: JSON.stringify({ query, session_id: sessionId }) }),
  chatStream: (query, sessionId, onEvent) => streamRequest('/chat/stream', { query, session_id: sessionId }, onEvent),
  scripts: () => request('/scripts'),
  rules: () => request('/rules'),
  logs: (operator = '', action = '') => request(`/logs?operator=${encodeURIComponent(operator)}&action=${encodeURIComponent(action)}`),
  clearLogs: () => request('/logs', { method: 'DELETE' }),
  members: () => request('/members'),
  setMemberRole: (id, role) => request(`/members/${id}/role?role=${encodeURIComponent(role)}`, { method: 'PUT' }),
  jobs: () => request('/ingestion-jobs'),
  stats: () => request('/stats'),
  keywordCloud: () => request('/ai/keyword-cloud'),
  analyzeContent: content => request('/ai/analyze', { method: 'POST', body: JSON.stringify({ content }) }),
  saveContent: (kind, item) => request(`/${kind}${item.id ? `/${item.id}` : ''}`, { method: item.id ? 'PUT' : 'POST', body: JSON.stringify(item) }),
  deleteContent: (kind, id) => request(`/${kind}/${id}`, { method: 'DELETE' }),
  retryJob: id => request(`/ingestion-jobs/${id}/retry`, { method: 'POST' }),
  quality: (documentId, versionId) => request(`/documents/${documentId}/quality-report?version_id=${encodeURIComponent(versionId)}`),
  parsed: (documentId, versionId) => request(`/documents/${documentId}/parsed-content?version_id=${encodeURIComponent(versionId)}`),
  saveParsed: (documentId, versionId, content) => request(`/documents/${documentId}/parsed-content?version_id=${encodeURIComponent(versionId)}`, { method: 'PUT', body: JSON.stringify({ content }) }),
  publishParsed: (documentId, versionId) => request(`/documents/${documentId}/publish?version_id=${encodeURIComponent(versionId)}`, { method: 'POST' }),
  conversations: () => request('/conversations'),
  createConversation: (title = '新会话') => request('/conversations', { method: 'POST', body: JSON.stringify({ title }) }),
  conversationMessages: id => request(`/conversations/${id}/messages`),
  renameConversation: (id, title) => request(`/conversations/${id}`, { method: 'PUT', body: JSON.stringify({ title }) }),
  deleteConversation: id => request(`/conversations/${id}`, { method: 'DELETE' }),
  restoreConversation: id => request(`/conversations/${id}/restore`, { method: 'POST' }),
}

export function previewUrl(documentId, versionId) {
  return `${API_BASE}/documents/${documentId}/preview?version_id=${encodeURIComponent(versionId)}`
}

export function downloadUrl(documentId, versionId) {
  return `${API_BASE}/documents/${documentId}/download?version_id=${encodeURIComponent(versionId)}`
}
