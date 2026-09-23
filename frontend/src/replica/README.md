# 教辅知识库前端 —— 复刻版（Replica）

把 `教辅知识库前端开发/index.html` 这个原页面**完整复刻**进 `edu-rag-platform/frontend`。

复刻策略：**原页面的 CSS、DOM 结构、交互逻辑全部逐字保留**，只把「接口层」换成本项目
自己的后端（FastAPI `/api/v1`，Cookie 会话 + CSRF）。这样能做到视觉与交互 100% 一致，
同时数据来自 edu-rag-platform 自己的数据库。

---

## 文件构成

| 文件 | 行数 | 说明 |
|---|---|---|
| `src/replica/legacy.css` | 1390 | 原 `<style>` 块（源文件 8–1397 行），逐字复制 |
| `src/replica/_markup-reference.html` | 342 | 原 `<body>` 内全部结构（源文件 1401–1741 行），逐字复制 |
| `public/replica/legacy-app.js` | 1737 | 原内联 `<script>`（源文件 1745–3481 行），逐字复制 |
| `public/replica/legacy-api.js` | 573 | **新写的适配层**，把原接口名映射到本项目后端 |
| `src/replica/ReplicaHost.vue` | 66 | Vue 宿主：注入结构 → 依次加载两个经典脚本 |
| `src/replica/host.css` | — | 宿主布局样式（`display:contents` 让注入节点回到 body 直系） |

`legacy-app.js` 的行号与源文件保持固定偏移 **+1744**，方便逐行比对。

---

## 为什么能"逐字保留"

原页面大量使用内联事件（`onclick="switchView('files', this)"`、`onkeydown="..."`），
这些依赖**全局函数**。`legacy-app.js` 被当作**经典脚本**（非 ES 模块）动态插入，
它顶层的 `function` 声明会挂到 `window` 上，因此这些内联事件无需任何改写即可工作。

加载顺序（由 `ReplicaHost.vue` 控制，全部 await 串行）：

```
1. 注入 _markup-reference.html 到 #replica-root
2. 加载 legacy-api.js          → 定义 AuthAPI / FileAPI / GroupAPI 等全局对象
3. await ReplicaAuth.bootstrap() → 探测已有 Cookie 会话，补上 token 标记以便自动登录
4. 加载 legacy-app.js          → 末尾自行调用 init()，页面开始工作
```

> 第 3 步是必要的：原 `autoLogin()` 要求 `localStorage` 里存在 token 才尝试恢复会话，
> 而本项目是纯 Cookie 会话。

---

## 接口映射

| 原页面 | 本项目后端 | 转换要点 |
|---|---|---|
| `POST /auth/login` → `{token, user}` | `POST /auth/login` → `{user, csrf_token}` | 无 token，补占位值；凭证走 `access_token` Cookie |
| `GET /auth/me` | `GET /auth/me` | 形状一致 |
| `PUT /auth/password {oldPassword,newPassword}` | `PUT /auth/password {old_password,new_password}` | 字段改名；新密码需 ≥8 位 |
| `SectionAPI` `/sections` | `/sections` | 形状一致（`{sections:[]}` ← `{items:[]}`） |
| `FileAPI` `/files*` | `/documents*` | **文件名 `file_name`→`name`**、上传者 `uploader`、`size_bytes`→格式化的 `size` 与原始 `sizeBytes`、`preview_kind`→`previewKind`、`file_type`→图标类型 |
| `POST /files/upload` FormData `sectionId` | `POST /documents/upload` `section_id` | 字段改名；后端按文件名命名，**不支持自定义 title** |
| `GroupAPI` `/script-groups` | *（后端无此接口）* | 用 `group_name` 派生分组；建/改/删在适配层用内容 PUT/DELETE 实现 |
| `RuleAPI` `/rules/groups` | *（后端无此接口）* | 同上 |
| `ScriptAPI` `/scripts` | `/scripts` | 请求 `groupId`→`group_name`；响应 `group_id`←`group_name` |
| `RuleAPI` `/rules` | `/rules` | 同上（`image_path` 恒为空） |
| `POST /rules/upload-image` | *（后端无此接口）* | 图片读成 dataURL 仅存内存，见下方"限制" |
| `LogAPI` `/logs` | `/logs` | 形状一致，仅后端限管理员 |
| `MemberAPI` `PUT /members/:username/role` | `PUT /members/:id/role?role=` | 用户名→id，body→query |
| `AIAPI.quickSearch` `POST /ai/quick-search` | *（后端无此接口）* | 用已加载的 files/scripts 做本地关键词打分，保持"即时显示" |
| `AIAPI.search` `POST /ai/search` → `{reply}` | `POST /chat` → `{answer, citations}` | 取 `answer`，附加"参考来源" |
| `AIAPI.keywordCloud` `GET /ai/keyword-cloud` | 同路径 | `{cloud}` ← `{items:[{word,weight}]}` |
| `GET /api/files/{id}/view` | `GET /documents/{id}/preview` | 文件预览已改为直接请求后端（带 `version_id`）；MutationObserver 仍保留，用于话术/规则里的图片 |
| `Authorization: Bearer` | Cookie + `X-CSRF-Token` | 所有请求 `credentials: 'include'` |

