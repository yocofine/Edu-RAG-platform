        // ==================== 全局状态 ====================
        const APP_STATE = {
            isLoggedIn: false,
            userName: '',
            userRole: 'user',
            currentView: 'chat',
            currentSectionId: null,
            currentScriptGroupId: null,
            currentRuleGroupId: null,
            ctxTargetId: null,
            ctxTargetType: null,
            renameTargetId: null,
            renameTargetType: null,
            moveTargetIds: [],
            pendingUploadFiles: [],
            selectedItems: new Set(),
            logPage: 0,
            logPageSize: 20,
            sectionOrder: [],
            scriptGroupOrder: [],
            ruleGroupOrder: [],
            draggedId: null,
            draggedType: null,
        };

        const LS_KEYS = { AUTH: 'kb_auth2', SECTION_ORDER: 'kb_sec_order2', SCRIPT_GROUP_ORDER: 'kb_sg_order2', RULE_GROUP_ORDER: 'kb_rg_order2', USERS: 'kb_users2' };

        function saveAuth(u, r) { /* 改用 JWT token，由 api.js 中的 setToken 管理 */ return; }

        function loadAuth() { /* 改用 JWT token，由 api.js 中的 getToken 管理 */ return null; }

        function clearAuth() { /* 改用 JWT token，由 api.js 中的 clearToken 管理 */ return; }

        function saveSectionOrder(o) { localStorage.setItem(LS_KEYS.SECTION_ORDER, JSON.stringify(o)); }

        function loadSectionOrder() { try { const r = localStorage.getItem(LS_KEYS.SECTION_ORDER); return r ? JSON.parse(
            r) : null; } catch { return null; } }

        function saveScriptGroupOrder(o) { localStorage.setItem(LS_KEYS.SCRIPT_GROUP_ORDER, JSON.stringify(o)); }

        function loadScriptGroupOrder() { try { const r = localStorage.getItem(LS_KEYS.SCRIPT_GROUP_ORDER); return r ? JSON
                .parse(r) : null; } catch { return null; } }

        function saveRuleGroupOrder(o) { localStorage.setItem(LS_KEYS.RULE_GROUP_ORDER, JSON.stringify(o)); }

        function loadRuleGroupOrder() { try { const r = localStorage.getItem(LS_KEYS.RULE_GROUP_ORDER); return r ? JSON.parse(
            r) : null; } catch { return null; } }

        function isAdmin() { return APP_STATE.userRole === 'admin'; }

        function canEdit(owner) { return isAdmin() || owner === APP_STATE.userName; }

        function canDelete(owner) { return isAdmin() || owner === APP_STATE.userName; }

        function debounce(fn, wait = 200) { let t = null; return function (...a) { clearTimeout(t); t = setTimeout(() => fn.apply(this, a), wait); }; }
        const debouncedRenderFiles = debounce(() => renderFilesRight());
        const debouncedRenderScripts = debounce(() => renderScriptsRight());
        const debouncedRenderRules = debounce(() => renderRulesRight());
        const debouncedRenderLogs = debounce(() => renderLogs());
        const debouncedRenderMembers = debounce(() => renderMembers());
        const debouncedRenderFaqs = debounce(() => renderFaqs());

        // ==================== 数据层（从后端 API 加载） ====================
        let MOCK_USERS = [];
        let MOCK_SECTIONS = [];
        let MOCK_FILES = [];
        let MOCK_INGESTION_JOBS = [];
        let MOCK_SCRIPT_GROUPS = [];
        let MOCK_SCRIPTS = [];
        let MOCK_RULE_GROUPS = [];
        let MOCK_RULES = [];
        let MOCK_LOGS = [];
        let MOCK_FAQS = [];
        let ingestionSocket = null;
        let ingestionReconnectTimer = null;
        let pendingQuickQuestion = '';

        const INGESTION_STATUS_LABELS = {
            queued: '等待处理', detecting: '检测文件类型', extracting_text: '提取文本',
            ocr_processing: 'OCR 识别', mineru_parsing: '解析复杂版面', table_normalizing: '整理表格',
            image_analyzing: '识别图片内容', quality_checking: '检查解析质量',
            converting_preview: '生成预览', chunking: '切分知识块', indexing: '生成向量并写入索引',
            published: '入库完成', needs_review: '等待人工复核', failed: '处理失败', interrupted: '任务中断'
        };

        function ingestionStatusLabel(status) {
            return INGESTION_STATUS_LABELS[status] || status || '处理中';
        }

        function formatFileSize(bytes) {
            const value = Number(bytes || 0);
            if (!Number.isFinite(value) || value <= 0) return '大小未知';
            if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
            if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
            return `${(value / (1024 * 1024 * 1024)).toFixed(2)} GB`;
        }

        function isIndeterminateJob(job) {
            return ['mineru_parsing', 'ocr_processing'].includes(job.status) && Number(job.progress || 0) <= 25;
        }

        function latestIngestionJobs() {
            const seen = new Set();
            return MOCK_INGESTION_JOBS.filter(job => {
                const key = job.document_id || job.document_version_id;
                if (!key || seen.has(key)) return false;
                seen.add(key);
                return true;
            });
        }

        function activeJobForDocument(documentId) {
            return latestIngestionJobs().find(job =>
                job.document_id === documentId && !['published'].includes(job.status)
            );
        }

        function pendingOnlyJobs() {
            return latestIngestionJobs().filter(job =>
                job.status !== 'published' && !MOCK_FILES.some(file => file.id === job.document_id)
            );
        }

        async function refreshFileAndJobData() {
            const [fileRes, jobRes] = await Promise.all([FileAPI.list(), JobAPI.list()]);
            MOCK_FILES = fileRes.files.map(f => ({
                id: f.id, name: f.name, type: f.type, size: f.size, date: (f.created_at || '').split(' ')[0],
                sizeBytes: f.sizeBytes || f.size_bytes || 0, previewKind: f.previewKind || f.preview_kind || '',
                versionId: f.versionId || f.version_id,
                keywords: (f.tags || '').split(',').filter(Boolean), aiSummary: '', iconType: f.type,
                sectionId: f.sectionId || f.section_id, owner: f.owner || f.uploader,
            }));
            MOCK_INGESTION_JOBS = jobRes.jobs || [];
            renderFilesLeftNav();
            if (APP_STATE.currentView === 'files') renderFilesRight();
            if (APP_STATE.currentView === 'ai-analysis') renderIngestionJobs();
            updateAllCounts();
            updateStorage();
        }

        function connectIngestionEvents() {
            if (!APP_STATE.isLoggedIn || (ingestionSocket && ingestionSocket.readyState <= WebSocket.OPEN)) return;
            clearTimeout(ingestionReconnectTimer);
            const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
            ingestionSocket = new WebSocket(`${protocol}//${location.host}/api/v1/events/ws`);
            ingestionSocket.onopen = () => ingestionSocket.send('ready');
            ingestionSocket.onmessage = async event => {
                try {
                    const payload = JSON.parse(event.data);
                    if (['document_uploaded', 'ingestion_status_changed', 'document_published'].includes(payload.type)) {
                        await refreshFileAndJobData();
                    }
                } catch (error) {
                    console.error('刷新入库进度失败:', error);
                }
            };
            ingestionSocket.onclose = () => {
                ingestionSocket = null;
                if (APP_STATE.isLoggedIn) ingestionReconnectTimer = setTimeout(connectIngestionEvents, 2500);
            };
        }

        function getUserByName(u) { return MOCK_USERS.find(x => x.username === u); }

        // 从后端加载所有数据
        async function loadAllData() {
            /* 每个接口单独兜底：某个接口失败（例如权限不足 403）时，其余数据照常渲染。
             * 之前这里是一个 Promise.all，任何一个请求失败都会让整页数据为空——
             * 表现为「话术库显示暂无话术」+ 一条红色错误提示。 */
            const loadFailures = [];
            function safeLoad(label, promise, fallback) {
                return promise.catch(function (error) {
                    const reason = (error && error.message) ? error.message : String(error);
                    loadFailures.push(label + '（' + reason + '）');
                    console.warn('加载失败：' + label, error);
                    return fallback;
                });
            }
            try {
                const [secRes, fileRes, grpRes, scrRes, rgrpRes, rulRes, jobRes, faqRes] = await Promise.all([
                    safeLoad('板块', SectionAPI.list(), { sections: [] }),
                    safeLoad('文件列表', FileAPI.list(), { files: [] }),
                    safeLoad('话术组', GroupAPI.list(), { groups: [] }),
                    safeLoad('话术库', ScriptAPI.list(), { scripts: [] }),
                    safeLoad('规则分组', RuleAPI.listGroups(), { groups: [] }),
                    safeLoad('规则通知', RuleAPI.list(), { rules: [] }),
                    safeLoad('入库任务', JobAPI.list(), { jobs: [] }),
                    isAdmin() ? safeLoad('FAQ库', FAQAPI.list(), { faqs: [] }) : Promise.resolve({ faqs: [] }),
                ]);
                MOCK_SECTIONS = secRes.sections.map(s => ({ id: s.id, name: s.name, icon: '📁', owner: 'admin', sort_order: s.sort_order }));
                MOCK_FILES = fileRes.files.map(f => ({
                    id: f.id, name: f.name, type: f.type, size: f.size, date: (f.created_at || '').split(' ')[0],
                    sizeBytes: f.sizeBytes || f.size_bytes || 0, previewKind: f.previewKind || f.preview_kind || '',
                    versionId: f.versionId || f.version_id,
                    keywords: (f.tags || '').split(',').filter(Boolean), aiSummary: '', iconType: f.type,
                    sectionId: f.sectionId || f.section_id, owner: f.owner || f.uploader,
                }));
                MOCK_INGESTION_JOBS = jobRes.jobs || [];
                MOCK_FAQS = faqRes.faqs || [];
                MOCK_SCRIPT_GROUPS = grpRes.groups.map(g => ({ id: g.id, name: g.name, icon: '💬', owner: g.owner, sort_order: g.sort_order }));
                MOCK_SCRIPTS = scrRes.scripts.map(s => ({
                    id: s.id, groupId: s.group_id, title: s.title, content: s.content || '',
                    tags: (s.tags || '').split(',').filter(Boolean), keywords: [], summary: '', updatedAt: (s.created_at || '').split(' ')[0], owner: s.owner,
                }));
                MOCK_RULE_GROUPS = rgrpRes.groups.map(g => ({ id: g.id, name: g.name, icon: '📢', owner: g.owner, sort_order: g.sort_order }));
                MOCK_RULES = rulRes.rules.map(r => ({
                    id: r.id, groupId: r.group_id, title: r.title, content: r.content || '',
                    tags: (r.tags || '').split(',').filter(Boolean), keywords: [], summary: '', updatedAt: (r.created_at || '').split(' ')[0], owner: r.owner,
                    imagePath: r.image_path || '',
                }));
                if (isAdmin()) {
                    const memRes = await safeLoad('成员列表', MemberAPI.list(), { users: [] });
                    MOCK_USERS = memRes.users.map(u => ({ username: u.username, password: '', role: u.role, createdAt: u.created_at }));
                }
                updateAllCounts();
                updateStorage();
                connectIngestionEvents();
                if (loadFailures.length) showToast('部分数据加载失败：' + loadFailures.join('；'), 'error');
            } catch (e) { console.error('加载数据失败:', e); showToast('数据加载失败: ' + e.message, 'error'); }
        }

        async function loadLogs() {
            try {
                const res = await LogAPI.list(0, document.getElementById('logOperatorSearch')?.value || '');
                MOCK_LOGS = res.logs.map(l => ({
                    id: l.id, timestamp: l.created_at, operator: l.operator, actionType: l.action,
                    targetType: l.target_type, targetName: l.target_name, targetPath: l.detail || '-',
                }));
            } catch (e) { console.error('加载日志失败:', e); }
        }

        async function loadMembers() {
            try {
                const res = await MemberAPI.list();
                MOCK_USERS = res.users.map(u => ({ username: u.username, password: '', role: u.role, createdAt: u.created_at }));
            } catch (e) { console.error('加载成员失败:', e); }
        }

        function getSectionById(id) { return MOCK_SECTIONS.find(s => s.id === id); }

        function getSectionName(id) { const s = getSectionById(id); return s ? s.name : '未知板块'; }

        function getSortedSections() {
            const order = APP_STATE.sectionOrder;
            const map = {};
            MOCK_SECTIONS.forEach(s => map[s.id] = s);
            const result = [];
            if (order.length > 0) { order.forEach(id => { if (map[id]) result.push(map[id]); });
                MOCK_SECTIONS.forEach(s => { if (!order.includes(s.id)) result.push(s); }); } else { result.push(
                ...MOCK_SECTIONS); }
            return result;
        }

        function getSortedScriptGroups() {
            const order = APP_STATE.scriptGroupOrder;
            const map = {};
            MOCK_SCRIPT_GROUPS.forEach(g => map[g.id] = g);
            const result = [];
            if (order.length > 0) { order.forEach(id => { if (map[id]) result.push(map[id]); });
                MOCK_SCRIPT_GROUPS.forEach(g => { if (!order.includes(g.id)) result.push(g); }); } else { result.push(
                    ...MOCK_SCRIPT_GROUPS); }
            return result;
        }

        function getSortedRuleGroups() {
            const order = APP_STATE.ruleGroupOrder;
            const map = {};
            MOCK_RULE_GROUPS.forEach(g => map[g.id] = g);
            const result = [];
            if (order.length > 0) { order.forEach(id => { if (map[id]) result.push(map[id]); });
                MOCK_RULE_GROUPS.forEach(g => { if (!order.includes(g.id)) result.push(g); }); } else { result.push(
                    ...MOCK_RULE_GROUPS); }
            return result;
        }

        function escapeHTML(s) { const d = document.createElement('div');
            d.textContent = s; return d.innerHTML; }

        function getFileTypeIcon(iconType, size) {
            const map = {
                word: { cls: 'word', text: 'W' },
                pdf: { cls: 'pdf', text: 'PDF' },
                ppt: { cls: 'ppt', text: 'P' },
                excel: { cls: 'excel', text: 'X' },
                img: { cls: 'img', text: '🖼️' },
                image: { cls: 'img', text: '🖼️' },
                video: { cls: 'video', text: '▶️' },
                audio: { cls: 'audio', text: '🎵' },
                doc: { cls: 'other', text: '📄' },
                other: { cls: 'other', text: '📦' },
                file: { cls: 'other', text: '📦' },
                txt: { cls: 'other', text: '📄' },
                zip: { cls: 'other', text: '📦' },
            };
            const m = map[iconType] || map.other;
            const style = size === 'large' ? 'width:72px;height:72px;border-radius:14px;font-size:28px;' : '';
            return `<span class="file-type-icon ${m.cls}"${style ? ` style="${style}"` : ''}>${m.text}</span>`;
        }

        function addLog(actionType, targetType, targetName, targetPath) {
            // 后端在每个路由中自动记录操作日志，前端无需再调用
            return;
        }

        // ==================== 权限UI更新 ====================
        function updatePermissionUI() {
            const adminOnlyElements = document.querySelectorAll('.admin-only');
            adminOnlyElements.forEach(el => el.style.display = isAdmin() ? '' : 'none');
            // 新建板块按钮
            const btnNewSection = document.getElementById('btnNewSection');
            if (btnNewSection) btnNewSection.style.display = isAdmin() ? '' : 'none';
            // 操作日志菜单
            const navLogs = document.getElementById('navLogs');
            if (navLogs) navLogs.style.display = isAdmin() ? '' : 'none';
            // 知识库成员菜单
            const navMembers = document.getElementById('navMembers');
            if (navMembers) navMembers.style.display = isAdmin() ? '' : 'none';
            // 左侧导航拖拽手柄可见性
            document.querySelectorAll('.ln-item .ln-drag').forEach(h => {
                h.style.display = isAdmin() ? '' : 'none';
            });
            // 角色显示
            document.getElementById('userRoleDisplay').textContent = isAdmin() ? '管理员' : '普通用户';
            document.getElementById('settingsRole').value = isAdmin() ? '管理员' : '普通用户';
            // 板块右键菜单仅管理员
            updateBatchMoveSelect();
            renderFilesLeftNav();
            renderScriptsLeftNav();
            renderRulesLeftNav();
            if (APP_STATE.currentView === 'files') renderFilesRight();
            if (APP_STATE.currentView === 'scripts') renderScriptsRight();
            if (APP_STATE.currentView === 'rules') renderRulesRight();
        }

        function updateBatchMoveSelect() {
            const bs = document.getElementById('batchMoveSectionSelect');
            if (bs) bs.innerHTML = '<option value="">📂 移动至板块...</option>' + getSortedSections().map(s =>
                `<option value="${s.id}">${escapeHTML(s.name)}</option>`).join('');
            const us = document.getElementById('uploadSectionSelect');
            if (us) us.innerHTML = getSortedSections().map(s =>
                `<option value="${s.id}">${escapeHTML(s.name)}</option>`).join('');
        }

        // ==================== 登录（对接后端 API） ====================
        async function handleAuth(e) {
            e.preventDefault();
            const u = document.getElementById('authUsername').value.trim();
            const p = document.getElementById('authPassword').value;
            const m = document.getElementById('authForm').dataset.mode;
            const btn = document.getElementById('authSubmitBtn');
            btn.disabled = true; btn.textContent = '处理中...';
            try {
                let data;
                if (m === 'login') {
                    data = await AuthAPI.login(u, p);
                } else {
                    if (document.getElementById('authConfirm').value !== p) { showToast('两次密码不一致', 'error'); return; }
                    if (!u || p.length < 4) { showToast('请填写用户名和密码（至少4位）', 'error'); return; }
                    data = await AuthAPI.register(u, p);
                    showToast('注册成功！', 'success');
                }
                setToken(data.token);
                await loginSuccess(data.user.username, data.user.role);
            } catch (err) {
                showToast(err.message || '操作失败', 'error');
            } finally {
                btn.disabled = false; btn.textContent = '登 录';
            }
        }

        async function loginSuccess(username, role) {
            APP_STATE.isLoggedIn = true;
            APP_STATE.userName = username;
            APP_STATE.userRole = role;
            document.getElementById('authOverlay').classList.add('hidden');
            document.getElementById('authOverlay').setAttribute('inert', '');
            document.getElementById('authPassword').value = '';
            document.getElementById('authConfirm').value = '';
            document.getElementById('appLayout').classList.add('visible');
            document.getElementById('userNameDisplay').textContent = username;
            document.getElementById('userAvatar').textContent = username.charAt(0).toUpperCase();
            document.getElementById('settingsUsername').value = username;
            updatePermissionUI();
            // 登录和 Cookie 自动恢复时统一初始化到智能搜索，避免静态模板中其他
            // view 的内联布局在首屏短暂或持续显示。
            switchView('chat');
            showToast(`欢迎回来，${username}！${isAdmin()?'（管理员）':''}`, 'success');
            // 从后端加载所有数据
            await loadAllData();
            renderFilesLeftNav();
            renderFilesRight();
            renderScriptsLeftNav();
            renderScriptsRight();
            renderLogs();
            renderKeywordCloud();
            renderIngestionJobs();
        }

        function handleLogout() {
            showConfirm('退出登录', '确定要退出登录吗？', async (ok) => { if (!ok) return; APP_STATE.isLoggedIn = false;
                clearToken();
                MOCK_USERS = []; MOCK_SECTIONS = []; MOCK_FILES = []; MOCK_INGESTION_JOBS = []; MOCK_SCRIPT_GROUPS = []; MOCK_SCRIPTS = []; MOCK_RULE_GROUPS = []; MOCK_RULES = []; MOCK_FAQS = []; MOCK_LOGS = [];
                if (ingestionSocket) { ingestionSocket.close(); ingestionSocket = null; }
                document.getElementById('authOverlay').classList.remove('hidden');
                document.getElementById('authOverlay').removeAttribute('inert');
                document.getElementById('appLayout').classList.remove('visible');
                document.getElementById('chatMessages').innerHTML = '';
                showToast('已安全退出', 'success'); });
        }

        async function autoLogin() {
            const token = getToken();
            if (!token) return false;
            try {
                const data = await AuthAPI.me();
                await loginSuccess(data.user.username, data.user.role);
                return true;
            } catch { clearToken(); return false; }
        }

        // ==================== 视图切换 ====================
        function switchView(viewName, navItem) {
            APP_STATE.currentView = viewName;
            navItem = navItem || document.querySelector(`.nav-item[data-view="${viewName}"]`);
            if (viewName === 'files') { APP_STATE.currentSectionId = null; }
            if (viewName === 'scripts') { APP_STATE.currentScriptGroupId = null; }
            if (viewName === 'rules') { APP_STATE.currentRuleGroupId = null; }
            document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
            if (navItem) navItem.classList.add('active');
            const titles = { chat: '智能搜索', files: '全部文件', rules: '规则通知', scripts: '话术库', 'ai-analysis': 'AI 分析中心',
                logs: '操作日志', settings: '系统设置', members: '知识库成员', faqs: 'FAQ库' };
            document.getElementById('pageTitle').textContent = titles[viewName] || '教辅知识库';
            document.querySelectorAll('.view').forEach(v => { v.classList.remove('active');
                v.style.display = 'none'; });
            const tv = document.getElementById(`view-${viewName}`);
            if (tv) { tv.classList.add('active');
                tv.style.display = viewName === 'files' || viewName === 'scripts' || viewName === 'rules' ? 'flex' : 'block'; }
            if (viewName === 'files') { updateBatchMoveSelect();
                renderFilesLeftNav();
                renderFilesRight(); } else if (viewName === 'scripts') { renderScriptsLeftNav();
                renderScriptsRight(); } else if (viewName === 'rules') { renderRulesLeftNav();
                renderRulesRight(); } else if (viewName === 'logs') { APP_STATE.logPage = 0;
                renderLogs(); } else if (viewName === 'ai-analysis') { renderKeywordCloud(); } else if (viewName ===
                'members') { renderMembers(); } else if (viewName ===
                'faqs') { renderFaqs(); } else if (viewName ===
                'chat') { setTimeout(() => document.getElementById('chatInput')?.focus(), 300); }
            if (viewName === 'ai-analysis') renderIngestionJobs();
            if (viewName === 'settings' && isAdmin()) loadDingtalkSettings();
            updateAllCounts();
        }

        function refreshCurrentView() {
            if (APP_STATE.currentView === 'files') { renderFilesLeftNav();
                renderFilesRight(); } else if (APP_STATE.currentView === 'scripts') { renderScriptsLeftNav();
                renderScriptsRight(); } else if (APP_STATE.currentView === 'rules') { renderRulesLeftNav();
                renderRulesRight(); } else if (APP_STATE.currentView === 'logs') renderLogs();
                else if (APP_STATE.currentView === 'members') renderMembers();
                else if (APP_STATE.currentView === 'faqs') reloadFaqs();
                else if (APP_STATE.currentView === 'ai-analysis') { renderKeywordCloud(); renderIngestionJobs(); }
                else if (APP_STATE.currentView === 'settings' && isAdmin()) loadDingtalkSettings();
            showToast('已刷新', 'success');
        }

        // ==================== 全部文件 - 左侧导航 ====================
        function renderFilesLeftNav() {
            const nav = document.getElementById('filesLeftNav');
            if (!nav) return;
            const sections = getSortedSections();
            const activeId = APP_STATE.currentSectionId;
            let html = `<div class="ln-all${activeId===null?' active':''}" data-section-id="" onclick="selectFileSection(null, this)">📋 全部</div>`;
            sections.forEach(s => {
                const count = MOCK_FILES.filter(f => f.sectionId === s.id).length +
                    pendingOnlyJobs().filter(job => job.section_id === s.id).length;
                html += `
                <div class="ln-item${activeId===s.id?' active':''}" data-section-id="${s.id}"
                     draggable="${isAdmin()?'true':'false'}"
                     ondragstart="handleLeftNavDragStart(event,'${s.id}','section')"
                     ondragover="handleLeftNavDragOver(event)"
                     ondrop="handleLeftNavDrop(event,'${s.id}','section')"
                     ondragend="handleLeftNavDragEnd(event)"
                     ondragleave="handleLeftNavDragLeave(event)"
                     onclick="selectFileSection('${s.id}', this)"
                     oncontextmenu="showSectionCtxMenu(event,'${s.id}')">
                    <span class="ln-drag" style="display:${isAdmin()?'':'none'};" onclick="event.stopPropagation();">⋮⋮</span>
                    <span class="ln-icon">${s.icon||'📂'}</span>
                    <span class="ln-name">${escapeHTML(s.name)}</span>
                    <span class="ln-count">${count}</span>
                </div>`;
            });
            nav.innerHTML = html;
        }

        function selectFileSection(sectionId, el) {
            APP_STATE.currentSectionId = sectionId;
            document.querySelectorAll('#filesLeftNav .ln-all, #filesLeftNav .ln-item').forEach(n => n.classList.remove(
                'active'));
            if (el) el.classList.add('active');
            renderFilesRight();
        }

        function showSectionCtxMenu(e, sid) {
            if (!isAdmin()) return;
            e.preventDefault();
            e.stopPropagation();
            APP_STATE.ctxTargetId = sid;
            APP_STATE.ctxTargetType = 'section';
            const m = document.getElementById('sectionCtxMenu');
            m.style.left = Math.min(e.clientX, window.innerWidth - 170) + 'px';
            m.style.top = Math.min(e.clientY, window.innerHeight - 120) + 'px';
            m.classList.add('show');
        }

        function sectionCtxAction(action) {
            const sid = APP_STATE.ctxTargetId;
            document.getElementById('sectionCtxMenu').classList.remove('show');
            if (!sid || !isAdmin()) return;
            const s = getSectionById(sid);
            if (!s) return;
            if (action === 'rename') {
                showPrompt('重命名板块', '板块名称', s.name, async (n) => {
                    if (!n || !n.trim() || n.trim() === s.name) return;
                    try {
                        await SectionAPI.rename(sid, n.trim());
                        await loadAllData();
                        renderFilesLeftNav();
                        renderFilesRight();
                        showToast('已重命名', 'success');
                    } catch (err) { showToast(err.message || '重命名失败', 'error'); }
                });
            } else if (action === 'delete') {
                if (MOCK_SECTIONS.length <= 1) { showToast('至少保留一个板块', 'error'); return; }
                const c = MOCK_FILES.filter(f => f.sectionId === sid).length;
                const ds = MOCK_SECTIONS.find(s => s.id !== sid);
                showConfirm('删除板块', `确定删除"${s.name}"？${c>0?`${c}个文件将移至"${ds?.name||'其他'}"。`:''}`, async (ok) => {
                    if (!ok) return;
                    try {
                        await SectionAPI.remove(sid);
                        APP_STATE.sectionOrder = APP_STATE.sectionOrder.filter(id => id !== sid);
                        saveSectionOrder(APP_STATE.sectionOrder);
                        if (APP_STATE.currentSectionId === sid) APP_STATE.currentSectionId = null;
                        await loadAllData();
                        renderFilesLeftNav();
                        renderFilesRight();
                        showToast('已删除', 'success');
                    } catch (err) { showToast(err.message || '删除失败', 'error'); }
                }, '删除');
            }
        }

        // ==================== 全部文件 - 右侧内容 ====================
        function renderFilesRight() {
            const container = document.getElementById('filesGridContainer');
            if (!container) return;
            const sections = getSortedSections();
            const activeId = APP_STATE.currentSectionId;
            const q = (document.getElementById('fileSearchInput')?.value || '').trim().toLowerCase();
            let total = 0;
            let html = '';
            if (activeId) {
                const s = getSectionById(activeId);
                let files = MOCK_FILES.filter(f => f.sectionId === activeId);
                let jobs = pendingOnlyJobs().filter(job => job.section_id === activeId);
                if (q) files = files.filter(f => f.name.toLowerCase().includes(q) || f.keywords.some(k => k.toLowerCase()
                    .includes(q)));
                if (q) jobs = jobs.filter(job => String(job.file_name || '').toLowerCase().includes(q));
                total = files.length + jobs.length;
                html +=
                    `<div class="section-divider"><span class="divider-icon">${s?.icon||'📂'}</span> ${escapeHTML(s?.name||'')} <span class="divider-count">(${total}个)</span></div>`;
                html += renderFileCards(files);
                html += renderIngestionCards(jobs);
            } else {
                const knownSectionIds = new Set(sections.map(section => section.id));
                sections.forEach(s => {
                    let files = MOCK_FILES.filter(f => f.sectionId === s.id);
                    let jobs = pendingOnlyJobs().filter(job => job.section_id === s.id);
                    if (q) files = files.filter(f => f.name.toLowerCase().includes(q) || f.keywords.some(k => k
                        .toLowerCase().includes(q)));
                    if (q) jobs = jobs.filter(job => String(job.file_name || '').toLowerCase().includes(q));
                    if (files.length === 0 && jobs.length === 0 && q) return;
                    total += files.length + jobs.length;
                    html +=
                        `<div class="section-divider" style="cursor:pointer;" onclick="selectFileSection('${s.id}', document.querySelector('#filesLeftNav [data-section-id=\"${s.id}\"]'))"><span class="divider-icon">${s.icon||'📂'}</span> ${escapeHTML(s.name)} <span class="divider-count">(${files.length + jobs.length}个)</span></div>`;
                    html += renderFileCards(files);
                    html += renderIngestionCards(jobs);
                });
                let unassignedFiles = MOCK_FILES.filter(file => !file.sectionId || !knownSectionIds.has(file.sectionId));
                let unassignedJobs = pendingOnlyJobs().filter(job => !job.section_id || !knownSectionIds.has(job.section_id));
                if (q) unassignedFiles = unassignedFiles.filter(file =>
                    file.name.toLowerCase().includes(q) || file.keywords.some(keyword => keyword.toLowerCase().includes(q))
                );
                if (q) unassignedJobs = unassignedJobs.filter(job => String(job.file_name || '').toLowerCase().includes(q));
                if (unassignedFiles.length || unassignedJobs.length) {
                    total += unassignedFiles.length + unassignedJobs.length;
                    html += `<div class="section-divider"><span class="divider-icon">📂</span> 未分类资料 <span class="divider-count">(${unassignedFiles.length + unassignedJobs.length}个)</span></div>`;
                    html += renderFileCards(unassignedFiles);
                    html += renderIngestionCards(unassignedJobs);
                }
            }
            const emptyMessage = q ? '未找到匹配文件，请清空搜索条件后重试。' : '暂无文件，可点击“上传文件”添加资料。';
            container.innerHTML = html || `<p style="color:var(--text-secondary);text-align:center;padding:30px;">${emptyMessage}</p>`;
            document.getElementById('fileResultCount').textContent = `共 ${total} 个文件`;
            updateAllCounts();
        }

        function renderFileCards(files) {
            return `<div class="file-grid">${files.map(f=>{const s=getSectionById(f.sectionId);const canEditFile=isAdmin();const activeJob=activeJobForDocument(f.id);return`
                <div class="file-card${APP_STATE.selectedItems.has(f.id)?' selected':''}" data-file-id="${f.id}"
                     onclick="handleFileClick('${f.id}',event)" oncontextmenu="showFileCtxMenu(event,'${f.id}')" ondblclick="previewFile('${f.id}')">
                    ${isAdmin()?`<input type="checkbox" class="file-checkbox" ${APP_STATE.selectedItems.has(f.id)?'checked':''} onclick="event.stopPropagation();toggleFileSelect('${f.id}')">`:''}
                    <span class="card-icon">${getFileTypeIcon(f.iconType)}</span><span class="card-name">${escapeHTML(f.name)}</span>
                    <span class="card-meta">${f.size} · ${f.date}${!canEditFile?' · 🔒':''}</span>
                    <span class="card-tags"><span class="tag">${f.type}</span>${s?`<span class="tag section-tag">📂 ${escapeHTML(s.name)}</span>`:''}</span>
                    ${activeJob ? renderIngestionProgress(activeJob, true) : ''}
                </div>`;}).join('')}</div>`;
        }

        function renderIngestionProgress(job, compact = false) {
            const progress = Math.max(0, Math.min(100, Number(job.progress || 0)));
            const indeterminate = isIndeterminateJob(job);
            const isError = ['failed', 'interrupted'].includes(job.status);
            const needsReview = job.status === 'needs_review';
            const stateClass = isError ? ' error' : needsReview ? ' review' : '';
            const action = (isError || needsReview)
                ? `<button type="button" class="ingestion-recovery" onclick="event.stopPropagation();switchView('ai-analysis')">前往处理</button>`
                : '';
            const progressValue = indeterminate ? '' : ` aria-valuenow="${progress}"`;
            return `<div class="ingestion-progress${compact ? ' compact' : ''}${stateClass}${indeterminate ? ' indeterminate' : ''}" role="status" aria-label="${escapeHTML(job.file_name || '文件')} ${escapeHTML(ingestionStatusLabel(job.status))}${indeterminate ? '，正在处理' : ` ${progress}%`}">
                <div class="ingestion-progress-head"><span>${escapeHTML(ingestionStatusLabel(job.status))}</span><strong>${indeterminate ? '实时处理中' : `${progress}%`}</strong></div>
                <div class="ingestion-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100"${progressValue}><i style="${indeterminate ? '' : `transform:scaleX(${progress / 100})`}"></i></div>
                ${action}
            </div>`;
        }

        function renderIngestionCards(jobs) {
            if (!jobs.length) return '';
            return `<div class="file-grid">${jobs.map(job => `
                <div class="file-card ingestion-file-card" data-status="${escapeHTML(job.status || '')}">
                    <span class="card-icon">⏳</span>
                    <span class="card-name">${escapeHTML(job.file_name || '正在处理的文件')}</span>
                    <span class="card-meta">${escapeHTML(job.uploader || '')} · ${escapeHTML(formatFileSize(job.size_bytes))}</span>
                    <span class="card-tags"><span class="tag">${escapeHTML(job.file_type || '文件')}</span><span class="tag section-tag">正在入库</span></span>
                    ${renderIngestionProgress(job)}
                    ${job.preview_available ? `<button type="button" class="ingestion-preview" onclick="event.stopPropagation();previewCitationSource('${escapeHTML(job.document_id)}','${escapeHTML(job.document_version_id)}','${escapeHTML(job.file_name || '文件')}',null)">预览当前文件</button>` : '<small class="ingestion-preview-hint">预览生成后可打开</small>'}
                </div>`).join('')}</div>`;
        }

        function handleFileClick(fid, e) { if (isAdmin() && (e.ctrlKey || e.metaKey)) { e.preventDefault(); toggleFileSelect(fid); } else { previewFile(fid); } }

        function toggleFileSelect(fid) { if (!isAdmin()) return; APP_STATE.selectedItems.has(fid) ? APP_STATE.selectedItems.delete(fid) : APP_STATE
                .selectedItems.add(fid);
            renderFilesRight(); }

        function showFileCtxMenu(e, fid) {
            e.preventDefault();
            APP_STATE.ctxTargetId = fid;
            APP_STATE.ctxTargetType = 'file';
            const f = MOCK_FILES.find(f => f.id === fid);
            const canEditFile = Boolean(f && isAdmin());
            document.getElementById('ctxRename').style.display = canEditFile ? '' : 'none';
            document.getElementById('ctxMove').style.display = canEditFile ? '' : 'none';
            document.getElementById('ctxDelete').style.display = canEditFile ? '' : 'none';
            const m = document.getElementById('contextMenu');
            m.style.left = Math.min(e.clientX, window.innerWidth - 170) + 'px';
            m.style.top = Math.min(e.clientY, window.innerHeight - 200) + 'px';
            m.classList.add('show');
        }

        function ctxAction(action) {
            const fid = APP_STATE.ctxTargetId;
            document.getElementById('contextMenu').classList.remove('show');
            if (!fid) return;
            if (['rename', 'move', 'delete'].includes(action) && !isAdmin()) {
                showToast('仅管理员可修改文件', 'error');
                return;
            }
            switch (action) { case 'preview':
                    previewFile(fid); break; case 'download':
                    downloadFile(fid); break; case 'rename':
                    openRenameModal(fid, 'file'); break; case 'move':
                    openMoveModal([fid]); break; case 'delete':
                    deleteFile(fid); break; }
        }

        /* 在线预览：后端 /documents/{id}/preview 可内联输出 PDF / 图片 / 文本。
         * 预览方式由后端 preview_kind 判定（Office 文档只有转出 PDF 预览件后才是 pdf），
         * 前端不再"只对图片预览、其它格式只显示一个大图标"。 */
        function inferPreviewKind(file) {
            const type = String(file.iconType || '').toLowerCase();
            if (type === 'img' || type === 'image') return 'image';
            if (type === 'pdf') return 'pdf';
            if (type === 'txt') return 'text';
            if (['ppt', 'word', 'excel'].includes(type)) return 'pdf';
            return 'none';
        }

        function documentFileUrl(file, action) {
            const version = file.versionId ? `?version_id=${encodeURIComponent(file.versionId)}` : '';
            return `/api/v1/documents/${encodeURIComponent(file.id)}/${action}${version}`;
        }

        function previewFile(fid) {
            const f = MOCK_FILES.find(item => item.id === fid);
            if (!f) return;
            const kind = f.previewKind || inferPreviewKind(f);
            const previewUrl = documentFileUrl(f, 'preview');
            const downloadUrl = documentFileUrl(f, 'download');
            document.getElementById('previewTitle').innerHTML = getFileTypeIcon(f.iconType) + ' ' + escapeHTML(f.name);
            const modal = document.getElementById('previewModal').querySelector('.modal');
            if (modal) modal.classList.toggle('modal-wide', kind !== 'none');
            let h = '';
            if (kind === 'pdf' || kind === 'text') {
                h = `<iframe class="preview-frame" src="${previewUrl}" title="预览 ${escapeHTML(f.name)}"></iframe>`;
            } else if (kind === 'image') {
                h = `<div class="preview-image-wrap"><img src="${previewUrl}" alt="${escapeHTML(f.name)}" /></div>`;
            } else {
                h = `<div class="preview-unsupported"><span class="preview-unsupported-icon">${getFileTypeIcon(f.iconType, 'large')}</span><p>该格式暂不支持在线预览。</p><p class="preview-unsupported-hint">请点击右下角「下载」用本地应用打开。</p></div>`;
            }
            const size = f.size ? `<p>📦 大小：${escapeHTML(f.size)}</p>` : '';
            h += `<div class="preview-meta"><p><strong>📋 AI摘要：</strong>${escapeHTML(f.aiSummary || '暂无')}</p>${size}<p>📂 板块：${escapeHTML(getSectionName(f.sectionId))}</p><p>👤 上传者：${escapeHTML(f.owner)}</p></div>`;
            document.getElementById('previewContent').innerHTML = h;
            document.getElementById('previewDownloadBtn').onclick = () => window.open(downloadUrl, '_blank', 'noopener');
            document.getElementById('previewModal').style.display = 'flex'; }

        function previewCitationSource(documentId, versionId, fileName, pageNumber) {
            if (!documentId || !versionId) return;
            const page = Number(pageNumber) > 0 ? Number(pageNumber) : null;
            const previewUrl = `/api/v1/documents/${encodeURIComponent(documentId)}/preview?version_id=${encodeURIComponent(versionId)}` +
                (page ? `#page=${page}` : '');
            const downloadUrl = `/api/v1/documents/${encodeURIComponent(documentId)}/download?version_id=${encodeURIComponent(versionId)}`;
            document.getElementById('previewTitle').textContent = `${fileName || '知识库资料'}${page ? ` · 第 ${page} 页` : ''}`;
            document.getElementById('previewContent').innerHTML =
                `<iframe class="citation-preview-frame" src="${previewUrl}" title="预览 ${escapeHTML(fileName || '知识库资料')}"></iframe>`;
            document.getElementById('previewDownloadBtn').onclick = () => window.open(downloadUrl, '_blank', 'noopener');
            document.getElementById('previewModal').style.display = 'flex';
        }

        function closePreviewModal() { document.getElementById('previewModal').style.display = 'none'; }

        async function downloadFile(fid) {
            const f = MOCK_FILES.find(f => f.id === fid);
            if (!f) return;
            try {
                const blob = await FileAPI.download(fid);
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = f.name;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
                showToast(`下载：${f.name}`, 'success');
            } catch (e) {
                showToast('下载失败: ' + e.message, 'error');
            }
        }

        function deleteFile(fid) { const f = MOCK_FILES.find(f => f.id === fid); if (!f) return; if (!isAdmin()) {
                showToast('无权限删除此文件', 'error'); return; }
            showConfirm('删除文件', `确定删除"${f.name}"？`, async (ok) => {
                if (!ok) return;
                try {
                    await FileAPI.remove(fid);
                    APP_STATE.selectedItems.delete(fid);
                    await loadAllData();
                    renderFilesRight();
                    renderFilesLeftNav();
                    updateStorage();
                    showToast('已删除', 'success');
                } catch (err) { showToast(err.message || '删除失败', 'error'); }
            }, '删除'); }

        function batchDeleteItems() { if (!isAdmin()) { showToast('仅管理员可删除文件', 'error'); return; } if (APP_STATE.selectedItems.size === 0) { showToast('请先选择文件', 'error'); return; } const ids = [
                ...APP_STATE.selectedItems
            ]; const noPerm = ids.filter(id => { const f = MOCK_FILES.find(f => f.id === id); return f && !canDelete(f
            .owner); }); if (noPerm.length > 0 && !isAdmin()) { showToast(`有 ${noPerm.length} 个文件无权限删除`, 'error'); return; }
            showConfirm('批量删除', `确定删除选中 ${ids.length} 个文件？`, async (ok) => {
                if (!ok) return;
                try {
                    await FileAPI.batchDelete(ids);
                    APP_STATE.selectedItems.clear();
                    await loadAllData();
                    renderFilesRight();
                    renderFilesLeftNav();
                    updateStorage();
                    showToast('批量删除完成', 'success');
                } catch (err) { showToast(err.message || '批量删除失败', 'error'); }
            }, '删除'); }

        function openRenameModal(fid, type) { if (!isAdmin()) { showToast('仅管理员可重命名文件', 'error'); return; } APP_STATE.renameTargetId = fid;
            APP_STATE.renameTargetType = type; const f = MOCK_FILES.find(f => f.id === fid); if (f) { document.getElementById(
                    'renameInput').value = f.name;
                document.querySelector('#renameModal h3').textContent = '✏️ 重命名文件';
                document.getElementById('renameModal').style.display = 'flex'; } }

        function closeRenameModal() { document.getElementById('renameModal').style.display = 'none'; }

        async function confirmRename() {
            if (!isAdmin()) { showToast('仅管理员可重命名文件', 'error'); return; }
            const n = document.getElementById('renameInput').value.trim();
            if (!n || !APP_STATE.renameTargetId) return;
            const f = MOCK_FILES.find(f => f.id === APP_STATE.renameTargetId);
            if (!f) return;
            if (!canEdit(f.owner)) { showToast('无权限重命名', 'error'); return; }
            if (n === f.name) { closeRenameModal(); return; }
            try {
                await FileAPI.update(APP_STATE.renameTargetId, { name: n });
                closeRenameModal();
                await loadAllData();
                renderFilesRight();
                showToast('已重命名', 'success');
            } catch (err) { showToast(err.message || '重命名失败', 'error'); }
        }

        function openMoveModal(fids) { if (!isAdmin()) { showToast('仅管理员可移动文件', 'error'); return; } APP_STATE.moveTargetIds = fids;
            document.getElementById('moveSectionSelect').innerHTML = getSortedSections().map(s =>
                `<option value="${s.id}">${escapeHTML(s.name)}</option>`).join('');
            document.getElementById('moveModal').style.display = 'flex'; }

        function closeMoveModal() { document.getElementById('moveModal').style.display = 'none'; }

        async function confirmMoveToSection() {
            const sid = document.getElementById('moveSectionSelect').value;
            if (!sid || APP_STATE.moveTargetIds.length === 0) return;
            const sn = getSectionName(sid);
            try {
                await FileAPI.batchMove([...APP_STATE.moveTargetIds], sid);
                APP_STATE.moveTargetIds.forEach(fid => APP_STATE.selectedItems.delete(fid));
                closeMoveModal();
                await loadAllData();
                renderFilesRight();
                renderFilesLeftNav();
                showToast(`已移动到"${sn}"`, 'success');
            } catch (err) { showToast(err.message || '移动失败', 'error'); }
        }

        async function batchMoveToSection(sid) {
            if (!isAdmin()) { showToast('仅管理员可移动文件', 'error'); return; }
            if (!sid || APP_STATE.selectedItems.size === 0) {
                if (!sid) return;
                showToast('请先选择文件', 'error'); return;
            }
            const sn = getSectionName(sid);
            try {
                await FileAPI.batchMove([...APP_STATE.selectedItems], sid);
                APP_STATE.selectedItems.clear();
                await loadAllData();
                renderFilesRight();
                renderFilesLeftNav();
                showToast(`已移动到"${sn}"`, 'success');
            } catch (err) { showToast(err.message || '移动失败', 'error'); }
        }

        function openNewSectionModal() { if (!isAdmin()) { showToast('仅管理员可创建板块', 'error'); return; }
            document.getElementById('newSectionNameInput').value = '';
            document.getElementById('newSectionModal').style.display = 'flex'; }

        function closeNewSectionModal() { document.getElementById('newSectionModal').style.display = 'none'; }

        async function confirmNewSection() {
            if (!isAdmin()) return;
            const n = document.getElementById('newSectionNameInput').value.trim();
            if (!n) { showToast('请输入板块名称', 'error'); return; }
            if (MOCK_SECTIONS.some(s => s.name.toLowerCase() === n.toLowerCase())) { showToast('已存在同名板块', 'error'); return; }
            try {
                await SectionAPI.create(n);
                closeNewSectionModal();
                await loadAllData();
                renderFilesLeftNav();
                renderFilesRight();
                updateBatchMoveSelect();
                showToast(`板块"${n}"已创建`, 'success');
            } catch (err) { showToast(err.message || '创建板块失败', 'error'); }
        }

        // ==================== 话术库 - 左侧导航 ====================
        function renderScriptsLeftNav() {
            const nav = document.getElementById('scriptsLeftNav');
            if (!nav) return;
            const groups = getSortedScriptGroups();
            const activeId = APP_STATE.currentScriptGroupId;
            let html =
                `<div class="ln-all${activeId===null?' active':''}" data-group-id="" onclick="selectScriptGroup(null, this)">📋 全部</div>`;
            groups.forEach(g => {
                const count = MOCK_SCRIPTS.filter(s => s.groupId === g.id).length;
                html += `
                <div class="ln-item${activeId===g.id?' active':''}" data-group-id="${g.id}"
                     draggable="${isAdmin()?'true':'false'}"
                     ondragstart="handleLeftNavDragStart(event,'${g.id}','group')"
                     ondragover="handleLeftNavDragOver(event)"
                     ondrop="handleLeftNavDrop(event,'${g.id}','group')"
                     ondragend="handleLeftNavDragEnd(event)"
                     ondragleave="handleLeftNavDragLeave(event)"
                     onclick="selectScriptGroup('${g.id}', this)"
                     oncontextmenu="showGroupCtxMenu(event,'${g.id}')">
                    <span class="ln-drag" style="display:${isAdmin()?'':'none'};" onclick="event.stopPropagation();">⋮⋮</span>
                    <span class="ln-icon">💬</span>
                    <span class="ln-name">${escapeHTML(g.name)}</span>
                    <span class="ln-count">${count}</span>
                </div>`;
            });
            nav.innerHTML = html;
        }

        function selectScriptGroup(gid, el) {
            APP_STATE.currentScriptGroupId = gid;
            document.querySelectorAll('#scriptsLeftNav .ln-all, #scriptsLeftNav .ln-item').forEach(n => n.classList.remove(
                'active'));
            if (el) el.classList.add('active');
            renderScriptsRight();
        }

        function showGroupCtxMenu(e, gid) {
            e.preventDefault();
            e.stopPropagation();
            APP_STATE.ctxTargetId = gid;
            APP_STATE.ctxTargetType = 'scriptGroup';
            const g = MOCK_SCRIPT_GROUPS.find(g => g.id === gid);
            const canEditGroup = g ? canEdit(g.owner) : false;
            document.getElementById('ctxGroupRename').style.display = canEditGroup ? '' : 'none';
            document.getElementById('ctxGroupDelete').style.display = canEditGroup ? '' : 'none';
            const m = document.getElementById('groupCtxMenu');
            m.style.left = Math.min(e.clientX, window.innerWidth - 170) + 'px';
            m.style.top = Math.min(e.clientY, window.innerHeight - 120) + 'px';
            m.classList.add('show');
        }

        function groupCtxAction(action) {
            const gid = APP_STATE.ctxTargetId;
            document.getElementById('groupCtxMenu').classList.remove('show');
            if (!gid) return;
            const g = MOCK_SCRIPT_GROUPS.find(g => g.id === gid);
            if (!g) return;
            if (action === 'rename') {
                if (!canEdit(g.owner)) { showToast('无权限重命名', 'error'); return; }
                showPrompt('重命名话术组', '组名称', g.name, async (n) => {
                    if (!n || !n.trim() || n.trim() === g.name) return;
                    try {
                        await GroupAPI.rename(gid, n.trim());
                        await loadAllData();
                        renderScriptsLeftNav();
                        renderScriptsRight();
                        showToast('已重命名', 'success');
                    } catch (err) { showToast(err.message || '重命名失败', 'error'); }
                });
            } else if (action === 'delete') {
                if (!canDelete(g.owner)) { showToast('无权限删除', 'error'); return; }
                showConfirm('删除话术组', `确定删除话术组"${g.name}"及其所有话术？`, async (ok) => {
                    if (!ok) return;
                    try {
                        await GroupAPI.remove(gid);
                        APP_STATE.scriptGroupOrder = APP_STATE.scriptGroupOrder.filter(id => id !== gid);
                        saveScriptGroupOrder(APP_STATE.scriptGroupOrder);
                        if (APP_STATE.currentScriptGroupId === gid) APP_STATE.currentScriptGroupId = null;
                        await loadAllData();
                        renderScriptsLeftNav();
                        renderScriptsRight();
                        showToast('已删除', 'success');
                    } catch (err) { showToast(err.message || '删除失败', 'error'); }
                }, '删除');
            }
        }

        // ==================== 话术库 - 右侧内容 ====================
        function renderScriptsRight() {
            const container = document.getElementById('scriptsGridContainer');
            if (!container) return;
            const groups = getSortedScriptGroups();
            const activeId = APP_STATE.currentScriptGroupId;
            const q = (document.getElementById('scriptSearchInput')?.value || '').trim().toLowerCase();
            let total = 0;
            let html = '';
            if (activeId) {
                const g = MOCK_SCRIPT_GROUPS.find(g => g.id === activeId);
                let scripts = MOCK_SCRIPTS.filter(s => s.groupId === activeId);
                if (q) scripts = scripts.filter(s => s.title.toLowerCase().includes(q) || s.content.replace(/<[^>]*/g, '')
                    .toLowerCase().includes(q));
                total = scripts.length;
                html +=
                    `<div class="section-divider"><span class="divider-icon">💬</span> ${escapeHTML(g?.name||'')} <span class="divider-count">(${scripts.length}条)</span></div>`;
                html += renderScriptCards(scripts);
            } else {
                groups.forEach(g => {
                    let scripts = MOCK_SCRIPTS.filter(s => s.groupId === g.id);
                    if (q) scripts = scripts.filter(s => s.title.toLowerCase().includes(q) || s.content.replace(
                        /<[^>]*>/g, '').toLowerCase().includes(q));
                    if (scripts.length === 0 && q) return;
                    total += scripts.length;
                    html +=
                        `<div class="section-divider" style="cursor:pointer;" onclick="selectScriptGroup('${g.id}', document.querySelector('#scriptsLeftNav [data-group-id=\"${g.id}\"]'))"><span class="divider-icon">💬</span> ${escapeHTML(g.name)} <span class="divider-count">(${scripts.length}条)</span></div>`;
                    html += renderScriptCards(scripts);
                });
            }
            container.innerHTML = html || '<p style="color:var(--text-secondary);text-align:center;padding:30px;">暂无话术</p>';
            document.getElementById('scriptResultCount').textContent = `共 ${total} 条话术`;
            updateAllCounts();
        }

        function renderScriptCards(scripts) {
            return `<div class="file-grid" style="grid-template-columns:repeat(auto-fill,minmax(280px,1fr));">${scripts.map(s=>{const canEditScript=canEdit(s.owner);const plain=s.content.replace(/<[^>]*>/g,'');const preview=plain.length>60?plain.substring(0,60)+'...':plain;const g=MOCK_SCRIPT_GROUPS.find(g=>g.id===s.groupId);return`
                <div class="script-card" style="cursor:pointer;" onclick="previewScript('${s.id}')">
                    <div class="sc-title">${escapeHTML(s.title)}${!canEditScript?' 🔒':''}</div>
                    ${s.summary?`<div class="sc-summary">🤖 ${escapeHTML(s.summary)}</div>`:''}
                    <div class="sc-preview">${escapeHTML(preview)}</div>
                    <div class="sc-keywords">${(s.keywords||[]).map(k=>`<span class="tag keyword-tag">🔑 ${escapeHTML(k)}</span>`).join('')}</div>
                    <div class="sc-tags">${(s.tags||[]).map(t=>`<span class="tag ai-tag">${escapeHTML(t)}</span>`).join('')}${g&&!APP_STATE.currentScriptGroupId?`<span class="tag section-tag">📁 ${escapeHTML(g.name)}</span>`:''}</div>
                    <div class="sc-meta">📅 ${s.updatedAt} · 👤 ${escapeHTML(s.owner)} · 👁️ 点击查看全文</div>
                    <div class="sc-actions">
                        <button class="btn-copy" onclick="event.stopPropagation();copyScriptContent('${s.id}')">📋 复制</button>
                        ${canEditScript?`<button class="btn btn-ghost btn-xs" onclick="event.stopPropagation();openScriptEditor('${s.id}')">✏️ 编辑</button><button class="btn btn-ghost btn-xs" style="color:var(--danger);" onclick="event.stopPropagation();deleteScript('${s.id}')">🗑️</button>`:''}
                    </div>
                </div>`;}).join('')}</div>`;
        }

        function deleteScript(sid) { const s = MOCK_SCRIPTS.find(s => s.id === sid); if (!s) return; if (!canDelete(s.owner)) {
                showToast('无权限删除', 'error'); return; }
            showConfirm('删除话术', `确定删除"${s.title}"？`, async (ok) => {
                if (!ok) return;
                try {
                    await ScriptAPI.remove(sid);
                    await loadAllData();
                    renderScriptsRight();
                    renderScriptsLeftNav();
                    updateAllCounts();
                    showToast('已删除', 'success');
                } catch (err) { showToast(err.message || '删除失败', 'error'); }
            }, '删除'); }

        function copyScriptContent(sid) { const s = MOCK_SCRIPTS.find(s => s.id === sid); if (!s) return; const plain = s.content
                .replace(/<[^>]*>/g, '').replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g,
                    '>');
            navigator.clipboard.writeText(plain).then(() => showToast('📋 已复制', 'success')).catch(() => { const ta = document
                    .createElement('textarea');
                ta.value = plain;
                document.body.appendChild(ta);
                ta.select();
                document.execCommand('copy');
                document.body.removeChild(ta);
                showToast('📋 已复制', 'success'); }); }

        function previewScript(sid) {
            const s = MOCK_SCRIPTS.find(x => x.id === sid);
            if (!s) { console.error('previewScript: 话术不存在, sid=', sid); return; }
            const g = MOCK_SCRIPT_GROUPS.find(x => x.id === s.groupId);
            document.getElementById('scriptPreviewTitle').textContent = s.title;
            document.getElementById('scriptPreviewMeta').innerHTML =
                `📁 ${escapeHTML(g?.name||'未分组')} · 👤 ${escapeHTML(s.owner)} · 📅 ${s.updatedAt||s.createdAt||''}` +
                (s.tags && s.tags.length ? ` · 🏷️ ${escapeHTML(s.tags.join(', '))}` : '');
            const content = s.content || '';
            document.getElementById('scriptPreviewContent').innerHTML = content.trim() ? content : '<p style="color:var(--text-secondary);">暂无内容</p>';
            document.getElementById('scriptPreviewCopyBtn').onclick = () => copyScriptContent(sid);
            document.getElementById('scriptPreviewModal').style.display = 'flex';
        }

        function closeScriptPreview() {
            document.getElementById('scriptPreviewModal').style.display = 'none';
        }

        function openNewScriptGroupModal() { document.getElementById('newGroupNameInput').value = '';
            document.getElementById('newGroupModal').style.display = 'flex'; }

        function closeNewGroupModal() { document.getElementById('newGroupModal').style.display = 'none'; }

        async function confirmNewGroup() {
            const n = document.getElementById('newGroupNameInput').value.trim();
            if (!n) { showToast('请输入组名称', 'error'); return; }
            if (MOCK_SCRIPT_GROUPS.some(g => g.name.toLowerCase() === n.toLowerCase())) { showToast('已存在同名话术组', 'error'); return; }
            try {
                await GroupAPI.create(n);
                closeNewGroupModal();
                await loadAllData();
                renderScriptsLeftNav();
                renderScriptsRight();
                showToast(`话术组"${n}"已创建`, 'success');
            } catch (err) { showToast(err.message || '创建话术组失败', 'error'); }
        }

        function openScriptEditor(sid = null) { document.getElementById('scriptEditId').value = sid || '';
            document.getElementById('scriptEditorTitle').textContent = sid ? '✏️ 编辑话术' : '➕ 新建话术';
            document.getElementById('scriptGroupSelect').innerHTML = MOCK_SCRIPT_GROUPS.map(g =>
                `<option value="${g.id}">💬 ${escapeHTML(g.name)}</option>`).join(''); if (sid) { const s = MOCK_SCRIPTS.find(
                s => s.id === sid); if (s) { if (!canEdit(s.owner)) { showToast('无权限编辑', 'error'); return; }
                document.getElementById('scriptTitleInput').value = s.title;
                document.getElementById('scriptGroupSelect').value = s.groupId;
                document.getElementById('richEditor').innerHTML = s.content;
                document.getElementById('scriptTagsInput').value = s.tags.join(','); } } else { document.getElementById(
                'scriptTitleInput').value = '';
                document.getElementById('richEditor').innerHTML = '';
            document.getElementById('scriptTagsInput').value = ''; if (APP_STATE.currentScriptGroupId) document
                .getElementById('scriptGroupSelect').value = APP_STATE.currentScriptGroupId; }
            document.getElementById('scriptEditorModal').style.display = 'flex';
            setTimeout(() => document.getElementById('scriptTitleInput').focus(), 200); }

        function closeScriptEditor() { document.getElementById('scriptEditorModal').style.display = 'none'; }

        /* 富文本命令。话术编辑器 id 是 richEditor，规则编辑器是 ruleRichEditor；
         * 原实现硬编码了 richEditor，导致在规则编辑器里点 B/I 会把焦点抢到话术编辑器。 */
        function activeRichEditor() {
            return document.querySelector('.rich-editor:focus')
                || document.getElementById('ruleRichEditor')
                || document.getElementById('richEditor'); }

        function execRichCmd(cmd) { document.execCommand(cmd, false, null);
            const editor = activeRichEditor(); if (editor) editor.focus(); }

        /* 字体颜色：8 色与钉钉客户端一致，另加“默认颜色”。
         * 系统内用 execCommand('foreColor') 写入 <font color>，渲染真实颜色；
         * 钉钉 markdown 不支持颜色，推送时由后端把颜色转成 emoji 色点前缀。 */
        function toggleRichColorPicker(event) { if (event) event.preventDefault();
            const panel = document.getElementById('richColorPicker'); if (!panel) return;
            if (panel.style.display === 'block') { panel.style.display = 'none'; return; }
            const button = event && event.currentTarget;
            if (button) { const rect = button.getBoundingClientRect();
                panel.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - 260)) + 'px';
                panel.style.top = (rect.bottom + 6) + 'px'; }
            panel.style.display = 'block'; }

        function applyRichColor(color) { const editor = activeRichEditor();
            if (editor) editor.focus();
            document.execCommand('foreColor', false, color);
            if (editor) editor.focus();
            const panel = document.getElementById('richColorPicker');
            if (panel) panel.style.display = 'none'; }

        /* 点击面板外部收起 */
        document.addEventListener('click', function (event) {
            const panel = document.getElementById('richColorPicker');
            if (!panel || panel.style.display !== 'block') return;
            if (panel.contains(event.target)) return;
            if (event.target && event.target.closest && event.target.closest('.rich-color-toggle')) return;
            panel.style.display = 'none'; });

        function aiAnalyzeScript(content) { const plain = content.replace(/<[^>]*>/g, '').trim(); const pool = ['客户', '产品',
                '服务', '价格', '支持', '升级', '保障', '技术', '团队', '效率', '管理', '智能化', 'AI', '知识库'
            ]; const found = pool.filter(k => plain.includes(k)); const kw = found.sort(() => Math.random() - 0.5).slice(0, Math
            .min(5, Math.max(3, found.length))); while (kw.length < 3) { const fb = ['核心话术', '标准回复', '常用模板', '客户沟通',
            '服务流程'][Math.floor(Math.random() * 5)]; if (!kw.includes(fb)) kw.push(fb); } let summary = plain.length > 18 ?
            plain.substring(0, 18) + '...' : plain; return { keywords: kw.slice(0, 5), summary: summary.length > 20 ? summary
                .substring(0, 18) + '...' : summary }; }

        async function saveScript() {
            const gid = document.getElementById('scriptGroupSelect').value;
            const title = document.getElementById('scriptTitleInput').value.trim();
            const content = document.getElementById('richEditor').innerHTML.trim();
            const ts = document.getElementById('scriptTagsInput').value.trim();
            if (!gid) { showToast('请选择话术组', 'error'); return; }
            if (!title) { showToast('请输入话术标题', 'error'); return; }
            if (!content || content === '<br>') { showToast('请输入正文', 'error'); return; }
            const tags = ts ? ts.split(/[,，]/).map(t => t.trim()).filter(t => t) : [];
            const eid = document.getElementById('scriptEditId').value;
            try {
                if (eid) {
                    const s = MOCK_SCRIPTS.find(s => s.id === eid);
                    if (!s) { showToast('话术不存在', 'error'); return; }
                    if (!canEdit(s.owner)) { showToast('无权限编辑', 'error'); return; }
                    await ScriptAPI.update(eid, { title, content, groupId: gid, tags: tags.join(',') });
                    showToast('话术已更新', 'success');
                } else {
                    await ScriptAPI.create({ title, content, groupId: gid, tags: tags.join(',') });
                    showToast('话术已创建', 'success');
                }
                closeScriptEditor();
                await loadAllData();
                renderScriptsRight();
                renderScriptsLeftNav();
                updateAllCounts();
            } catch (err) { showToast(err.message || '保存话术失败', 'error'); }
        }

        // ==================== 规则通知 - 左侧导航 ====================
        function renderRulesLeftNav() {
            const nav = document.getElementById('rulesLeftNav');
            if (!nav) return;
            const groups = getSortedRuleGroups();
            const activeId = APP_STATE.currentRuleGroupId;
            let html =
                `<div class="ln-all${activeId===null?' active':''}" data-rule-group-id="" onclick="selectRuleGroup(null, this)">📋 全部</div>`;
            groups.forEach(g => {
                const count = MOCK_RULES.filter(r => r.groupId === g.id).length;
                html += `
                <div class="ln-item${activeId===g.id?' active':''}" data-rule-group-id="${g.id}"
                     draggable="${isAdmin()?'true':'false'}"
                     ondragstart="handleLeftNavDragStart(event,'${g.id}','ruleGroup')"
                     ondragover="handleLeftNavDragOver(event)"
                     ondrop="handleLeftNavDrop(event,'${g.id}','ruleGroup')"
                     ondragend="handleLeftNavDragEnd(event)"
                     ondragleave="handleLeftNavDragLeave(event)"
                     onclick="selectRuleGroup('${g.id}', this)"
                     oncontextmenu="showRuleGroupCtxMenu(event,'${g.id}')">
                    <span class="ln-drag" style="display:${isAdmin()?'':'none'};" onclick="event.stopPropagation();">⋮⋮</span>
                    <span class="ln-icon">📢</span>
                    <span class="ln-name">${escapeHTML(g.name)}</span>
                    <span class="ln-count">${count}</span>
                </div>`;
            });
            nav.innerHTML = html;
        }

        function selectRuleGroup(gid, el) {
            APP_STATE.currentRuleGroupId = gid;
            document.querySelectorAll('#rulesLeftNav .ln-all, #rulesLeftNav .ln-item').forEach(n => n.classList.remove(
                'active'));
            if (el) el.classList.add('active');
            renderRulesRight();
        }

        function showRuleGroupCtxMenu(e, gid) {
            if (!isAdmin()) return;
            e.preventDefault();
            e.stopPropagation();
            APP_STATE.ctxTargetId = gid;
            APP_STATE.ctxTargetType = 'ruleGroup';
            const g = MOCK_RULE_GROUPS.find(g => g.id === gid);
            const canEditGroup = Boolean(g && isAdmin());
            document.getElementById('ctxRuleGroupRename').style.display = canEditGroup ? '' : 'none';
            document.getElementById('ctxRuleGroupDelete').style.display = canEditGroup ? '' : 'none';
            const m = document.getElementById('ruleGroupCtxMenu');
            m.style.left = Math.min(e.clientX, window.innerWidth - 170) + 'px';
            m.style.top = Math.min(e.clientY, window.innerHeight - 120) + 'px';
            m.classList.add('show');
        }

        function ruleGroupCtxAction(action) {
            const gid = APP_STATE.ctxTargetId;
            document.getElementById('ruleGroupCtxMenu').classList.remove('show');
            if (!gid) return;
            const g = MOCK_RULE_GROUPS.find(g => g.id === gid);
            if (!g) return;
            if (action === 'rename') {
                if (!isAdmin()) { showToast('仅管理员可修改规则分组', 'error'); return; }
                showPrompt('重命名分组', '组名称', g.name, async (n) => {
                    if (!n || !n.trim() || n.trim() === g.name) return;
                    try {
                        await RuleAPI.updateGroup(gid, n.trim());
                        await loadAllData();
                        renderRulesLeftNav();
                        renderRulesRight();
                        showToast('已重命名', 'success');
                    } catch (err) { showToast(err.message || '重命名失败', 'error'); }
                });
            } else if (action === 'delete') {
                if (!isAdmin()) { showToast('仅管理员可删除规则分组', 'error'); return; }
                showConfirm('删除分组', `确定删除分组"${g.name}"及其所有规则？`, async (ok) => {
                    if (!ok) return;
                    try {
                        await RuleAPI.deleteGroup(gid);
                        APP_STATE.ruleGroupOrder = APP_STATE.ruleGroupOrder.filter(id => id !== gid);
                        saveRuleGroupOrder(APP_STATE.ruleGroupOrder);
                        if (APP_STATE.currentRuleGroupId === gid) APP_STATE.currentRuleGroupId = null;
                        await loadAllData();
                        renderRulesLeftNav();
                        renderRulesRight();
                        showToast('已删除', 'success');
                    } catch (err) { showToast(err.message || '删除失败', 'error'); }
                }, '删除');
            }
        }

        // ==================== 规则通知 - 右侧内容 ====================
        function renderRulesRight() {
            const container = document.getElementById('rulesGridContainer');
            if (!container) return;
            const groups = getSortedRuleGroups();
            const activeId = APP_STATE.currentRuleGroupId;
            const q = (document.getElementById('ruleSearchInput')?.value || '').trim().toLowerCase();
            let total = 0;
            let html = '';
            if (activeId) {
                const g = MOCK_RULE_GROUPS.find(g => g.id === activeId);
                let rules = MOCK_RULES.filter(r => r.groupId === activeId);
                if (q) rules = rules.filter(r => r.title.toLowerCase().includes(q) || r.content.replace(/<[^>]*/g, '')
                    .toLowerCase().includes(q));
                total = rules.length;
                html +=
                    `<div class="section-divider"><span class="divider-icon">📢</span> ${escapeHTML(g?.name||'')} <span class="divider-count">(${rules.length}条)</span></div>`;
                html += renderRuleCards(rules);
            } else {
                groups.forEach(g => {
                    let rules = MOCK_RULES.filter(r => r.groupId === g.id);
                    if (q) rules = rules.filter(r => r.title.toLowerCase().includes(q) || r.content.replace(
                        /<[^>]*>/g, '').toLowerCase().includes(q));
                    if (rules.length === 0 && q) return;
                    total += rules.length;
                    html +=
                        `<div class="section-divider" style="cursor:pointer;" onclick="selectRuleGroup('${g.id}', document.querySelector('#rulesLeftNav [data-rule-group-id=\"${g.id}\"]'))"><span class="divider-icon">📢</span> ${escapeHTML(g.name)} <span class="divider-count">(${rules.length}条)</span></div>`;
                    html += renderRuleCards(rules);
                });
            }
            container.innerHTML = html || '<p style="color:var(--text-secondary);text-align:center;padding:30px;">暂无规则</p>';
            document.getElementById('ruleResultCount').textContent = `共 ${total} 条规则`;
            updateAllCounts();
        }

        function renderRuleCards(rules) {
            return `<div class="file-grid" style="grid-template-columns:repeat(auto-fill,minmax(280px,1fr));">${rules.map(r=>{const canEditRule=isAdmin();const plain=r.content.replace(/<[^>]*>/g,'');const preview=plain.length>60?plain.substring(0,60)+'...':plain;const g=MOCK_RULE_GROUPS.find(g=>g.id===r.groupId);return`
                <div class="script-card" style="cursor:pointer;" onclick="previewRule('${r.id}')">
                    <div class="sc-title">${escapeHTML(r.title)}${!canEditRule?' 🔒':''}</div>
                    ${r.summary?`<div class="sc-summary">🤖 ${escapeHTML(r.summary)}</div>`:''}
                    <div class="sc-preview">${escapeHTML(preview)}</div>
                    <div class="sc-keywords">${(r.keywords||[]).map(k=>`<span class="tag keyword-tag">🔑 ${escapeHTML(k)}</span>`).join('')}</div>
                    <div class="sc-tags">${(r.tags||[]).map(t=>`<span class="tag ai-tag">${escapeHTML(t)}</span>`).join('')}${g&&!APP_STATE.currentRuleGroupId?`<span class="tag section-tag">📁 ${escapeHTML(g.name)}</span>`:''}</div>
                    <div class="sc-meta">📅 ${r.updatedAt} · 👤 ${escapeHTML(r.owner)} · 👁️ 点击查看全文</div>
                    <div class="sc-actions">
                        <button class="btn-copy" onclick="event.stopPropagation();copyRuleContent('${r.id}')">📋 复制</button>
                        ${canEditRule?`<button class="btn btn-ghost btn-xs" onclick="event.stopPropagation();notifyRuleDingtalk('${r.id}')">推送钉钉</button><button class="btn btn-ghost btn-xs" onclick="event.stopPropagation();openRuleEditor('${r.id}')">✏️ 编辑</button><button class="btn btn-ghost btn-xs" style="color:var(--danger);" onclick="event.stopPropagation();deleteRule('${r.id}')">🗑️</button>`:''}
                    </div>
                </div>`;}).join('')}</div>`;
        }

        function deleteRule(rid) { const r = MOCK_RULES.find(r => r.id === rid); if (!r) return; if (!isAdmin()) {
                showToast('无权限删除', 'error'); return; }
            showConfirm('删除规则', `确定删除"${r.title}"？`, async (ok) => {
                if (!ok) return;
                try {
                    await RuleAPI.remove(rid);
                    await loadAllData();
                    renderRulesRight();
                    renderRulesLeftNav();
                    updateAllCounts();
                    showToast('已删除', 'success');
                } catch (err) { showToast(err.message || '删除失败', 'error'); }
            }, '删除'); }

        async function notifyRuleDingtalk(rid) {
            if (!isAdmin()) { showToast('仅管理员可推送规则通知', 'error'); return; }
            const rule = MOCK_RULES.find(item => item.id === rid);
            if (!rule) return;
            try {
                await RuleAPI.notifyDingtalk(rid);
                showToast('规则通知已推送到钉钉群', 'success');
            } catch (error) {
                showToast(error.message || '钉钉推送失败', 'error');
            }
        }

        function copyRuleContent(rid) { const r = MOCK_RULES.find(r => r.id === rid); if (!r) return; const plain = r.content
                .replace(/<[^>]*>/g, '').replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g,
                    '>');
            navigator.clipboard.writeText(plain).then(() => showToast('📋 已复制', 'success')).catch(() => { const ta = document
                    .createElement('textarea');
                ta.value = plain;
                document.body.appendChild(ta);
                ta.select();
                document.execCommand('copy');
                document.body.removeChild(ta);
                showToast('📋 已复制', 'success'); }); }

        function previewRule(rid) {
            const r = MOCK_RULES.find(x => x.id === rid);
            if (!r) { console.error('previewRule: 规则不存在, rid=', rid); return; }
            const g = MOCK_RULE_GROUPS.find(x => x.id === r.groupId);
            document.getElementById('rulePreviewTitle').textContent = r.title;
            document.getElementById('rulePreviewMeta').innerHTML =
                `📁 ${escapeHTML(g?.name||'未分组')} · 👤 ${escapeHTML(r.owner)} · 📅 ${r.updatedAt||''}` +
                (r.tags && r.tags.length ? ` · 🏷️ ${escapeHTML(r.tags.join(', '))}` : '');
            const content = r.content || '';
            document.getElementById('rulePreviewContent').innerHTML = content.trim() ? content : '<p style="color:var(--text-secondary);">暂无内容</p>';
            if (r.imagePath) {
                document.getElementById('rulePreviewImage').style.display = 'block';
                document.getElementById('rulePreviewImg').src = '/uploads/' + r.imagePath;
            } else {
                document.getElementById('rulePreviewImage').style.display = 'none';
            }
            document.getElementById('rulePreviewCopyBtn').onclick = () => copyRuleContent(rid);
            document.getElementById('rulePreviewModal').style.display = 'flex';
        }

        function closeRulePreview() {
            document.getElementById('rulePreviewModal').style.display = 'none';
        }

        function openNewRuleGroupModal() { if (!isAdmin()) { showToast('仅管理员可新建规则分组', 'error'); return; } document.getElementById('newRuleGroupNameInput').value = '';
            document.getElementById('newRuleGroupModal').style.display = 'flex'; }

        function closeNewRuleGroupModal() { document.getElementById('newRuleGroupModal').style.display = 'none'; }

        async function confirmNewRuleGroup() {
            if (!isAdmin()) { showToast('仅管理员可新建规则分组', 'error'); return; }
            const n = document.getElementById('newRuleGroupNameInput').value.trim();
            if (!n) { showToast('请输入组名称', 'error'); return; }
            if (MOCK_RULE_GROUPS.some(g => g.name.toLowerCase() === n.toLowerCase())) { showToast('已存在同名分组', 'error'); return; }
            try {
                await RuleAPI.createGroup(n);
                closeNewRuleGroupModal();
                await loadAllData();
                renderRulesLeftNav();
                renderRulesRight();
                showToast(`分组"${n}"已创建`, 'success');
            } catch (err) { showToast(err.message || '创建分组失败', 'error'); }
        }

        function openRuleEditor(rid = null) { if (!isAdmin()) { showToast('仅管理员可修改规则通知', 'error'); return; } document.getElementById('ruleEditId').value = rid || '';
            document.getElementById('ruleEditorTitle').textContent = rid ? '✏️ 编辑规则' : '➕ 新建规则';
            document.getElementById('ruleGroupSelect').innerHTML = MOCK_RULE_GROUPS.map(g =>
                `<option value="${g.id}">📢 ${escapeHTML(g.name)}</option>`).join('');
            if (!MOCK_RULE_GROUPS.length) { showToast('请先创建分组', 'error'); return; }
            // 重置图片区域
            clearRuleImage();
            if (rid) { const r = MOCK_RULES.find(
                r => r.id === rid); if (r) { if (!isAdmin()) { showToast('仅管理员可编辑规则', 'error'); return; }
                document.getElementById('ruleGroupSelect').value = r.groupId;
                document.getElementById('ruleTitleInput').value = r.title;
                document.getElementById('ruleRichEditor').innerHTML = r.content;
                document.getElementById('ruleTagsInput').value = r.tags.join(',');
                if (r.imagePath) {
                    document.getElementById('ruleImagePath').value = r.imagePath;
                    document.getElementById('ruleImagePlaceholder').style.display = 'none';
                    document.getElementById('ruleImagePreview').style.display = 'block';
                    document.getElementById('ruleImagePreviewImg').src = '/uploads/' + r.imagePath;
                } } } else { document.getElementById(
                'ruleTitleInput').value = '';
            document.getElementById('ruleRichEditor').innerHTML = '';
            document.getElementById('ruleTagsInput').value = ''; if (APP_STATE.currentRuleGroupId) document
                .getElementById('ruleGroupSelect').value = APP_STATE.currentRuleGroupId; }
            document.getElementById('ruleEditorModal').style.display = 'flex';
            setTimeout(() => document.getElementById('ruleRichEditor').focus(), 200); }

        function closeRuleEditor() { document.getElementById('ruleEditorModal').style.display = 'none'; }

        // ==================== 规则图片上传 + AI OCR识别 ====================
        function clearRuleImage() {
            document.getElementById('ruleImagePath').value = '';
            document.getElementById('ruleImagePlaceholder').style.display = 'block';
            document.getElementById('ruleImagePlaceholder').innerHTML = '<div style="font-size:22px;margin-bottom:2px;">📎</div><div style="font-size:var(--font-xs);color:var(--text-secondary);">点击上传截图 或 Ctrl+V 粘贴截图</div><div style="font-size:var(--font-xxs);color:var(--text-secondary);margin-top:2px;">支持 PNG / JPG，截图将保存留存，AI自动识别文字填充正文</div>';
            document.getElementById('ruleImagePreview').style.display = 'none';
            document.getElementById('ruleImageFileInput').value = '';
        }

        function handleRuleImageFile(input) {
            const file = input.files[0];
            if (file) uploadRuleImage(file);
        }

        function handleRuleImageDrop(e) {
            e.preventDefault();
            e.currentTarget.style.borderColor = 'var(--border)';
            e.currentTarget.style.background = '';
            const file = e.dataTransfer.files[0];
            if (file && file.type.startsWith('image/')) uploadRuleImage(file);
            else showToast('请拖入图片文件', 'error');
        }

        async function uploadRuleImage(file) {
            const ph = document.getElementById('ruleImagePlaceholder');
            ph.innerHTML = '<div style="font-size:var(--font-xs);color:var(--primary);">⏳ 正在识别文字...</div>';
            try {
                let ocrText = '';
                try {
                    const ocrResult = await Tesseract.recognize(file, 'chi_sim', {
                        logger: m => console.log(m),
                    });
                    ocrText = ocrResult.data.text;
                } catch (ocrErr) {
                    console.error('Tesseract OCR failed:', ocrErr);
                }
                ph.innerHTML = '<div style="font-size:var(--font-xs);color:var(--primary);">⏳ 正在上传图片...</div>';
                const result = await RuleAPI.uploadImage(file);
                document.getElementById('ruleImagePath').value = result.imagePath;
                document.getElementById('ruleImagePlaceholder').style.display = 'none';
                document.getElementById('ruleImagePreview').style.display = 'block';
                document.getElementById('ruleImagePreviewImg').src = '/uploads/' + result.imagePath;
                if (ocrText && ocrText.trim()) {
                    const editor = document.getElementById('ruleRichEditor');
                    const html = ocrText.split('\n').filter(l => l.trim()).map(l => `<p>${escapeHTML(l)}</p>`).join('');
                    const existing = editor.innerHTML.trim();
                    editor.innerHTML = (existing && existing !== '<br>' ? existing + html : html);
                    const titleInput = document.getElementById('ruleTitleInput');
                    if (!titleInput.value.trim()) {
                        const firstLine = ocrText.split('\n').find(l => l.trim());
                        if (firstLine) titleInput.value = firstLine.trim().substring(0, 50);
                    }
                    showToast('✅ 图片上传成功，已识别文字并填充', 'success');
                } else {
                    showToast('✅ 图片上传成功（未识别到文字）', 'success');
                }
            } catch (err) {
                ph.innerHTML = '<div style="font-size:var(--font-xs);color:var(--danger);">❌ 上传失败</div><div style="font-size:var(--font-xxs);color:var(--text-secondary);margin-top:2px;">' + escapeHTML(err.message || '请重试') + '</div>';
                showToast('上传失败: ' + (err.message || ''), 'error');
            }
            document.getElementById('ruleImageFileInput').value = '';
        }

        document.addEventListener('paste', function(e) {
            const modal = document.getElementById('ruleEditorModal');
            if (!modal || modal.style.display === 'none') return;
            const items = e.clipboardData?.items;
            if (!items) return;
            for (const item of items) {
                if (item.type.startsWith('image/')) {
                    e.preventDefault();
                    const file = item.getAsFile();
                    if (file) uploadRuleImage(file);
                    return;
                }
            }
        });

        document.addEventListener('paste', function(e) {
            const modal = document.getElementById('uploadModal');
            if (!modal || modal.style.display === 'none') return;
            const items = e.clipboardData?.items;
            if (!items) return;
            for (const item of items) {
                if (item.type.startsWith('image/')) {
                    e.preventDefault();
                    const file = item.getAsFile();
                    if (file) {
                        APP_STATE.pendingUploadFiles = [file];
                        document.getElementById('uploadPreview').innerHTML = `<div>${getFileIconFromType(file.type)} ${escapeHTML(file.name || '剪贴板图片')} (${(file.size/1024).toFixed(1)} KB)</div>`;
                        showToast('已粘贴图片', 'success');
                    }
                    return;
                }
            }
        });

        async function saveRule() {
            if (!isAdmin()) { showToast('仅管理员可保存规则通知', 'error'); return; }
            const gid = document.getElementById('ruleGroupSelect').value;
            const titleInput = document.getElementById('ruleTitleInput').value.trim();
            const content = document.getElementById('ruleRichEditor').innerHTML.trim();
            const ts = document.getElementById('ruleTagsInput').value.trim();
            const imagePath = document.getElementById('ruleImagePath').value.trim();
            if (!gid) { showToast('请选择分组', 'error'); return; }
            if (!content || content === '<br>') { showToast('请输入正文', 'error'); return; }
            const tags = ts ? ts.split(/[,，]/).map(t => t.trim()).filter(t => t) : [];
            const g = MOCK_RULE_GROUPS.find(g => g.id === gid);
            const gn = g ? g.name : '未分组';
            const eid = document.getElementById('ruleEditId').value;
            try {
                if (eid) {
                    const r = MOCK_RULES.find(r => r.id === eid);
                    if (!r) { showToast('规则不存在', 'error'); return; }
                    if (!isAdmin()) { showToast('仅管理员可编辑规则', 'error'); return; }
                    const title = titleInput || r.title;
                    const result = await RuleAPI.update(eid, { title, content, groupId: gid, tags: tags.join(','), imagePath });
                    showToast(result.dingtalk_queued ? '规则已更新并推送钉钉' : '规则已更新（钉钉机器人未配置）', 'success');
                } else {
                    const ex = MOCK_RULES.filter(r => r.groupId === gid);
                    const title = titleInput || (gn + ' #' + (ex.length + 1));
                    const result = await RuleAPI.create({ title, content, groupId: gid, tags: tags.join(','), imagePath });
                    showToast(result.dingtalk_queued ? '规则已创建并推送钉钉' : '规则已创建（钉钉机器人未配置）', 'success');
                }
                closeRuleEditor();
                await loadAllData();
                renderRulesRight();
                renderRulesLeftNav();
                updateAllCounts();
            } catch (err) { showToast(err.message || '保存规则失败', 'error'); }
        }

        // ==================== 拖拽排序（左侧导航通用） ====================
        function handleLeftNavDragStart(e, id, type) {
            if (!isAdmin()) { e.preventDefault(); return; }
            APP_STATE.draggedId = id;
            APP_STATE.draggedType = type;
            e.dataTransfer.effectAllowed = 'move';
            e.target.style.opacity = '0.5';
        }

        function handleLeftNavDragOver(e) { e.preventDefault();
            e.dataTransfer.dropEffect = 'move'; const item = e.target.closest('.ln-item'); if (item) item.classList.add(
                'drag-over'); }

        function handleLeftNavDragLeave(e) { const item = e.target.closest('.ln-item'); if (item) item.classList.remove(
            'drag-over'); }

        function handleLeftNavDrop(e, targetId, type) {
            e.preventDefault();
            document.querySelectorAll('.ln-item.drag-over').forEach(el => el.classList.remove('drag-over'));
            if (!APP_STATE.draggedId || APP_STATE.draggedId === targetId || APP_STATE.draggedType !== type) return;
            if (type === 'section') {
                const sections = getSortedSections();
                const order = sections.map(s => s.id);
                const di = order.indexOf(APP_STATE.draggedId);
                const ti = order.indexOf(targetId);
                if (di < 0 || ti < 0) return;
                const [mv] = order.splice(di, 1);
                order.splice(ti, 0, mv);
                APP_STATE.sectionOrder = order;
                saveSectionOrder(order);
                renderFilesLeftNav();
                if (APP_STATE.currentSectionId === null) renderFilesRight();
                showToast('板块顺序已更新', 'success');
            } else if (type === 'group') {
                const groups = getSortedScriptGroups();
                const order = groups.map(g => g.id);
                const di = order.indexOf(APP_STATE.draggedId);
                const ti = order.indexOf(targetId);
                if (di < 0 || ti < 0) return;
                const [mv] = order.splice(di, 1);
                order.splice(ti, 0, mv);
                APP_STATE.scriptGroupOrder = order;
                saveScriptGroupOrder(order);
                renderScriptsLeftNav();
                if (APP_STATE.currentScriptGroupId === null) renderScriptsRight();
                showToast('话术组顺序已更新', 'success');
            } else if (type === 'ruleGroup') {
                const groups = getSortedRuleGroups();
                const order = groups.map(g => g.id);
                const di = order.indexOf(APP_STATE.draggedId);
                const ti = order.indexOf(targetId);
                if (di < 0 || ti < 0) return;
                const [mv] = order.splice(di, 1);
                order.splice(ti, 0, mv);
                APP_STATE.ruleGroupOrder = order;
                saveRuleGroupOrder(order);
                renderRulesLeftNav();
                if (APP_STATE.currentRuleGroupId === null) renderRulesRight();
                showToast('分组顺序已更新', 'success');
            }
            APP_STATE.draggedId = null;
            APP_STATE.draggedType = null;
        }

        function handleLeftNavDragEnd(e) {
            e.target.style.opacity = '1';
            document.querySelectorAll('.ln-item.drag-over').forEach(el => el.classList.remove('drag-over'));
            APP_STATE.draggedId = null;
            APP_STATE.draggedType = null;
        }

        // ==================== 上传 ====================
        function openUploadModal() { if (!isAdmin()) { showToast('仅管理员可上传文件', 'error'); return; } APP_STATE.pendingUploadFiles = [];
            document.getElementById('uploadPreview').innerHTML = '';
            document.getElementById('uploadInput').value = '';
            document.getElementById('uploadTitleInput').value = '';
            updateBatchMoveSelect();
            document.getElementById('uploadModal').style.display = 'flex'; }

        function closeUploadModal() { document.getElementById('uploadModal').style.display = 'none'; }

        function getFileIconFromType(type) {
            if (type.startsWith('image/')) return '🖼️';
            if (type.includes('pdf')) return '📕';
            if (type.includes('word') || type.includes('doc')) return '📘';
            if (type.includes('excel') || type.includes('xls')) return '📗';
            if (type.includes('ppt') || type.includes('powerpoint')) return '📙';
            if (type.includes('video')) return '🎬';
            if (type.includes('audio')) return '🎵';
            return '📦';
        }

        function handleFileUpload(e) { APP_STATE.pendingUploadFiles = Array.from(e.target.files);
            document.getElementById('uploadPreview').innerHTML = APP_STATE.pendingUploadFiles.map(f =>
                `<div>${getFileIconFromType(f.type)} ${escapeHTML(f.name)} (${(f.size/1024).toFixed(1)} KB)</div>`).join(''); }

        async function confirmUpload() {
            if (!isAdmin()) { showToast('仅管理员可上传文件', 'error'); return; }
            if (!APP_STATE.pendingUploadFiles.length) { showToast('请选择文件', 'error'); return; }
            const title = document.getElementById('uploadTitleInput').value.trim();
            if (!title) { showToast('请输入文件标题', 'error'); return; }
            const sid = document.getElementById('uploadSectionSelect').value || MOCK_SECTIONS[0]?.id;
            if (!sid) { showToast('请先创建板块', 'error'); return; }
            const sn = getSectionName(sid);
            let okCount = 0;
            for (const f of APP_STATE.pendingUploadFiles) {
                try {
                    const formData = new FormData();
                    formData.append('file', f);
                    formData.append('sectionId', sid);
                    formData.append('title', title);
                    formData.append('tags', '');
                    await FileAPI.upload(formData);
                    okCount++;
                } catch (err) {
                    showToast(`上传失败 ${f.name}: ${err.message}`, 'error');
                }
            }
            APP_STATE.pendingUploadFiles = [];
            closeUploadModal();
            try { await loadAllData(); } catch (e) { /* loadAllData 内部已处理错误提示 */ }
            renderFilesLeftNav();
            renderFilesRight();
            updateStorage();
            if (okCount > 0) showToast(`成功上传${okCount}个文件到"${sn}"`, 'success');
        }

        // ==================== 聊天 ====================
        function citationValue(item, key) {
            return item?.[key] || item?.metadata?.[key] || '';
        }

        function stripInlineReferences(text, hasCitations) {
            if (!hasCitations) return text || '';
            return String(text || '').replace(/\n{1,2}参考来源[:：][\s\S]*$/u, '').trim();
        }

        function renderAnswerEvidence(messageElement, citations, metrics) {
            const bubble = messageElement?.querySelector('.message-bubble');
            if (!bubble) return;
            const sources = Array.isArray(citations) ? citations : [];
            if (sources.length) {
                const group = document.createElement('div');
                group.className = 'answer-citations';
                group.setAttribute('aria-label', '参考来源');
                sources.slice(0, 5).forEach(item => {
                    const documentId = citationValue(item, 'document_id');
                    const versionId = item.version_id || citationValue(item, 'document_version_id');
                    const fileName = item.file_name || citationValue(item, 'file_name') || citationValue(item, 'source') || item.citation || '知识库资料';
                    const pageNumber = Number(item.page_number || citationValue(item, 'page_number') || 0) || null;
                    const button = document.createElement('button');
                    button.type = 'button';
                    button.className = 'answer-citation';
                    button.disabled = !documentId || !versionId;
                    button.setAttribute('aria-label', button.disabled ? `${fileName}，不可预览` : `预览 ${fileName}${pageNumber ? `第 ${pageNumber} 页` : ''}`);
                    const name = document.createElement('span');
                    name.textContent = fileName;
                    const page = document.createElement('small');
                    page.textContent = pageNumber ? `第 ${pageNumber} 页` : '页码未知';
                    button.append(name, page);
                    if (!button.disabled) button.addEventListener('click', () => previewCitationSource(documentId, versionId, fileName, pageNumber));
                    group.appendChild(button);
                });
                bubble.appendChild(group);
            }
            if (metrics) {
                const usage = metrics.token_usage || {};
                const seconds = Number(metrics.processing_time);
                const total = Number(usage.total_tokens || 0);
                const meta = document.createElement('div');
                meta.className = 'answer-metrics';
                const elapsedText = Number.isFinite(seconds) ? (seconds < 1 ? `${Math.round(seconds * 1000)} 毫秒` : `${seconds.toFixed(2)} 秒`) : '—';
                meta.textContent = `耗时 ${elapsedText} · ${usage.estimated ? '约 ' : ''}${total.toLocaleString('zh-CN')} tokens`;
                bubble.appendChild(meta);
            }
        }

        async function sendChatMessage() {
            const input = document.getElementById('chatInput');
            if (input.disabled) return;
            const q = input.value.trim();
            if (!q) return;
            appendChatMessage('user', q);
            input.value = '';
            const sendButton = document.querySelector('.chat-send-btn');
            input.disabled = true;
            if (sendButton) sendButton.disabled = true;
            const responseMessage = appendChatMessage('ai', '正在识别意图...');
            const bubble = responseMessage.querySelector('.message-bubble');
            bubble.innerHTML = '<p style="white-space:pre-wrap;"></p>';
            const paragraph = bubble.querySelector('p');
            paragraph.textContent = '正在识别意图...';
            let answer = '';
            let files = null;
            let citations = [];
            let metrics = null;
            let hasToken = false;
            let streamError = '';
            try {
                await AIAPI.stream(q, event => {
                    if (event.type === 'status' && !hasToken) {
                        paragraph.textContent = event.message || '正在处理...';
                    } else if (event.type === 'token') {
                        if (!hasToken) {
                            hasToken = true;
                            paragraph.textContent = '';
                        }
                        answer += event.content || '';
                        paragraph.textContent = answer;
                    } else if (event.type === 'files') {
                        files = (event.items || []).map(file => ({ ...file, score: 100 }));
                    } else if (event.type === 'citations') {
                        citations = event.items || [];
                    } else if (event.type === 'end') {
                        metrics = { processing_time: event.processing_time, token_usage: event.token_usage || {} };
                    } else if (event.type === 'error') {
                        streamError = event.message || '服务暂时不可用';
                        paragraph.textContent = streamError;
                    }
                    const messages = document.getElementById('chatMessages');
                    if (messages) messages.scrollTop = messages.scrollHeight;
                });

                if (streamError) return;

                const displayAnswer = stripInlineReferences(answer, citations.length > 0);
                if (files && files.length > 0) {
                    responseMessage.remove();
                    const finalMessage = appendChatMessage('ai', displayAnswer || `找到 ${files.length} 个相关文件。`, files, null, 'search');
                    renderAnswerEvidence(finalMessage, citations, metrics);
                } else {
                    paragraph.textContent = displayAnswer || '暂时没有可显示的结果。';
                    renderAnswerEvidence(responseMessage, citations, metrics);
                }
            } catch (err) {
                paragraph.textContent = '请求失败：' + (err.message || '未知错误');
            } finally {
                input.disabled = false;
                if (sendButton) sendButton.disabled = false;
                if (pendingQuickQuestion) {
                    const nextQuestion = pendingQuickQuestion;
                    pendingQuickQuestion = '';
                    input.value = nextQuestion;
                    setTimeout(() => sendChatMessage(), 80);
                } else {
                    input.focus();
                }
            }
        }

        function askFrequentQuestion(question) {
            const value = String(question || '').trim();
            if (!value) return;
            switchView('chat');
            requestAnimationFrame(() => {
                const input = document.getElementById('chatInput');
                if (!input) return;
                input.value = value;
                input.focus();
                if (input.disabled) {
                    pendingQuickQuestion = value;
                    showToast('当前回答完成后将自动提问', 'success');
                    return;
                }
                sendChatMessage();
            });
        }

        async function aiSearchDeep(q) {
            q = decodeURIComponent(q);
            const aiPh = appendChatMessage('ai', '🤔 AI 正在深入分析...');
            try {
                const result = await AIAPI.search(q);
                aiPh.remove();
                appendChatMessage('ai', result.reply || '分析完成');
            } catch (err) {
                aiPh.remove();
                appendChatMessage('ai', '⚠️ AI 分析暂时不可用：' + (err.message || '未知错误'));
            }
        }

        function quickSearch(q) { document.getElementById('chatInput').value = q;
            sendChatMessage(); }

        function searchFiles(q) { const ql = q.toLowerCase(); return MOCK_FILES.map(f => { let s = 0; if (f.name.toLowerCase()
                .includes(ql)) s += 40;
            f.keywords.forEach(k => { if (k.toLowerCase().includes(ql)) s += 25; }); if (f.aiSummary.toLowerCase().includes(
                ql)) s += 15; if (getSectionName(f.sectionId).toLowerCase().includes(ql)) s += 10; return { ...f,
                score: Math.min(s, 98) }; }).filter(f => f.score > 5).sort((a, b) => b.score - a.score).slice(0, 5); }

        function searchScripts(q) { const ql = q.replace(/话术/g, '').toLowerCase().trim(); return MOCK_SCRIPTS.map(s => { let sc =
                0; const plain = s.content.replace(/<[^>]*>/g, ''); if (s.title.toLowerCase().includes(ql)) sc += 40; if (
                plain.toLowerCase().includes(ql)) sc += 25;
            (s.keywords || []).forEach(k => { if (k.toLowerCase().includes(ql)) sc += 25; }); const g = MOCK_SCRIPT_GROUPS
                .find(g => g.id === s.groupId); if (g && g.name.toLowerCase().includes(ql)) sc += 15; return { ...s,
                score: Math.min(sc, 100), groupName: g?.name || '未知组' }; }).filter(s => s.score >= 35).sort((a, b) => b
            .score - a.score).slice(0, 3); }

        function appendChatMessage(role, text, fr = null, sr = null, st = null) { const c = document.getElementById(
                'chatMessages'); const md = document.createElement('div');
            md.className = `message ${role}`; const ae = role === 'user' ? '👤' : '🤖'; let bc = ''; if (role === 'user') { bc =
                    `<p>${escapeHTML(text)}</p>`; } else if (st === 'scripts') { if (sr && sr.length > 0) { bc =
                        `<span class="result-source-label scripts">💬 话术匹配结果</span><p style="font-size:12px;margin-bottom:4px;">💬 为您找到 <strong>${sr.length}</strong> 条相关话术（按匹配度排序）：</p>`;
                    sr.forEach((s, i) => { const plain = s.content.replace(/<[^>]*>/g, ''); const preview = plain.length >
                        60 ? plain.substring(0, 60) + '...' : plain;
                        bc +=
                            `<div class="script-result-block"><div class="sr-header">【${i+1}】${escapeHTML(s.groupName)} | 匹配度：${Math.round(s.score)}%</div><div class="sr-body">${escapeHTML(preview)}</div><button class="btn-copy" onclick="copyScriptContent('${s.id}')">📋 复制本条</button></div>`; });
                    bc +=
                        '<p style="font-size:10px;color:var(--text-secondary);margin-top:4px;">📋 点击按钮可一键复制</p>'; } else { bc =
                        '<span class="result-source-label scripts">💬 话术匹配结果</span><p>😕 未找到匹配度足够高的话术，试试更精准的关键词吧。</p>'; } } else if (
                st === 'files') { if (fr && fr.length > 0) { bc =
                        `<span class="result-source-label files">📂 文件匹配结果</span><div style="display:flex;flex-direction:column;gap:5px;">${fr.map(f=>{const isImage=['image','img'].includes(f.iconType);const iconHtml=isImage?`<img src="/api/files/${f.id}/view" style="width:32px;height:32px;border-radius:6px;object-fit:cover;" alt="${escapeHTML(f.name)}" />`:`<span>${getFileTypeIcon(f.iconType)}</span>`;return`<div style="display:flex;align-items:center;gap:8px;padding:8px 10px;background:#fafbfc;border-radius:8px;border:1px solid var(--border);cursor:pointer;" onclick="previewFile('${f.id}')">${iconHtml}<span style="flex:1;font-size:12px;font-weight:600;">${escapeHTML(f.name)}</span><span style="font-size:10px;color:var(--text-secondary);">${f.size}</span><span style="font-size:10px;background:#e8f8ed;color:#34c759;padding:2px 6px;border-radius:8px;">${Math.round(f.score)}%</span></div>`;}).join('')}</div>`; } else { bc =
                        '<span class="result-source-label files">📂 文件匹配结果</span><p>😕 未找到相关文件。</p>'; } } else if (st === 'search') {
                    bc = `<p>${escapeHTML(text || '')}</p>`;
                    if (fr && fr.length > 0) {
                        bc += `<span class="result-source-label files">📂 相关文件（${fr.length}）</span><div style="display:flex;flex-direction:column;gap:5px;">`;
                        fr.forEach(f => {
                            const isImage = ['image', 'img'].includes(f.iconType);
                            const iconHtml = isImage ? `<img src="/api/files/${f.id}/view" style="width:32px;height:32px;border-radius:6px;object-fit:cover;" alt="${escapeHTML(f.name)}" />` : getFileTypeIcon(f.iconType);
                            bc += `<div style="display:flex;align-items:center;gap:8px;padding:8px 10px;background:#fafbfc;border-radius:8px;border:1px solid var(--border);cursor:pointer;" onclick="previewFile('${f.id}')"><span>${iconHtml}</span><span style="flex:1;font-size:12px;font-weight:600;">${escapeHTML(f.name)}</span><span style="font-size:10px;color:var(--text-secondary);">${f.size}</span></div>`;
                        });
                        bc += '</div>';
                    }
                    if (sr && sr.length > 0) {
                        bc += `<span class="result-source-label scripts">💬 相关话术（${sr.length}）</span>`;
                        sr.forEach((s, i) => {
                            const plain = (s.content || '').replace(/<[^>]*>/g, '');
                            const preview = plain.length > 60 ? plain.substring(0, 60) + '...' : plain;
                            const tagHtml = s.tags ? ` <span style="font-size:10px;background:#e8f4fd;color:#1890ff;padding:1px 6px;border-radius:4px;margin-left:4px;">🏷️ ${escapeHTML(s.tags)}</span>` : '';
                            bc += `<div class="script-result-block"><div class="sr-header">【${i+1}】${escapeHTML(s.title||'')}${tagHtml}</div><div class="sr-body">${escapeHTML(preview)}</div><button class="btn-copy" onclick="copyScriptContent('${s.id}')">📋 复制本条</button></div>`;
                        });
                    }
                    if ((!fr || fr.length === 0) && (!sr || sr.length === 0)) {
                        bc += '<p style="font-size:11px;color:var(--text-secondary);">未找到相关文件或话术。</p>';
                    }
                } else { bc = text ? `<p>${escapeHTML(text)}</p>` : '<p>😕 未找到匹配结果。</p>'; }
            md.innerHTML =
                `<div class="message-avatar">${ae}</div><div class="message-bubble">${bc}</div>`;
            c.appendChild(md);
            c.scrollTop = c.scrollHeight;
            return md;
        }

        // ==================== 关键词云 ====================
        const KW_DATA = ['项目规划', '产品需求', '技术架构', '用户调研', '数据分析', 'AI模型', '部署方案', '设计规范', '测试报告', '机器学习', '接口文档',
            '安全审计', '教辅资源', '课程设计', '销售话术'
        ];
        const KW_COLORS = ['kw-color-0', 'kw-color-1', 'kw-color-2', 'kw-color-3', 'kw-color-4', 'kw-color-5', 'kw-color-6',
            'kw-color-7'
        ];

        function renderIngestionJobs() {
            const container = document.getElementById('ingestionJobList');
            if (!container) return;
            const jobs = latestIngestionJobs();
            if (!jobs.length) {
                container.innerHTML = '<div class="ingestion-job-empty">暂无入库任务。上传文件后可在这里查看解析与向量化进度。</div>';
                return;
            }
            const retryable = new Set(['failed', 'needs_review', 'interrupted', 'published']);
            container.innerHTML = jobs.map(job => {
                const progress = Math.max(0, Math.min(100, Number(job.progress || 0)));
                const indeterminate = isIndeterminateJob(job);
                const canRetry = isAdmin() && retryable.has(job.status);
                const actionLabel = job.status === 'published' ? '重新解析' : '重试';
                const error = job.error_message
                    ? `<p class="ingestion-job-error" role="alert">${escapeHTML(job.error_message)}</p>`
                    : '';
                return `<article class="ingestion-job-row" data-status="${escapeHTML(job.status || '')}">
                    <div class="ingestion-job-file"><strong title="${escapeHTML(job.file_name || '')}">${escapeHTML(job.file_name || '未知文件')}</strong><small>${escapeHTML(job.parser || '等待识别解析器')} · ${escapeHTML(job.uploader || '')} · ${escapeHTML(formatFileSize(job.size_bytes))}</small>${error}</div>
                    <div class="ingestion-job-state"><span>${escapeHTML(ingestionStatusLabel(job.status))}</span><strong>${indeterminate ? '实时处理中' : `${progress}%`}</strong></div>
                    <div class="ingestion-job-progress${indeterminate ? ' indeterminate' : ''}" role="progressbar" aria-label="${escapeHTML(job.file_name || '文件')} ${escapeHTML(ingestionStatusLabel(job.status))}" aria-valuemin="0" aria-valuemax="100"${indeterminate ? '' : ` aria-valuenow="${progress}"`}><i style="${indeterminate ? '' : `transform:scaleX(${progress / 100})`}"></i></div>
                    <button type="button" class="btn btn-outline btn-xs" ${canRetry ? '' : 'disabled'} onclick="retryIngestionJob('${escapeHTML(job.id)}')">${canRetry ? actionLabel : (retryable.has(job.status) ? '仅管理员' : '处理中')}</button>
                </article>`;
            }).join('');
        }

        async function refreshIngestionJobs() {
            const container = document.getElementById('ingestionJobList');
            if (container) container.setAttribute('aria-busy', 'true');
            try {
                const result = await JobAPI.list();
                MOCK_INGESTION_JOBS = result.jobs || [];
                renderIngestionJobs();
                renderFilesLeftNav();
                if (APP_STATE.currentView === 'files') renderFilesRight();
                updateAllCounts();
            } catch (error) {
                if (container) container.innerHTML = `<div class="ingestion-job-error" role="alert">任务加载失败：${escapeHTML(error.message || '未知错误')}。请稍后重试。</div>`;
            } finally {
                container?.removeAttribute('aria-busy');
            }
        }

        function retryIngestionJob(jobId) {
            if (!isAdmin()) { showToast('仅管理员可重新解析文件', 'error'); return; }
            const job = latestIngestionJobs().find(item => item.id === jobId);
            if (!job) return;
            showConfirm('重新解析文件', `确定重新解析“${job.file_name || '该文件'}”吗？系统会重新提取内容、切块并生成向量。`, async ok => {
                if (!ok) return;
                try {
                    await JobAPI.retry(jobId);
                    showToast('任务已重新排队', 'success');
                    await refreshIngestionJobs();
                } catch (error) {
                    showToast(error.message || '重新解析失败', 'error');
                }
            }, '重新解析');
        }

        async function renderKeywordCloud() {
            const cloud = document.getElementById('keywordCloud');
            if (!cloud) return;
            cloud.innerHTML = '<span style="color:var(--text-secondary);font-size:12px;">加载中...</span>';
            try {
                const result = await AIAPI.keywordCloud();
                const items = result.cloud || [];
                const countElement = document.getElementById('statQuestionCount');
                if (countElement) countElement.textContent = result.totalQuestions || 0;
                if (items.length === 0) {
                    cloud.innerHTML = '<span style="color:var(--text-secondary);font-size:12px;">暂无智能检索问题数据</span>';
                    return;
                }
                cloud.innerHTML = items.map((item, i) => {
                    const k = typeof item === 'string' ? item : (item.word || item.name || '');
                    const w = typeof item === 'object' ? (item.weight || 1) : 1;
                    const sz = w >= 7 ? 'sz-lg' : w >= 4 ? 'sz-md' : 'sz-sm';
                    const cl = KW_COLORS[i % KW_COLORS.length];
                    const encodedQuestion = encodeURIComponent(k).replace(/'/g, '%27');
                    return `<button type="button" class="keyword-tag question-cloud-item ${sz} ${cl}" data-question="${encodedQuestion}" aria-label="快速提问：${escapeHTML(k)}，出现 ${w} 次"><span>${escapeHTML(k)}</span><small>${w}次</small></button>`;
                }).join('');
                cloud.querySelectorAll('.question-cloud-item').forEach(button => {
                    button.addEventListener('click', () => askFrequentQuestion(decodeURIComponent(button.dataset.question || '')));
                });
            } catch (err) {
                cloud.innerHTML = '<span style="color:var(--danger);font-size:12px;">关键词云加载失败</span>';
            }
        }

        // ==================== FAQ库（管理员） ====================
        function faqCategories() {
            return [...new Set(MOCK_FAQS.map(item => item.category || '通用'))].sort((a, b) => a.localeCompare(b, 'zh-CN'));
        }

        function syncFaqCategoryOptions() {
            const categories = faqCategories();
            const filter = document.getElementById('faqCategoryFilter');
            if (filter) {
                const selected = filter.value;
                filter.innerHTML = '<option value="">全部分类</option>' + categories.map(category =>
                    `<option value="${escapeHTML(category)}">${escapeHTML(category)}</option>`
                ).join('');
                filter.value = categories.includes(selected) ? selected : '';
            }
            const datalist = document.getElementById('faqCategoryOptions');
            if (datalist) datalist.innerHTML = categories.map(category => `<option value="${escapeHTML(category)}"></option>`).join('');
        }

        function renderFaqs() {
            const container = document.getElementById('faqGrid');
            if (!container) return;
            if (!isAdmin()) {
                container.innerHTML = '<div class="faq-empty">FAQ库仅管理员可访问。</div>';
                return;
            }
            syncFaqCategoryOptions();
            const keyword = (document.getElementById('faqSearchInput')?.value || '').trim().toLowerCase();
            const category = document.getElementById('faqCategoryFilter')?.value || '';
            const items = MOCK_FAQS.filter(item => {
                if (category && item.category !== category) return false;
                if (!keyword) return true;
                return [item.question, item.answer, item.category, item.tags].some(value => String(value || '').toLowerCase().includes(keyword));
            });
            document.getElementById('faqResultCount').textContent = `共 ${items.length} 条 FAQ`;
            if (!items.length) {
                container.innerHTML = `<div class="faq-empty">${keyword || category ? '没有匹配的 FAQ，请调整搜索条件。' : '暂无 FAQ，点击“新建 FAQ”录入第一条标准问答。'}</div>`;
                return;
            }
            container.innerHTML = items.map(item => {
                const tags = String(item.tags || '').split(/[,，]/).map(tag => tag.trim()).filter(Boolean);
                return `<article class="faq-card">
                    <div class="faq-card-head"><span class="faq-category">${escapeHTML(item.category || '通用')}</span><span class="faq-index-state" data-indexed="${item.indexed ? 'true' : 'false'}">${item.indexed ? '已索引' : '等待索引'}</span></div>
                    <h3>${escapeHTML(item.question)}</h3>
                    <p>${escapeHTML(item.answer)}</p>
                    <div class="faq-tags">${tags.map(tag => `<span>${escapeHTML(tag)}</span>`).join('')}</div>
                    <footer><small>${escapeHTML(item.owner || '')} · ${escapeHTML(item.updatedAt || '')}</small><div><button type="button" class="btn btn-ghost btn-xs" onclick="openFaqEditor('${item.id}')">编辑</button><button type="button" class="btn btn-ghost btn-xs" style="color:var(--danger);" onclick="deleteFaq('${item.id}')">删除</button></div></footer>
                </article>`;
            }).join('');
        }

        async function reloadFaqs() {
            if (!isAdmin()) return;
            const result = await FAQAPI.list();
            MOCK_FAQS = result.faqs || [];
            renderFaqs();
            updateAllCounts();
        }

        function openFaqEditor(itemId = '') {
            if (!isAdmin()) { showToast('仅管理员可维护 FAQ', 'error'); return; }
            const item = MOCK_FAQS.find(value => value.id === itemId);
            syncFaqCategoryOptions();
            document.getElementById('faqEditorTitle').textContent = item ? '编辑 FAQ' : '新建 FAQ';
            document.getElementById('faqEditId').value = item?.id || '';
            document.getElementById('faqQuestionInput').value = item?.question || '';
            document.getElementById('faqAnswerInput').value = item?.answer || '';
            document.getElementById('faqCategoryInput').value = item?.category || '通用';
            document.getElementById('faqTagsInput').value = item?.tags || '';
            document.getElementById('faqEditorError').textContent = '';
            document.getElementById('faqEditorModal').style.display = 'flex';
            setTimeout(() => document.getElementById('faqQuestionInput').focus(), 100);
        }

        function closeFaqEditor() {
            document.getElementById('faqEditorModal').style.display = 'none';
            document.getElementById('faqEditorForm').reset();
            document.getElementById('faqEditId').value = '';
            document.getElementById('faqEditorError').textContent = '';
        }

        function validateFaqEditor() {
            const question = document.getElementById('faqQuestionInput')?.value.trim() || '';
            const answer = document.getElementById('faqAnswerInput')?.value.trim() || '';
            let message = '';
            if (question && question.length < 2) message = '标准问题至少需要 2 个字符。';
            else if (question && !answer) message = '请填写标准答案。';
            document.getElementById('faqEditorError').textContent = message;
            return !message;
        }

        async function saveFaq(event) {
            event?.preventDefault();
            if (!isAdmin()) { showToast('仅管理员可维护 FAQ', 'error'); return; }
            if (!validateFaqEditor()) return;
            const itemId = document.getElementById('faqEditId').value;
            const payload = {
                question: document.getElementById('faqQuestionInput').value.trim(),
                answer: document.getElementById('faqAnswerInput').value.trim(),
                category: document.getElementById('faqCategoryInput').value.trim() || '通用',
                tags: document.getElementById('faqTagsInput').value.trim(),
            };
            if (!payload.question || !payload.answer) {
                document.getElementById('faqEditorError').textContent = '标准问题和标准答案不能为空。';
                return;
            }
            const button = document.getElementById('faqSaveButton');
            button.disabled = true;
            button.textContent = '保存中…';
            try {
                if (itemId) await FAQAPI.update(itemId, payload);
                else await FAQAPI.create(payload);
                closeFaqEditor();
                await reloadFaqs();
                showToast(itemId ? 'FAQ 已更新并重新索引' : 'FAQ 已创建并开始索引', 'success');
            } catch (error) {
                document.getElementById('faqEditorError').textContent = error.message || 'FAQ 保存失败';
            } finally {
                button.disabled = false;
                button.textContent = '保存并入库';
            }
        }

        function deleteFaq(itemId) {
            if (!isAdmin()) { showToast('仅管理员可维护 FAQ', 'error'); return; }
            const item = MOCK_FAQS.find(value => value.id === itemId);
            if (!item) return;
            showConfirm('删除 FAQ', `确定删除“${item.question}”吗？删除后将同步移除 FAQ 向量。`, async confirmed => {
                if (!confirmed) return;
                try {
                    await FAQAPI.remove(itemId);
                    await reloadFaqs();
                    showToast('FAQ 已删除', 'success');
                } catch (error) {
                    showToast(error.message || 'FAQ 删除失败', 'error');
                }
            }, '删除');
        }

        // ==================== 日志 ====================
        function getFilteredLogs() { let logs = [...MOCK_LOGS]; const tf = document.getElementById('logTypeFilter')?.value || ''; const
            os = (document.getElementById('logOperatorSearch')?.value || '').trim().toLowerCase(); if (tf) logs = logs.filter(
                l => l.actionType === tf); if (os) logs = logs.filter(l => l.operator.toLowerCase().includes(os)); return logs; }

        function renderLogs(retried) {
            if (MOCK_LOGS.length === 0 && !retried) { loadLogs().then(() => renderLogs(true)); return; }
            const logs = getFilteredLogs();
            document.getElementById('logTotalCount').textContent = `共 ${logs.length} 条`; const tp = Math.ceil(logs.length / APP_STATE
                .logPageSize); if (APP_STATE.logPage >= tp) APP_STATE.logPage = Math.max(0, tp - 1); const s = APP_STATE
                .logPage * APP_STATE.logPageSize; const pl = logs.slice(s, s + APP_STATE.logPageSize); const bc = { '上传': 'upload',
                '删除': 'delete', '重命名': 'rename', '移动': 'move', '新建板块': 'create', '新建话术组': 'create', '新建话术': 'create',
                '编辑话术': 'edit' };
            document.getElementById('logTableBody').innerHTML = pl.map(l => `
                <tr><td>${l.timestamp}</td><td>${escapeHTML(l.operator)}</td><td><span class="log-badge ${bc[l.actionType]||'create'}">${l.actionType}</span></td><td>${l.targetType}</td><td>${escapeHTML(l.targetName)}</td><td style="font-size:10px;color:var(--text-secondary);">${escapeHTML(l.targetPath)}</td></tr>`)
                .join(''); let pg = ''; if (tp > 1) { pg +=
                    `<button class="btn btn-ghost btn-xs" onclick="APP_STATE.logPage=0;renderLogs();" ${APP_STATE.logPage===0?'disabled':''}>首页</button>`;
                pg +=
                    `<button class="btn btn-ghost btn-xs" onclick="APP_STATE.logPage=Math.max(0,APP_STATE.logPage-1);renderLogs();" ${APP_STATE.logPage===0?'disabled':''}>上一页</button>`;
                pg +=
                    `<span>第 ${APP_STATE.logPage+1}/${tp} 页</span>`;
                pg +=
                    `<button class="btn btn-ghost btn-xs" onclick="APP_STATE.logPage=Math.min(tp-1,APP_STATE.logPage+1);renderLogs();" ${APP_STATE.logPage>=tp-1?'disabled':''}>下一页</button>`;
                pg +=
                    `<button class="btn btn-ghost btn-xs" onclick="APP_STATE.logPage=tp-1;renderLogs();" ${APP_STATE.logPage>=tp-1?'disabled':''}>末页</button>`; }
            document.getElementById('logPagination').innerHTML = pg; }

        function clearAllLogs() { if (!isAdmin()) { showToast('仅管理员可清空日志', 'error'); return; }
            showConfirm('清空日志', '确定清空所有操作日志？', async (ok) => {
                if (!ok) return;
                try {
                    await LogAPI.clear();
                    APP_STATE.logPage = 0;
                    await loadLogs();
                    renderLogs();
                    showToast('日志已清空', 'success');
                } catch (err) { showToast(err.message || '清空失败', 'error'); }
            }, '清空'); }

        // ==================== 全局更新 ====================
        function updateAllCounts() { document.getElementById('fileCountBadge').textContent = MOCK_FILES.length + pendingOnlyJobs().length;
            document.getElementById('scriptCountBadge').textContent = MOCK_SCRIPTS.length;
            document.getElementById('ruleCountBadge').textContent = MOCK_RULES.length;
            const faqBadge = document.getElementById('faqCountBadge');
            if (faqBadge) faqBadge.textContent = MOCK_FAQS.length;
            document.getElementById('statFileCount').textContent = MOCK_FILES.length + pendingOnlyJobs().length;
            document.getElementById('statSectionCount').textContent = MOCK_SECTIONS.length; }

        /* 用后端返回的真实字节数统计存储占用，不再解析 "12.3 MB" 这类展示字符串
         * （旧实现遇到 B 单位会误按 MB 计算，导致占用虚高）。 */
        function fileSizeBytes(file) {
            const explicit = Number(file.sizeBytes);
            if (isFinite(explicit) && explicit > 0) return explicit;
            const text = String(file.size || '');
            const value = parseFloat(text);
            if (!isFinite(value)) return 0;
            if (text.includes('GB')) return value * 1024 * 1024 * 1024;
            if (text.includes('MB')) return value * 1024 * 1024;
            return value * 1024;
        }

        function updateStorage() {
            const totalMb = MOCK_FILES.reduce((sum, f) => sum + fileSizeBytes(f), 0) / (1024 * 1024);
            document.getElementById('storageUsed').textContent = `${totalMb.toFixed(1)} MB / 10 GB`;
            document.getElementById('storageBarFill').style.width = Math.min(totalMb / 10240 * 100, 100) + '%'; }

        function showToast(msg, type = 'success') { const c = document.getElementById('toastContainer'); const t = document
                .createElement('div');
            t.className = `toast ${type}`;
            t.innerHTML = `${type==='success'?'✅':'⚠️'} ${msg}`;
            c.appendChild(t);
            setTimeout(() => { t.style.opacity = '0';
                t.style.transform = 'translateX(50px)';
                t.style.transition = '0.3s ease';
                setTimeout(() => t.remove(), 300); }, 2400); }

        function closeAllModals() { ['uploadModal', 'previewModal', 'renameModal', 'moveModal', 'newSectionModal',
                'newGroupModal', 'scriptEditorModal', 'newRuleGroupModal', 'ruleEditorModal', 'rulePreviewModal',
                'confirmModal', 'promptModal', 'passwordModal'
            ].forEach(id => { const el = document.getElementById(id); if (el) el.style.display = 'none'; });
            ['contextMenu', 'sectionCtxMenu', 'groupCtxMenu', 'ruleGroupCtxMenu'].forEach(id => { const m = document.getElementById(id); if (m) m
                    .classList.remove('show'); }); }

        // ==================== 通用确认/输入模态框 ====================
        let _confirmCb = null, _promptCb = null;
        function showConfirm(title, message, cb, okText = '确认') {
            _confirmCb = cb;
            document.getElementById('confirmTitle').textContent = title;
            document.getElementById('confirmMessage').textContent = message;
            const ok = document.getElementById('confirmOkBtn');
            ok.textContent = okText;
            ok.onclick = () => { closeConfirmModal(); if (_confirmCb) { const c = _confirmCb; _confirmCb = null; c(true); } };
            document.getElementById('confirmModal').style.display = 'flex';
        }
        function closeConfirmModal() { document.getElementById('confirmModal').style.display = 'none'; }
        function showPrompt(title, label, defaultValue, cb, okText = '确认') {
            _promptCb = cb;
            document.getElementById('promptTitle').textContent = title;
            document.getElementById('promptLabel').textContent = label;
            const inp = document.getElementById('promptInput');
            inp.value = defaultValue || '';
            const ok = document.getElementById('promptOkBtn');
            ok.textContent = okText;
            ok.onclick = () => { const v = inp.value.trim(); closePromptModal(); if (_promptCb) { const c = _promptCb; _promptCb = null; c(v); } };
            document.getElementById('promptModal').style.display = 'flex';
            setTimeout(() => inp.focus(), 100);
        }
        function closePromptModal() { document.getElementById('promptModal').style.display = 'none'; }

        // ==================== 钉钉机器人设置 ====================
        function setDingtalkSettingsStatus(message, type = '') {
            const element = document.getElementById('dingtalkSettingsStatus');
            if (!element) return;
            element.textContent = message || '';
            element.className = `dingtalk-settings-status${type ? ` ${type}` : ''}`;
        }

        async function loadDingtalkSettings() {
            if (!isAdmin()) return;
            setDingtalkSettingsStatus('正在读取配置…');
            try {
                const config = await SettingsAPI.dingtalk();
                document.getElementById('dingtalkEnabled').checked = Boolean(config.enabled);
                document.getElementById('dingtalkWebhook').value = '';
                document.getElementById('dingtalkSecret').value = '';
                document.getElementById('dingtalkWebhookHint').textContent = config.configured
                    ? `已配置：${config.webhook_masked}` : '尚未配置 Webhook';
                document.getElementById('dingtalkSecretHint').textContent = config.secret_configured
                    ? '加签密钥已配置，留空将保持不变' : '尚未配置加签密钥';
                document.getElementById('dingtalkTestButton').disabled = !(config.enabled && config.configured);
                setDingtalkSettingsStatus(config.enabled && config.configured ? '机器人已启用' : '机器人未启用或配置不完整');
            } catch (error) {
                setDingtalkSettingsStatus(error.message || '配置读取失败', 'error');
            }
        }

        function validateDingtalkWebhook() {
            const value = document.getElementById('dingtalkWebhook')?.value.trim() || '';
            if (!value) return true;
            try {
                const url = new URL(value);
                if (url.protocol !== 'https:' || !url.hostname.endsWith('dingtalk.com') || !url.searchParams.get('access_token')) {
                    throw new Error('invalid');
                }
                setDingtalkSettingsStatus('Webhook 格式正确');
                return true;
            } catch {
                setDingtalkSettingsStatus('请输入包含 access_token 的钉钉官方 HTTPS Webhook', 'error');
                return false;
            }
        }

        async function saveDingtalkSettings(event) {
            event?.preventDefault();
            if (!isAdmin()) { showToast('仅管理员可修改系统设置', 'error'); return; }
            if (!validateDingtalkWebhook()) return;
            const button = document.getElementById('dingtalkSaveButton');
            button.disabled = true;
            setDingtalkSettingsStatus('正在保存…');
            try {
                const result = await SettingsAPI.saveDingtalk({
                    enabled: document.getElementById('dingtalkEnabled').checked,
                    webhook: document.getElementById('dingtalkWebhook').value.trim(),
                    secret: document.getElementById('dingtalkSecret').value.trim(),
                });
                setDingtalkSettingsStatus(result.message || '配置已保存', 'success');
                showToast('钉钉机器人配置已保存', 'success');
                await loadDingtalkSettings();
            } catch (error) {
                setDingtalkSettingsStatus(error.message || '配置保存失败', 'error');
            } finally {
                button.disabled = false;
            }
        }

        async function testDingtalkSettings() {
            if (!isAdmin()) { showToast('仅管理员可测试机器人', 'error'); return; }
            const button = document.getElementById('dingtalkTestButton');
            button.disabled = true;
            setDingtalkSettingsStatus('正在发送测试消息…');
            try {
                const result = await SettingsAPI.testDingtalk();
                setDingtalkSettingsStatus(result.message || '测试消息已发送', 'success');
                showToast('钉钉测试消息发送成功', 'success');
            } catch (error) {
                setDingtalkSettingsStatus(error.message || '测试消息发送失败', 'error');
            } finally {
                button.disabled = false;
            }
        }

        // ==================== 修改密码 ====================
        function openPasswordModal() {
            document.getElementById('oldPasswordInput').value = '';
            document.getElementById('newPasswordInput').value = '';
            document.getElementById('confirmNewPasswordInput').value = '';
            document.getElementById('passwordModal').style.display = 'flex';
            setTimeout(() => document.getElementById('oldPasswordInput').focus(), 100);
        }
        function closePasswordModal() {
            document.getElementById('passwordModal').style.display = 'none';
            document.getElementById('oldPasswordInput').value = '';
            document.getElementById('newPasswordInput').value = '';
            document.getElementById('confirmNewPasswordInput').value = '';
        }
        async function confirmChangePassword() {
            const oldP = document.getElementById('oldPasswordInput').value;
            const newP = document.getElementById('newPasswordInput').value;
            const confirmP = document.getElementById('confirmNewPasswordInput').value;
            if (!oldP) { showToast('请输入原密码', 'error'); return; }
            if (newP.length < 4) { showToast('新密码至少4位', 'error'); return; }
            if (newP !== confirmP) { showToast('两次新密码不一致', 'error'); return; }
            try {
                await AuthAPI.changePassword(oldP, newP);
                closePasswordModal();
                showToast('密码修改成功', 'success');
            } catch (err) { showToast(err.message || '修改密码失败', 'error'); }
        }

        // ==================== 知识库成员 ====================
        function renderMembers(retried) {
            const body = document.getElementById('memberTableBody');
            if (!body) return;
            if (MOCK_USERS.length === 0 && !retried) { loadMembers().then(() => renderMembers(true)); return; }
            const q = (document.getElementById('memberSearchInput')?.value || '').trim().toLowerCase();
            let users = [...MOCK_USERS];
            if (q) users = users.filter(u => u.username.toLowerCase().includes(q));
            const adminCount = MOCK_USERS.filter(u => u.role === 'admin').length;
            document.getElementById('memberTotal').textContent = `（共 ${MOCK_USERS.length} 人，管理员 ${adminCount} 人）`;
            body.innerHTML = users.map(u => {
                const isMe = u.username === APP_STATE.userName;
                const isDefaultAdmin = u.username === 'Admin';
                const roleBadge = u.role === 'admin'
                    ? '<span class="role-badge admin">👑 管理员</span>'
                    : '<span class="role-badge user">👤 普通用户</span>';
                let actions = '<span style="color:var(--text-secondary);font-size:11px;">—</span>';
                if (isAdmin() && !isMe) {
                    if (u.role === 'admin') {
                        if (!isDefaultAdmin) actions = `<button class="btn btn-ghost btn-xs" onclick="setMemberRole('${escapeHTML(u.username)}','user')">设为普通用户</button>`;
                        else actions = '<span style="color:var(--text-secondary);font-size:11px;">系统管理员</span>';
                    } else {
                        actions = `<button class="btn btn-outline btn-xs" onclick="setMemberRole('${escapeHTML(u.username)}','admin')">设为管理员</button>`;
                    }
                }
                return `<tr>
                    <td><strong>${escapeHTML(u.username)}</strong>${isMe?' <span style="font-size:10px;color:var(--accent);">（我）</span>':''}</td>
                    <td>${roleBadge}</td>
                    <td style="font-size:11px;color:var(--text-secondary);">${escapeHTML(u.createdAt || '-')}</td>
                    <td style="text-align:right;">${actions}</td>
                </tr>`;
            }).join('');
        }
        function setMemberRole(username, role) {
            if (!isAdmin()) { showToast('仅管理员可设置角色', 'error'); return; }
            const user = getUserByName(username);
            if (!user) return;
            const label = role === 'admin' ? '管理员' : '普通用户';
            showConfirm('确认设置', `确定将"${username}"设为${label}？`, async (ok) => {
                if (!ok) return;
                try {
                    await MemberAPI.setRole(username, role);
                    await loadMembers();
                    renderMembers();
                    showToast(`已将"${username}"设为${label}`, 'success');
                } catch (err) { showToast(err.message || '设置角色失败', 'error'); }
            }, '确认设置');
        }

        // ==================== 初始化 ====================
        function setupAuthTabs() { document.querySelectorAll('.auth-tab').forEach(tab => { tab.addEventListener('click', () =>
                { document.querySelectorAll('.auth-tab').forEach(t => t.classList.remove('active'));
                    tab.classList.add('active'); const il = tab.dataset.tab === 'login';
                    document.getElementById('authTitle').textContent = il ? '欢迎使用教辅知识库' : '注册教辅知识库账户';
                    document.getElementById('authSubmitBtn').textContent = il ? '登 录' : '注 册';
                    document.getElementById('authPassword').autocomplete = il ? 'current-password' : 'new-password';
                    document.getElementById('confirmGroup').style.display = il ? 'none' : 'block';
                    document.getElementById('authForm').dataset.mode = il ? 'login' : 'register'; }); });
            document.getElementById('authForm').dataset.mode = 'login'; }

        function setupGlobalListeners() { document.addEventListener('click', (e) => { ['contextMenu', 'sectionCtxMenu',
                'groupCtxMenu', 'ruleGroupCtxMenu'].forEach(id => { const m = document.getElementById(id); if (m && !m.contains(e
                        .target)) m.classList.remove('show'); }); });
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') { const el = document.activeElement; if (el && el.getAttribute('role') === 'button' && el.tagName !== 'BUTTON') { e.preventDefault(); el.click(); } }
                if (e.key === 'Escape') { ['contextMenu', 'sectionCtxMenu',
                    'groupCtxMenu', 'ruleGroupCtxMenu'].forEach(id => { const m = document.getElementById(id); if (m) m.classList
                        .remove('show'); });
                closeAllModals(); } }); }

        function init() { setupAuthTabs();
            setupGlobalListeners(); const so = loadSectionOrder(); if (so && so.length > 0) APP_STATE.sectionOrder = so; const
                sgo = loadScriptGroupOrder(); if (sgo && sgo.length > 0) APP_STATE.scriptGroupOrder = sgo; const
                rgo = loadRuleGroupOrder(); if (rgo && rgo.length > 0) APP_STATE.ruleGroupOrder = rgo;
            // authOverlay 默认可见；autoLogin 成功时会通过 loginSuccess 隐藏它
            autoLogin();
            updateStorage();
            updateAllCounts();
            renderKeywordCloud(); }
        init();
