import { createApp } from 'vue'
import ReplicaHost from './replica/ReplicaHost.vue'
import './replica/host.css'

// 复刻版入口：挂载「教辅知识库前端开发」原页面的逐字复刻。
// 原工作台入口保留在 ./main-workspace.js，可随时切回。
createApp(ReplicaHost).mount('#app')
