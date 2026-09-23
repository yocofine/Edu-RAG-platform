<script setup>
import { nextTick, ref } from 'vue'
import UiIcon from './UiIcon.vue'

defineProps({ open: Boolean, sessions: Array, activeId: String, loading: Boolean })
const emit = defineEmits(['close', 'new', 'select', 'rename', 'delete'])
const editing = ref('')
const title = ref('')

function startRename(session) {
  editing.value = session.id
  title.value = session.title
  nextTick(() => document.querySelector('.history-rename')?.focus())
}
function commit(session) {
  if (title.value.trim() && title.value.trim() !== session.title) emit('rename', session.id, title.value.trim())
  editing.value = ''
}
function format(value) {
  return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(value))
}
</script>

<template>
  <Transition name="drawer">
    <section class="history-panel" :data-open="open" aria-label="历史会话">
      <header class="history-head">
        <div><span class="mono-label">YOUR THREADS</span><h2>历史会话</h2></div>
        <span class="count-readout">{{ sessions?.length || 0 }}</span>
        <button class="icon-button history-close" aria-label="关闭历史会话" @click="emit('close')"><UiIcon name="close" /></button>
      </header>
      <button class="button button--secondary history-new" @click="emit('new')"><UiIcon name="plus" />新建会话</button>
      <div v-if="loading" class="history-skeleton" aria-label="正在加载会话"><i v-for="n in 4" :key="n"></i></div>
      <div v-else-if="!sessions?.length" class="empty-state compact"><UiIcon name="history" :size="24" /><strong>还没有历史会话</strong><p>开始一次检索后，会话会保存在这里。</p></div>
      <ol v-else class="history-list">
        <li v-for="session in sessions" :key="session.id" :class="{ active: activeId === session.id }">
          <button class="history-select" @click="emit('select', session.id)">
            <span v-if="editing !== session.id" class="history-title">{{ session.title }}</span>
            <input v-else v-model="title" class="history-rename" maxlength="160" @click.stop @blur="commit(session)" @keyup.enter="commit(session)" @keyup.esc="editing=''" />
            <small>{{ format(session.updated_at) }} · {{ session.message_count }} 条消息</small>
          </button>
          <div class="history-actions">
            <button class="mini-action" aria-label="重命名会话" @click="startRename(session)">改名</button>
            <button class="mini-action danger" aria-label="删除会话" @click="emit('delete', session.id)">删除</button>
          </div>
        </li>
      </ol>
    </section>
  </Transition>
</template>
