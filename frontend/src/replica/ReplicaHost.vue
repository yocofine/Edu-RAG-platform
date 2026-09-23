<script setup>
/**
 * 教辅知识库前端复刻版 —— Vue 宿主
 *
 * 复刻策略：原页面（教辅知识库前端开发/index.html）的样式、结构与交互逻辑
 * 逐字保留，只把「接口层」替换为本项目自己的后端 API。
 *
 *   src/replica/legacy.css                原 <style> 块，逐字复制
 *   src/replica/_markup-reference.html    原 <body> 结构，逐字复制（341 行）
 *   public/replica/legacy-api.js          适配层：把原接口名映射到本项目 /api/v1
 *   public/replica/legacy-app.js          原内联 <script>，逐字复制（1737 行）
 *
 * legacy-app.js 以「经典脚本」方式加载，不是 ES 模块，因此它的顶层函数声明
 * 会成为全局函数；原结构里 onclick="switchView('files', this)" 之类的内联事件
 * 无需任何改写即可工作。
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'
import markup from './_markup-reference.html?raw'
import './legacy.css'
import './appkit.css'

const host = ref(null)
const failure = ref('')
let disposed = false
let removeResponsiveHandlers = () => {}

function setMobileNavigation(open) {
  const sidebar = host.value?.querySelector('#appSidebar')
  const trigger = host.value?.querySelector('#mobileNavToggle')
  const scrim = host.value?.querySelector('#mobileNavScrim')
  sidebar?.classList.toggle('mobile-open', open)
  scrim?.classList.toggle('visible', open)
  trigger?.setAttribute('aria-expanded', String(open))
}

function installResponsiveNavigation() {
  const nav = host.value?.querySelector('#sidebarNav')
  const handleNavClick = () => {
    if (window.matchMedia('(max-width: 768px)').matches) setMobileNavigation(false)
  }
  const handleKeydown = (event) => {
    if (event.key === 'Escape') setMobileNavigation(false)
  }
  window.toggleMobileNavigation = () => {
    const open = !host.value?.querySelector('#appSidebar')?.classList.contains('mobile-open')
    setMobileNavigation(open)
  }
  nav?.addEventListener('click', handleNavClick)
  window.addEventListener('keydown', handleKeydown)
  return () => {
    nav?.removeEventListener('click', handleNavClick)
    window.removeEventListener('keydown', handleKeydown)
    delete window.toggleMobileNavigation
  }
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const element = document.createElement('script')
    element.src = src
    element.async = false
    element.dataset.replica = 'true'
    element.addEventListener('load', () => resolve(), { once: true })
    element.addEventListener('error', () => reject(new Error(`无法加载 ${src}`)), { once: true })
    document.body.appendChild(element)
  })
}

onMounted(async () => {
  try {
    host.value.innerHTML = markup
    removeResponsiveHandlers = installResponsiveNavigation()

    await loadScript('/replica/legacy-api.js?v=20260918-citations-metrics') // 定义 AuthAPI / FileAPI 等全局对象
    if (disposed) return
    if (window.ReplicaAuth?.bootstrap) await window.ReplicaAuth.bootstrap()  // 让已有的 Cookie 会话可以自动登录
    if (disposed) return
    await loadScript('/replica/legacy-app.js?v=20260918-citations-metrics') // 原页面逻辑，末尾会自行调用 init()
  } catch (error) {
    failure.value = error?.message || String(error)
  }
})

onBeforeUnmount(() => {
  disposed = true
  removeResponsiveHandlers()
})
</script>

<template>
  <div id="replica-root" ref="host">
    <div v-if="failure" class="replica-failure" role="alert">
      <h2>界面资源加载失败</h2>
      <p>{{ failure }}</p>
      <p class="replica-failure-hint">
        请确认 <code>frontend/public/replica/legacy-api.js</code> 与
        <code>legacy-app.js</code> 存在且可访问。
      </p>
    </div>
  </div>
</template>
