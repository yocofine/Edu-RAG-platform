/* ============================================================================
 * 教辅知识库前端复刻版 —— 接口适配层
 * ============================================================================
 * 原页面的 api.js 面向一个 Express+SQLite 后端（/api/sections、/api/files…），
 * 且使用 `Authorization: Bearer <token>`。本项目后端是 FastAPI（/api/v1/*），
 * 使用 Cookie 会话 + CSRF 头。
 *
 * 本文件不引入任何框架，只做两件事：
 *   1. 重新实现原页面用到的全局对象（AuthAPI / SectionAPI / FileAPI / GroupAPI /
 *      ScriptAPI / RuleAPI / LogAPI / MemberAPI / AIAPI / StatsAPI）以及
 *      getToken / setToken / clearToken；
 *   2. 把两边的数据形状互相翻译，让 legacy-app.js 可以逐字保留、无需改写。
 *
 * 命名对照（原 → 本项目）：
 *   files            → documents          file name: name
 *   f.type           → file_type          上传者: uploader(用户名)
 *   f.size           → （后端未返回字节数，见下方 SIZE_TEXT 说明）
 *   s.group_id       → 组名 group_name（后端没有独立的话术组/规则组表）
 *   { error }        → { detail }
 *   Bearer token     → access_token Cookie + X-CSRF-Token
 * ========================================================================== */
