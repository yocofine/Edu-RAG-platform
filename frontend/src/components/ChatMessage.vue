<script setup>
import UiIcon from './UiIcon.vue'
defineProps({ message: Object })
const emit = defineEmits(['preview', 'download'])
function format(value) {
  return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(new Date(value || Date.now()))
}
function citationName(item) {
  return item.file_name || item.metadata?.file_name || item.metadata?.source || item.citation || '知识库资料'
}
function citationPage(item) {
  const page = Number(item.page_number || item.metadata?.page_number || 0)
  return Number.isFinite(page) && page > 0 ? `第 ${page} 页` : '页码未知'
}
function canPreview(item) {
  return Boolean(item.document_id || item.metadata?.document_id)
}
function durationLabel(seconds) {
  const value = Number(seconds)
  if (!Number.isFinite(value)) return ''
  return value < 1 ? `${Math.round(value * 1000)} 毫秒` : `${value.toFixed(2)} 秒`
}
function tokenLabel(usage) {
  if (!usage) return ''
  const total = Number(usage.total_tokens || 0)
  return `${usage.estimated ? '约 ' : ''}${total.toLocaleString('zh-CN')} tokens`
}
</script>

<template>
  <article class="message" :class="`message--${message.role}`" :data-state="message.state || 'default'">
    <div class="message-author"><span><UiIcon :name="message.role === 'user' ? 'user' : 'assistant'" /></span><small>{{ message.role === 'user' ? '你' : '知识助手' }}</small></div>
    <div class="message-body">
      <div v-if="message.state === 'loading'" class="thinking"><i></i><i></i><i></i><span>{{ message.status || '正在理解你的问题' }}</span></div>
      <p v-else>{{ message.content }}</p>
      <div v-if="message.files?.length" class="message-files">
        <article v-for="file in message.files" :key="file.version_id" class="file-result">
          <UiIcon name="files" :size="20" />
          <div><strong>{{ file.name }}</strong><small>{{ file.section_name }} · {{ file.uploader }}</small></div>
          <button class="icon-button" aria-label="预览文件" @click="emit('preview', file)"><UiIcon name="eye" /></button>
          <button class="icon-button" aria-label="下载文件" @click="emit('download', file)"><UiIcon name="download" /></button>
        </article>
      </div>
      <div v-if="message.citations?.length" class="citation-list" aria-label="参考来源">
        <button
          v-for="(item,index) in message.citations"
          :key="`${item.version_id || index}-${item.page_number || 0}`"
          type="button"
          :disabled="!canPreview(item)"
          :aria-label="canPreview(item) ? `预览 ${citationName(item)} ${citationPage(item)}` : undefined"
          @click="canPreview(item) && emit('preview', item)"
        >
          <UiIcon name="files" :size="14" aria-hidden="true" />
          <span>{{ citationName(item) }}</span>
          <small>{{ citationPage(item) }}</small>
        </button>
      </div>
      <div class="message-meta">
        <small v-if="message.role === 'assistant' && message.metrics" class="response-metrics">
          耗时 {{ durationLabel(message.metrics.processing_time) }} · {{ tokenLabel(message.metrics.token_usage) }}
        </small>
        <time>{{ format(message.created_at) }}</time>
      </div>
    </div>
  </article>
</template>
