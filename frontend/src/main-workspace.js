// 原 Vue 工作台入口（Cinnabar Archive 设计系统）。
// 复刻版上线后 main.js 已改为挂载 src/replica/ReplicaHost.vue；
// 若需切回原来的界面，把本文件的 import 路径复制回 main.js 即可。
import { createApp } from 'vue'
import App from './Workspace.vue'
import '../tokens.css'
import './hallmark.css'
import './archive-desk.css'

createApp(App).mount('#app')