(function () {
  'use strict';

  var API_BASE = '/api/v1';

  /* 后端 /documents 返回真实的 size_bytes。
   * 原页面用 f.size 这类「已格式化字符串」显示大小并参与存储统计，所以这里
   * 统一格式化成 "12.3 MB" 形态；原始字节数另存 f.sizeBytes 供统计精确使用。 */
  var SIZE_TEXT = '0 KB';

  function formatSize(bytes) {
    var value = Number(bytes);
    if (!isFinite(value) || value <= 0) return SIZE_TEXT;
    if (value < 1024 * 1024) return (value / 1024).toFixed(1) + ' KB';
    if (value < 1024 * 1024 * 1024) return (value / (1024 * 1024)).toFixed(1) + ' MB';
    return (value / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
  }

  var TOKEN_KEY = 'kb_token';

  /* ============================ 基础工具 ============================ */

  function cookie(name) {
    var row = document.cookie.split('; ').find(function (item) { return item.indexOf(name + '=') === 0; });
    return row ? decodeURIComponent(row.split('=').slice(1).join('=')) : '';
  }

  function getToken() { return localStorage.getItem(TOKEN_KEY) || ''; }
  function setToken(value) { try { localStorage.setItem(TOKEN_KEY, value || 'cookie'); } catch (e) { /* 忽略 */ } }
  function removeToken() { try { localStorage.removeItem(TOKEN_KEY); } catch (e) { /* 忽略 */ } }

  /* 原页面每个 handleLogout 分支都会调用 clearToken()；
   * 这里顺便通知后端销毁会话（原来只清本地 token，服务端会话会残留）。 */
  function clearToken() {
    removeToken();
    fetch(API_BASE + '/auth/logout', {
      method: 'POST',
      credentials: 'include',
      headers: { 'X-CSRF-Token': cookie('csrf_token') }
    }).catch(function () { /* 退出接口失败不影响本地登出 */ });
  }

  function apiError(data, status) {
    var detail = data && (data.detail || data.message || data.error);
    if (Array.isArray(detail)) detail = detail.map(function (item) { return item && item.msg; }).filter(Boolean).join('；');
    if (!detail) detail = '请求失败（HTTP ' + status + '）';
    return new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }

  function request(path, options) {
    options = options || {};
    var headers = Object.assign({}, options.headers || {});
    if (!(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
    var method = (options.method || 'GET').toUpperCase();
    if (method !== 'GET' && method !== 'HEAD') headers['X-CSRF-Token'] = cookie('csrf_token');
    return fetch(API_BASE + path, Object.assign({}, options, { headers: headers, credentials: 'include' }))
      .then(function (response) {
        var type = response.headers.get('content-type') || '';
        var parsed = type.indexOf('json') >= 0 ? response.json().catch(function () { return null; }) : Promise.resolve(null);
        return parsed.then(function (data) {
          if (!response.ok) throw apiError(data, response.status);
          return data;
        });
      });
  }

  /* 后端返回 ISO（有时带 +00:00，有时是 naive），原页面按 "YYYY-MM-DD HH:MM:SS" 处理。 */
  function stamp(value) {
    if (!value) return '';
    var text = String(value).trim();
    if (!text || text.indexOf('T') < 0) return text;
    var hasZone = /(z|[+-]\d{2}:?\d{2})$/i.test(text);
    var date = new Date(hasZone ? text : text + 'Z');
    if (isNaN(date.getTime())) return text;
    var pad = function (n) { return (n < 10 ? '0' : '') + n; };
    return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate()) + ' ' +
      pad(date.getHours()) + ':' + pad(date.getMinutes()) + ':' + pad(date.getSeconds());
  }

  /* 后端 file_type 是去掉点的后缀（pdf / docx / jpg…），
   * 原页面 getFileTypeIcon() 需要 word/pdf/ppt/excel/img/image/video/audio/txt/other。 */
  function iconType(fileType) {
    var value = String(fileType || '').toLowerCase().replace(/^\./, '');
    if (value === 'pdf') return 'pdf';
    if (value === 'doc' || value === 'docx' || value === 'rtf') return 'word';
    if (value === 'ppt' || value === 'pptx') return 'ppt';
    if (value === 'xls' || value === 'xlsx' || value === 'csv') return 'excel';
    if (value === 'jpg' || value === 'jpeg' || value === 'png' || value === 'gif' || value === 'webp' || value === 'bmp') return 'img';
    if (value === 'txt' || value === 'md') return 'txt';
    if (value === 'mp4' || value === 'mov' || value === 'avi') return 'video';
    if (value === 'mp3' || value === 'wav' || value === 'm4a') return 'audio';
    if (value === 'zip' || value === 'rar' || value === '7z') return 'zip';
    return 'other';
  }

  /* 原页面的 typeIcon 只认「文档/图片/视频/音频」四个粗分类。 */
  function coarseType(type) {
    if (type === 'img' || type === 'image') return '图片';
    if (type === 'video') return '视频';
    if (type === 'audio') return '音频';
    return '文档';
  }

  /* 后端没有「话术组 / 规则组」表，组就是内容上的 group_name 字符串。
   * 为了支持「新建空分组」，额外在 localStorage 里登记尚未有内容的分组名。 */
  var GROUP_KEYS = { scripts: 'kb_replica_script_groups', rules: 'kb_replica_rule_groups' };

  function readLocalGroups(kind) {
    try { return JSON.parse(localStorage.getItem(GROUP_KEYS[kind]) || '[]'); } catch (e) { return []; }
  }
  function writeLocalGroups(kind, names) {
    try { localStorage.setItem(GROUP_KEYS[kind], JSON.stringify(names)); } catch (e) { /* 忽略 */ }
  }
  function addLocalGroup(kind, name) {
    var names = readLocalGroups(kind);
    if (name && names.indexOf(name) < 0) { names.push(name); writeLocalGroups(kind, names); }
  }
  function renameLocalGroup(kind, from, to) {
    writeLocalGroups(kind, readLocalGroups(kind).map(function (name) { return name === from ? to : name; }));
  }
  function dropLocalGroup(kind, name) {
    writeLocalGroups(kind, readLocalGroups(kind).filter(function (item) { return item !== name; }));
  }
  function fallbackGroup(kind) { return kind === 'rules' ? '通知规则' : '默认分组'; }

  /* ============================ 认证 ============================ */

  var AuthAPI = {
    /* 后端返回 { user, csrf_token }，没有 token 字段；原页面要读 data.token，
     * 这里补一个占位值，真正的凭证是 access_token Cookie。 */
    login: function (username, password) {
      return request('/auth/login', { method: 'POST', body: JSON.stringify({ username: username, password: password }) })
        .then(function (data) { setToken('cookie'); return { token: 'cookie', user: data.user }; });
    },
    register: function (username, password) {
      /* 原页面只校验「至少 4 位」，后端要求 8-128 位，这里提前给出可读的提示。 */
      if (!password || password.length < 8) {
        return Promise.reject(new Error('密码至少需要 8 位（后端安全策略要求）'));
      }
      return request('/auth/register', { method: 'POST', body: JSON.stringify({ username: username, password: password }) })
        .then(function (data) { setToken('cookie'); return { token: 'cookie', user: data.user }; });
    },
    me: function () { return request('/auth/me'); },
    changePassword: function (oldPassword, newPassword) {
      if (!newPassword || newPassword.length < 8) {
        return Promise.reject(new Error('新密码至少需要 8 位（后端安全策略要求）'));
      }
      return request('/auth/password', {
        method: 'PUT',
        body: JSON.stringify({ old_password: oldPassword, new_password: newPassword })
      });
    }
  };

  /* 供 Vue 宿主在加载 legacy-app.js 之前调用：
   * 原页面 autoLogin() 要求 localStorage 里存在 token 才会尝试恢复会话，
   * 而本项目是纯 Cookie 会话，所以先探一次 /auth/me，成功则补上 token 标记。 */
  function bootstrap() {
    return request('/auth/me')
      .then(function (data) { if (data && data.user) { setToken('cookie'); return data.user; } return null; })
      .catch(function () { removeToken(); return null; });
  }

  /* ============================ 板块 ============================ */

  var SectionAPI = {
    list: function () {
      return request('/sections').then(function (data) {
        return {
          sections: (data.items || []).map(function (item) {
            return { id: item.id, name: item.name, icon: '📁', owner: 'admin', sort_order: item.sort_order };
          })
        };
      });
    },
    create: function (name) { return request('/sections', { method: 'POST', body: JSON.stringify({ name: name }) }); },
    rename: function (id, name) { return request('/sections/' + encodeURIComponent(id), { method: 'PUT', body: JSON.stringify({ name: name }) }); },
    remove: function (id) { return request('/sections/' + encodeURIComponent(id), { method: 'DELETE' }); },
    reorder: function (order) { return request('/sections/order/reorder', { method: 'PUT', body: JSON.stringify({ order: order }) }); }
  };

  /* ============================ 文件 ============================ */

  function mapDocument(item) {
    var type = iconType(item.file_type);
    var when = stamp(item.uploaded_at);
    return {
      id: item.id,
      versionId: item.version_id,
      name: item.name,
      type: type,
      size: formatSize(item.size_bytes),
      sizeBytes: Number(item.size_bytes || 0),
      date: when.split(' ')[0],
      keywords: String(item.tags || '').split(',').map(function (tag) { return tag.trim(); }).filter(Boolean),
      aiSummary: '',
      iconType: type,
      /* previewKind 由后端判定（image / pdf / text / none）：
       * Office 文件只有转出 PDF 预览件后才是 pdf，不能只按后缀猜。 */
      previewKind: item.preview_kind || '',
      previewAvailable: item.preview_available !== false,
      sectionId: item.section_id,
      sectionName: item.section_name,
      owner: item.uploader,
      created_at: when,
      tags: item.tags || '',
      versionNo: item.version_no,
      status: item.ingestion_status
    };
  }

  /* legacy-app.js 用 `let MOCK_FILES = []` 声明这些数据，
   * 顶层 let/const 只创建「全局词法绑定」，不会挂到 window 上，
   * 所以必须用裸标识符访问（并防住 TDZ：脚本尚未加载时 typeof/取值会抛错）。 */
  function fileList() { try { return MOCK_FILES || []; } catch (e) { return []; } }
  function scriptList() { try { return MOCK_SCRIPTS || []; } catch (e) { return []; } }

  function findDocument(id) {
    return fileList().find(function (item) { return item.id === id; });
  }

  var FileAPI = {
    list: function (sectionId) {
      var path = '/documents' + (sectionId ? '?section_id=' + encodeURIComponent(sectionId) : '');
      return request(path).then(function (data) {
        return { files: (data.items || []).map(mapDocument) };
      });
    },
    get: function (id) {
      return request('/documents/' + encodeURIComponent(id) + '/versions').then(function (data) {
        var first = (data.items || [])[0];
        return first ? mapDocument(first) : null;
      });
    },
    /* 原页面传的 FormData 字段是 file / sectionId / title / tags，
     * 后端要 file / section_id / tags（标题由文件名决定，后端不支持自定义标题）。 */
    upload: function (formData) {
      var form = new FormData();
      form.append('file', formData.get('file'));
      form.append('section_id', formData.get('sectionId') || '');
      form.append('tags', formData.get('tags') || '');
      return request('/documents/upload', { method: 'POST', body: form });
    },
    download: function (id) {
      var document_ = findDocument(id);
      var versionId = document_ && document_.versionId;
      var url = API_BASE + '/documents/' + encodeURIComponent(id) + '/download' +
        (versionId ? '?version_id=' + encodeURIComponent(versionId) : '');
      return fetch(url, { credentials: 'include' }).then(function (response) {
        if (!response.ok) throw new Error('下载失败');
        return response.blob();
      });
    },
    update: function (id, data) {
      return request('/documents/' + encodeURIComponent(id), { method: 'PUT', body: JSON.stringify(data) });
    },
    remove: function (id) { return request('/documents/' + encodeURIComponent(id), { method: 'DELETE' }); },
    batchDelete: function (ids) { return request('/documents/batch-delete', { method: 'POST', body: JSON.stringify({ ids: ids }) }); },
    batchMove: function (ids, sectionId) {
      return request('/documents/batch-move', { method: 'POST', body: JSON.stringify({ ids: ids, section_id: sectionId }) });
    }
  };

  /* ============================ 话术组（由 group_name 派生） ============================ */

  function mapContent(item) {
    var group = item.group_name || fallbackGroup('scripts');
    return {
      id: item.id,
      groupId: group,
      group_id: group,
      title: item.title,
      content: item.content || '',
      tags: item.tags || '',
      keywords: [],
      summary: '',
      updatedAt: stamp(item.updated_at || item.created_at).split(' ')[0],
      created_at: stamp(item.created_at),
      owner: item.owner
    };
  }

  function listDerivedGroups(kind, endpoint) {
    return request(endpoint).then(function (data) {
      var names = [];
      (data.items || []).forEach(function (item) {
        var name = String(item.group_name || '').trim() || fallbackGroup(kind);
        if (names.indexOf(name) < 0) names.push(name);
      });
      readLocalGroups(kind).forEach(function (name) { if (names.indexOf(name) < 0) names.push(name); });
      return {
        groups: names.map(function (name, index) {
          return { id: name, name: name, icon: kind === 'rules' ? '📢' : '💬', owner: 'admin', sort_order: index };
        })
      };
    });
  }

  /* 重命名 / 删除分组 = 逐个改写组内内容的 group_name。 */
  function renameGroupEverywhere(kind, endpoint, from, to) {
    return request(endpoint).then(function (data) {
      var targets = (data.items || []).filter(function (item) { return (item.group_name || fallbackGroup(kind)) === from; });
      return targets.reduce(function (chain, item) {
        return chain.then(function () {
          return request(endpoint + '/' + encodeURIComponent(item.id), {
            method: 'PUT',
            body: JSON.stringify({
              title: item.title, content: item.content || '', group_name: to, tags: item.tags || ''
            })
          });
        });
      }, Promise.resolve());
    }).then(function () { renameLocalGroup(kind, from, to); });
  }

  function deleteGroupEverywhere(kind, endpoint, name) {
    return request(endpoint).then(function (data) {
      var targets = (data.items || []).filter(function (item) { return (item.group_name || fallbackGroup(kind)) === name; });
      return targets.reduce(function (chain, item) {
        return chain.then(function () {
          return request(endpoint + '/' + encodeURIComponent(item.id), { method: 'DELETE' });
        });
      }, Promise.resolve());
    }).then(function () { dropLocalGroup(kind, name); });
  }

  function makeGroupAPI(kind, endpoint) {
    return {
      list: function () { return listDerivedGroups(kind, endpoint); },
      create: function (name) { addLocalGroup(kind, name); return Promise.resolve({ ok: true }); },
      rename: function (id, name) { return renameGroupEverywhere(kind, endpoint, id, name); },
      remove: function (id) { return deleteGroupEverywhere(kind, endpoint, id); },
      reorder: function () { return Promise.resolve({ ok: true }); }
    };
  }

  /* ============================ 话术 / 规则 ============================ */

  function contentPayload(data) {
    return {
      title: data.title,
      content: data.content || '',
      group_name: data.groupId || data.group_id || data.group_name || fallbackGroup('scripts'),
      tags: typeof data.tags === 'string' ? data.tags : (data.tags || []).join(',')
    };
  }

  function makeContentAPI(kind, endpoint) {
    return {
      list: function () {
        return request(endpoint).then(function (data) {
          return { [kind]: (data.items || []).map(mapContent) };
        });
      },
      create: function (data) { return request(endpoint, { method: 'POST', body: JSON.stringify(contentPayload(data)) }); },
      update: function (id, data) {
        return request(endpoint + '/' + encodeURIComponent(id), { method: 'PUT', body: JSON.stringify(contentPayload(data)) });
      },
      remove: function (id) { return request(endpoint + '/' + encodeURIComponent(id), { method: 'DELETE' }); },
      /* 「删除」在各调用点有 remove / delete 两种写法，两个都提供，
       * 避免再出现 "RuleAPI.delete is not a function"。 */
      delete: function (id) { return request(endpoint + '/' + encodeURIComponent(id), { method: 'DELETE' }); }
    };
  }

  var ScriptAPI = makeContentAPI('scripts', '/scripts');
  var RuleAPI = Object.assign(makeContentAPI('rules', '/rules'), {
    listGroups: function () { return listDerivedGroups('rules', '/rules'); },
    createGroup: function (name) { addLocalGroup('rules', name); return Promise.resolve({ ok: true }); },
    updateGroup: function (id, name) { return renameGroupEverywhere('rules', '/rules', id, name); },
    deleteGroup: function (id) { return deleteGroupEverywhere('rules', '/rules', id); },
    notifyDingtalk: function (id) {
      return request('/rules/' + encodeURIComponent(id) + '/notify-dingtalk', { method: 'POST' });
    },
    /* 后端没有 /rules/upload-image，也没有任何返回 image_object_key 的接口。
     * 为了让「粘贴截图 → OCR 填正文 → 预览缩略图」这条链路仍然可用，
     * 这里把图片读成 dataURL 缓存在内存里，配合下面的 URL 桥接替换 /uploads/ 前缀。
     * 注意：图片不会持久化，刷新后消失。 */
    uploadImage: function (file) {
      return new Promise(function (resolve, reject) {
        var reader = new FileReader();
        reader.onload = function () {
          var key = 'local_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8) + '.png';
          UPLOAD_CACHE[key] = reader.result;
          resolve({ imagePath: key, local: true });
        };
        reader.onerror = function () { reject(new Error('图片读取失败')); };
        reader.readAsDataURL(file);
      });
    }
  });

  /* ============================ 日志 ============================ */

  var LogAPI = {
    list: function (page, operator) {
      var query = operator ? '?operator=' + encodeURIComponent(operator) : '';
      return request('/logs' + query).then(function (data) {
        return {
          logs: (data.items || []).map(function (item) {
            return {
              id: item.id,
              created_at: stamp(item.created_at),
              operator: item.operator,
              action: item.action,
              target_type: item.target_type,
              target_name: item.target_name,
              detail: item.detail || '-'
            };
          })
        };
      });
    },
    clear: function () { return request('/logs', { method: 'DELETE' }); }
  };

  /* ============================ 成员 ============================ */

  var memberCache = [];

  var MemberAPI = {
    list: function () {
      return request('/members').then(function (data) {
        memberCache = data.items || [];
        return {
          users: memberCache.map(function (item) {
            return { username: item.username, password: '', role: item.role, createdAt: stamp(item.created_at) };
          })
        };
      });
    },
    /* 原页面按用户名改角色，后端按用户 id 改，且 role 走 query 参数。 */
    setRole: function (username, role) {
      var target = memberCache.find(function (item) { return item.username === username; });
      if (!target) return Promise.reject(new Error('未找到该成员'));
      return request('/members/' + encodeURIComponent(target.id) + '/role?role=' + encodeURIComponent(role), { method: 'PUT' });
    }
  };

  /* ============================ AI ============================ */

  /* 原页面 /ai/quick-search 是关键词匹配（非大模型）。后端没有这个接口，
   * 这里用已加载到内存的文件/话术做同样的打分匹配，保持「即时显示」的体验。 */
  function quickSearch(query) {
    var raw = String(query || '').trim();
    var needle = raw.toLowerCase();
    var scriptNeedle = needle.replace(/话术/g, '');

    var files = fileList().map(function (file) {
      var score = 0;
      if (String(file.name || '').toLowerCase().indexOf(needle) >= 0) score += 40;
      (file.keywords || []).forEach(function (word) { if (String(word).toLowerCase().indexOf(needle) >= 0) score += 25; });
      if (String(file.aiSummary || '').toLowerCase().indexOf(needle) >= 0) score += 15;
      if (String(file.sectionName || '').toLowerCase().indexOf(needle) >= 0) score += 10;
      return { file: file, score: Math.min(score, 98) };
    }).filter(function (hit) {
      return hit.score > 5;
    }).sort(function (a, b) { return b.score - a.score; }).slice(0, 5);

    var scripts = (scriptNeedle ? scriptList().map(function (script) {
      var score = 0;
      var plain = String(script.content || '').replace(/<[^>]*>/g, '').toLowerCase();
      if (String(script.title || '').toLowerCase().indexOf(scriptNeedle) >= 0) score += 40;
      if (plain.indexOf(scriptNeedle) >= 0) score += 25;
      (script.keywords || []).forEach(function (word) { if (String(word).toLowerCase().indexOf(scriptNeedle) >= 0) score += 25; });
      return { script: script, score: Math.min(score, 100) };
    }).filter(function (hit) {
      return hit.score >= 35;
    }).sort(function (a, b) { return b.score - a.score; }).slice(0, 3) : []);

    return Promise.resolve({
      files: files.map(function (hit) {
        return { id: hit.file.id, name: hit.file.name, type: coarseType(hit.file.iconType), size: hit.file.size };
      }),
      scripts: scripts.map(function (hit) {
        return {
          id: hit.script.id,
          title: hit.script.title,
          content: hit.script.content,
          group_name: hit.script.groupId || '',
          tags: Array.isArray(hit.script.tags) ? hit.script.tags.join('、') : (hit.script.tags || '')
        };
      })
    });
  }

  var AIAPI = {
    /* 所有聊天输入先进入后端统一意图路由。后端以 NDJSON 发送状态、意图、
     * token、文件与引用事件，前端可以边接收边渲染，不再先做本地关键词匹配。 */
    stream: async function (query, onEvent) {
      var response = await fetch(API_BASE + '/chat/stream', {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': cookie('csrf_token')
        },
        body: JSON.stringify({ query: query })
      });
      if (!response.ok) {
        var errorData = await response.json().catch(function () { return null; });
        throw apiError(errorData, response.status);
      }
      if (!response.body) throw new Error('当前浏览器不支持流式响应');

      var reader = response.body.getReader();
      var decoder = new TextDecoder('utf-8');
      var buffer = '';

      function emit(line) {
        if (!line.trim()) return;
        var event = JSON.parse(line);
        if (event.type === 'files') event.items = (event.items || []).map(mapDocument);
        if (typeof onEvent === 'function') onEvent(event);
      }

      while (true) {
        var chunk = await reader.read();
        buffer += decoder.decode(chunk.value || new Uint8Array(), { stream: !chunk.done });
        var lines = buffer.split('\n');
        buffer = lines.pop() || '';
        lines.forEach(emit);
        if (chunk.done) break;
      }
      if (buffer.trim()) emit(buffer);
    },
    /* 原页面 /ai/search 读 result.reply，对应后端的 /chat（大模型问答 + 检索）。 */
    search: function (query) {
      return request('/chat', { method: 'POST', body: JSON.stringify({ query: query }) }).then(function (data) {
        if (data.type === 'file_results') {
          var count = (data.items || []).length;
          return { reply: (data.answer || '') + '（共 ' + count + ' 个文件，可点击上方结果查看）' };
        }
        var reply = data.answer || data.message || '分析完成';
        var sources = (data.citations || []).map(function (item) {
          return item.file_name || item.source || item.section_name || '';
        }).filter(Boolean);
        if (sources.length) reply += '\n\n参考来源：' + sources.slice(0, 5).join('、');
        return { reply: reply };
      });
    },
    quickSearch: quickSearch,
    keywordCloud: function () {
      return request('/ai/keyword-cloud').then(function (data) {
        return {
          cloud: data.items || data.cloud || [],
          totalQuestions: Number(data.total_questions || 0),
          uniqueQuestions: Number(data.unique_questions || 0)
        };
      });
    },
    analyze: function (content) { return request('/ai/analyze', { method: 'POST', body: JSON.stringify({ content: content }) }); },
    ocr: function () { return Promise.reject(new Error('OCR 由浏览器端 Tesseract 完成')); }
  };

  /* ============================ 统计 ============================ */

  var StatsAPI = { get: function () { return request('/stats'); } };

  var SettingsAPI = {
    dingtalk: function () { return request('/settings/dingtalk'); },
    saveDingtalk: function (payload) {
      return request('/settings/dingtalk', { method: 'PUT', body: JSON.stringify(payload) });
    },
    testDingtalk: function () { return request('/settings/dingtalk/test', { method: 'POST' }); }
  };

  function mapFaq(item) {
    return {
      id: item.id,
      question: item.question || '',
      answer: item.answer || '',
      category: item.category || '通用',
      tags: item.tags || '',
      owner: item.owner || '',
      indexed: Boolean(item.indexed),
      createdAt: stamp(item.created_at),
      updatedAt: stamp(item.updated_at)
    };
  }

  var FAQAPI = {
    list: function () {
      return request('/faqs').then(function (data) { return { faqs: (data.items || []).map(mapFaq) }; });
    },
    create: function (payload) {
      return request('/faqs', { method: 'POST', body: JSON.stringify(payload) });
    },
    update: function (id, payload) {
      return request('/faqs/' + encodeURIComponent(id), { method: 'PUT', body: JSON.stringify(payload) });
    },
    remove: function (id) {
      return request('/faqs/' + encodeURIComponent(id), { method: 'DELETE' });
    }
  };

  var JobAPI = {
    list: function () {
      return request('/ingestion-jobs').then(function (data) {
        return { jobs: data.items || [] };
      });
    },
    retry: function (jobId) {
      return request('/ingestion-jobs/' + encodeURIComponent(jobId) + '/retry', { method: 'POST' });
    }
  };

  /* ============================ URL 桥接 ============================
   * 原页面把图片地址硬编码成 /api/files/{id}/view 与 /uploads/{path}，
   * 这两个前缀在本项目后端并不存在。这里用一个 MutationObserver 在图片插入
   * DOM 后改写 src，从而不必改动 legacy-app.js 里的模板字符串。
   * ================================================================= */

  var UPLOAD_CACHE = {};

  function rewriteSource(image) {
    var source = image.getAttribute('src') || '';
    if (source.indexOf('/api/files/') === 0) {
      var id = source.slice('/api/files/'.length).split('/')[0];
      if (id) image.setAttribute('src', API_BASE + '/documents/' + encodeURIComponent(id) + '/preview');
    } else if (source.indexOf('/uploads/') === 0) {
      var cached = UPLOAD_CACHE[source.slice('/uploads/'.length)];
      if (cached) image.setAttribute('src', cached);
    }
  }

  function scanNode(node) {
    if (!node || node.nodeType !== 1) return;
    if (node.tagName === 'IMG') rewriteSource(node);
    if (node.querySelectorAll) {
      node.querySelectorAll('img[src^="/api/files/"], img[src^="/uploads/"]').forEach(rewriteSource);
    }
  }

  function installUrlBridge() {
    if (!window.MutationObserver) return;
    new MutationObserver(function (records) {
      records.forEach(function (record) {
        record.addedNodes.forEach(scanNode);
      });
    }).observe(document.documentElement, { childList: true, subtree: true });
  }

  /* ============================ 暴露到全局 ============================ */

  Object.assign(window, {
    AuthAPI: AuthAPI,
    SectionAPI: SectionAPI,
    FileAPI: FileAPI,
    GroupAPI: makeGroupAPI('scripts', '/scripts'),
    ScriptAPI: ScriptAPI,
    RuleAPI: RuleAPI,
    LogAPI: LogAPI,
    MemberAPI: MemberAPI,
    AIAPI: AIAPI,
    StatsAPI: StatsAPI,
    SettingsAPI: SettingsAPI,
    FAQAPI: FAQAPI,
    JobAPI: JobAPI,
    getToken: getToken,
    setToken: setToken,
    clearToken: clearToken,
    ReplicaAuth: { bootstrap: bootstrap }
  });

  installUrlBridge();
})();