---

## 对原脚本的**行为修正**

改动均为**局部替换**，除下列条目外 `legacy-app.js` 与原页面保持一致。

1. `renderLogs()`（第 1548 行）与 `renderMembers()`（第 1663 行）：
   原文 `if (LIST.length === 0) { loadX().then(() => renderX()); return; }`
   在数据为空时会**无限重复请求**（空列表 → 再取 → 仍为空 → 再取…）。
   改为 `if (LIST.length === 0 && !retried) { ... renderX(true); }`：
   一次重取后如果仍为空，就正常渲染空表格。除空列表场景外行为完全一致。

2. `previewFile()`：原文**只对图片**渲染 `<img>`，其它格式只显示一个大图标，
   且图片地址硬编码为 `/api/files/{id}/view`（本项目后端无此前缀，靠 MutationObserver 兜底）。
   现改为按后端 `preview_kind` 分流：
   - `pdf` / `text` → `<iframe class="preview-frame">` 直接内联预览（带 `version_id`）；
   - `image` → `<img>`；
   - `none` → 明确的"暂不支持在线预览，请下载查看"引导。
   同时"下载"按钮改为走带 `version_id` 的下载地址，保证下载的就是预览的那一版。

3. `updateStorage()`：原文 `parseFloat(f.size)` 解析展示字符串来算占用，
   遇到 `B` 单位会误按 MB 计算导致虚高。现改用后端返回的真实字节数（`sizeBytes`）。

4. `MOCK_FILES` 映射追加 `sizeBytes` / `previewKind` 两个字段（两处，`refreshFileAndJobData`
   与 `loadAllData`），供上面两条使用。

---

## 已知差异（受后端能力限制）

1. **规则截图不持久化**：后端无 `/rules/upload-image`，图片仅以 dataURL 存在内存中，
   刷新后消失（同会话内预览与 OCR 回填正常）。
2. **注册密码 ≥8 位**：原页面提示"至少4位"，后端强制 8–128 位；适配层提前给出中文提示。
3. **规则 `groupId` 无独立实体**：后端没有话术组/规则组表，分组就是 `group_name` 字符串。
   新建空分组登记在 localStorage；重命名/删除分组会逐个改写组内内容。
4. **AI 深度搜索**走 `POST /chat`（真正的 RAG 问答），比原 Express 后端更"重"，
   回答来自本项目知识库，措辞与原 mock 后端不同属预期。
5. `Tesseract.js` 仍从 CDN 加载（与原页面一致）；离线时静默失败，不影响其它功能。
6. **Office 在线预览依赖解析结果**：`docx/xlsx/ppt` 只有在入库时由 LibreOffice 转出
   PDF 预览件后才可内联预览；未转出时后端 `/preview` 会返回 409，前端提示下载查看。

---

## 运行

```powershell
# 1) 后端（端口 8000）
.venv\Scripts\uvicorn backend.app.main:app --reload --port 8000

# 2) 前端（端口 5173，vite.config.js 已把 /api 代理到 127.0.0.1:8000）
cd frontend
npm install
npm run dev
```

首次使用注册一个账号（**第一个注册用户自动成为管理员**），管理员才能看到
"操作日志 / 知识库成员"两个菜单。

---

## 回退到原 Vue 工作台

`src/main.js` 已改为挂载复刻版；原入口完整保留在 `src/main-workspace.js`。
把 `main.js` 换回：

```js
import { createApp } from 'vue'
import App from './Workspace.vue'
import '../tokens.css'
import './hallmark.css'
import './archive-desk.css'

createApp(App).mount('#app')
```

原 `Workspace.vue` / `App.vue` / `api.js` / `components/` **未被删除或修改**。

---

## 验证状态

最初复刻时在**无法运行构建的环境**下完成，已做的验证：

- 三份逐字复制的文件都做了**行数校验**与首尾行、抽样段落比对；
- 适配层的字段名全部对照后端源码逐一核对（`serialize()`、`models.py`、各 router）；
- 全局作用域问题已确认（`let MOCK_FILES` 不会被挂到 `window`，故适配层用裸标识符 + try/catch 访问）。

后续「真实文件大小 / 在线预览 / 入库实时进度」这次改动额外验证了：

- `node --check` 通过 `legacy-api.js` 与 `legacy-app.js`；
- `py_compile` 通过 `documents.py` / `ingestion.py` / `mineru.py` / `image_analysis.py` / `config.py`；
- `tests/test_document_size_and_preview.py`（12 项）全绿，覆盖 `preview_kind` 判定、
  `serialize()` 的 `size_bytes`、以及前端映射/样式源码断言；
- 端到端（TestClient + SQLite + 本地存储）实测：上传 `.txt` 后 `/documents/recent`
  返回 `size_bytes=75`、`preview_kind=text`，`/documents/{id}/preview` 返回 `200 text/markdown`；
- 进度逻辑单测：区间映射边界、MinerU 状态字段解析、回调异常容错。

**尚未验证**：真实浏览器中的渲染与交互，以及真实 Milvus + MinerU 链路下的进度推进，
需要用上面的命令跑一次（`npm run build` 需重新构建才会更新 `dist/`）。
