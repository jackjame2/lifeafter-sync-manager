// ============================================================
// License Key Management System - Cloudflare Worker
// 卡密管理系统 - API + Admin Panel
// ============================================================

// ---- Utility: CORS headers ----
function corsHeaders() {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    // Content-Type is set per-route
  };
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json; charset=utf-8', ...corsHeaders() } });
}

function error(msg, status = 400) {
  return json({ error: msg }, status);
}

// ---- Utility: Hash a license key (SHA-256, hex) ----
async function hashKey(key) {
  const normalized = key.toUpperCase().replace(/[^A-Z0-9]/g, '');
  const data = new TextEncoder().encode(normalized);
  const hashBuffer = await crypto.subtle.digest('SHA-256', data);
  return Array.from(new Uint8Array(hashBuffer))
    .map(b => b.toString(16).padStart(2, '0'))
    .join('');
}

// ---- Utility: Generate a random key segment ----
function randomSegment(length = 4) {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'; // no I,O,0,1 to avoid confusion
  let result = '';
  const bytes = new Uint8Array(length);
  crypto.getRandomValues(bytes);
  for (let i = 0; i < length; i++) {
    result += chars[bytes[i] % chars.length];
  }
  return result;
}

function generateKey() {
  return `${randomSegment(4)}-${randomSegment(4)}-${randomSegment(4)}-${randomSegment(4)}`;
}

// ---- Utility: Normalize key input ----
function normalizeKey(input) {
  return input.toUpperCase().replace(/[^A-Z0-9]/g, '');
}

// ---- Utility: Check admin auth ----
function checkAdmin(request, env) {
  const auth = request.headers.get('Authorization') || '';
  const token = auth.replace(/^Bearer\s+/i, '');
  return token === env.ADMIN_PASSWORD;
}

// ---- Utility: Log an action ----
async function logAction(db, keyId, action, hwid, ip, details = '') {
  await db.prepare(
    'INSERT INTO usage_logs (key_id, action, ip_address, hwid, details) VALUES (?, ?, ?, ?, ?)'
  ).bind(keyId, action, ip, hwid, details).run();
}

// ============================================================
// API Handlers
// ============================================================

// POST /api/verify - Verify a license key
async function handleVerify(db, body, ip) {
  const { key } = body;
  if (!key || typeof key !== 'string') return error('Missing key', 400);

  const normalized = normalizeKey(key);
  if (normalized.length < 8) return error('Invalid key format', 400);

  const kh = await hashKey(key);
  const row = await db.prepare(
    'SELECT id, key_prefix, status, hwid, hwid_2, activated_at, expires_at, notes FROM license_keys WHERE key_hash = ?'
  ).bind(kh).first();

  if (!row) {
    return json({ valid: false, status: 'not_found', message: '卡密不存在' });
  }

  // Check expiry
  if (row.expires_at && new Date(row.expires_at) < new Date()) {
    if (row.status !== 'revoked') {
      await db.prepare("UPDATE license_keys SET status = 'expired' WHERE id = ?").bind(row.id).run();
      row.status = 'expired';
    }
  }

  await logAction(db, row.id, 'verify', '', ip, `status=${row.status}`);

  if (row.status === 'revoked') {
    return json({ valid: false, status: 'revoked', message: '卡密已被禁用' });
  }
  if (row.status === 'expired') {
    return json({ valid: false, status: 'expired', message: '卡密已过期' });
  }

  return json({
    valid: true,
    status: row.status,
    key_prefix: row.key_prefix,
    bound: !!row.hwid,
    activated_at: row.activated_at,
  });
}

// POST /api/activate - Activate (bind) a key to a machine
async function handleActivate(db, body, ip) {
  const { key, hwid } = body;
  if (!key || typeof key !== 'string') return error('Missing key', 400);
  if (!hwid || typeof hwid !== 'string') return error('Missing hwid', 400);

  const normalized = normalizeKey(key);
  if (normalized.length < 8) return error('Invalid key format', 400);

  const kh = await hashKey(key);
  const row = await db.prepare(
    'SELECT id, key_prefix, status, hwid, hwid_2 FROM license_keys WHERE key_hash = ?'
  ).bind(kh).first();

  if (!row) {
    return json({ success: false, message: '卡密不存在' });
  }

  if (row.status === 'revoked') {
    await logAction(db, row.id, 'activate_blocked', hwid, ip, 'key revoked');
    return json({ success: false, message: '卡密已被禁用，请联系管理员' });
  }

  if (row.status === 'expired') {
    return json({ success: false, message: '卡密已过期' });
  }

  // If already bound to this hwid, just return success
  if (row.hwid === hwid || row.hwid_2 === hwid) {
    await db.prepare(
      'UPDATE license_keys SET activation_count = activation_count + 1 WHERE id = ?'
    ).bind(row.id).run();
    await logAction(db, row.id, 'reactivate', hwid, ip, 'already bound');
    return json({ success: true, message: '验证成功', already_bound: true });
  }

  // If bound to a different hwid, reject
  if (row.hwid) {
    await logAction(db, row.id, 'activate_blocked', hwid, ip, `bound to different hwid: ${row.hwid.substring(0, 12)}...`);
    return json({ success: false, message: '该卡密已绑定到其他设备，请联系管理员解绑' });
  }

  // Bind the key
  const now = new Date().toISOString();
  await db.prepare(
    "UPDATE license_keys SET hwid = ?, activated_at = ?, status = 'used', activation_count = 1 WHERE id = ?"
  ).bind(hwid, now, row.id).run();

  await logAction(db, row.id, 'activate', hwid, ip, 'first activation');

  return json({ success: true, message: '激活成功', key_prefix: row.key_prefix });
}

// ============================================================
// Admin API Handlers (all require admin password)
// ============================================================

// POST /api/admin/keys - List all keys
async function handleAdminKeys(db, body) {
  const { status, search, limit = 100, offset = 0 } = body || {};

  let sql = 'SELECT id, key_prefix, status, created_at, activated_at, expires_at, hwid, activation_count, notes FROM license_keys';
  const conditions = [];
  const params = [];

  if (status && status !== 'all') {
    conditions.push('status = ?');
    params.push(status);
  }
  if (search) {
    conditions.push('(key_prefix LIKE ? OR notes LIKE ?)');
    params.push(`%${search}%`, `%${search}%`);
  }

  if (conditions.length > 0) {
    sql += ' WHERE ' + conditions.join(' AND ');
  }

  sql += ' ORDER BY created_at DESC LIMIT ? OFFSET ?';
  params.push(limit, offset);

  const { results } = await db.prepare(sql).bind(...params).all();

  // Count total
  let countSql = 'SELECT COUNT(*) as total FROM license_keys';
  if (conditions.length > 0) {
    countSql += ' WHERE ' + conditions.join(' AND ');
  }
  const countResult = await db.prepare(countSql).bind(...params.slice(0, -2)).first();

  return json({ keys: results, total: countResult.total });
}

// POST /api/admin/create - Create new keys
async function handleAdminCreate(db, body) {
  const { count = 1, notes = '' } = body || {};
  const numKeys = Math.min(Math.max(1, parseInt(count) || 1), 100);

  const created = [];
  const stmt = db.prepare(
    "INSERT INTO license_keys (key_hash, key_prefix, status, notes) VALUES (?, ?, 'active', ?)"
  );

  // Use batch for efficiency
  const batchOps = [];
  for (let i = 0; i < numKeys; i++) {
    const rawKey = generateKey();
    const kh = await hashKey(rawKey);
    batchOps.push(stmt.bind(kh, rawKey.substring(0, 7) + '...', notes));
    created.push(rawKey);
  }

  await db.batch(batchOps);

  return json({
    success: true,
    created: numKeys,
    keys: created,
    message: `成功生成 ${numKeys} 个卡密`,
  });
}

// POST /api/admin/revoke - Revoke a key
async function handleAdminRevoke(db, body) {
  const { key } = body || {};
  if (!key) return error('Missing key', 400);

  const kh = await hashKey(key);
  const row = await db.prepare('SELECT id, key_prefix, status FROM license_keys WHERE key_hash = ?').bind(kh).first();
  if (!row) return error('Key not found', 404);

  await db.prepare("UPDATE license_keys SET status = 'revoked' WHERE id = ?").bind(row.id).run();
  await logAction(db, row.id, 'revoke', '', '', 'admin action');

  return json({ success: true, message: `卡密 ${row.key_prefix} 已禁用` });
}

// POST /api/admin/unrevoke - Re-enable a revoked key
async function handleAdminUnrevoke(db, body) {
  const { key } = body || {};
  if (!key) return error('Missing key', 400);

  const kh = await hashKey(key);
  const row = await db.prepare('SELECT id, key_prefix, status FROM license_keys WHERE key_hash = ?').bind(kh).first();
  if (!row) return error('Key not found', 404);

  const newStatus = row.hwid ? 'used' : 'active';
  await db.prepare('UPDATE license_keys SET status = ? WHERE id = ?').bind(newStatus, row.id).run();
  await logAction(db, row.id, 'unrevoke', '', '', 'admin action');

  return json({ success: true, message: `卡密 ${row.key_prefix} 已恢复` });
}

// POST /api/admin/reset - Reset a key (unbind from machine)
async function handleAdminReset(db, body) {
  const { key } = body || {};
  if (!key) return error('Missing key', 400);

  const kh = await hashKey(key);
  const row = await db.prepare('SELECT id, key_prefix, hwid FROM license_keys WHERE key_hash = ?').bind(kh).first();
  if (!row) return error('Key not found', 404);

  await db.prepare(
    "UPDATE license_keys SET hwid = NULL, hwid_2 = NULL, activated_at = NULL, status = 'active', activation_count = 0 WHERE id = ?"
  ).bind(row.id).run();
  await logAction(db, row.id, 'reset', '', '', 'admin action - unbound');

  return json({ success: true, message: `卡密 ${row.key_prefix} 已解绑，可重新绑定` });
}

// POST /api/admin/delete - Delete a key
async function handleAdminDelete(db, body) {
  const { key } = body || {};
  if (!key) return error('Missing key', 400);

  const kh = await hashKey(key);
  const row = await db.prepare('SELECT id, key_prefix FROM license_keys WHERE key_hash = ?').bind(kh).first();
  if (!row) return error('Key not found', 404);

  await db.prepare('DELETE FROM usage_logs WHERE key_id = ?').bind(row.id).run();
  await db.prepare('DELETE FROM license_keys WHERE id = ?').bind(row.id).run();

  return json({ success: true, message: `卡密 ${row.key_prefix} 已删除` });
}

// POST /api/admin/stats - Statistics
async function handleAdminStats(db) {
  const total = await db.prepare('SELECT COUNT(*) as cnt FROM license_keys').first();
  const active = await db.prepare("SELECT COUNT(*) as cnt FROM license_keys WHERE status = 'active'").first();
  const used = await db.prepare("SELECT COUNT(*) as cnt FROM license_keys WHERE status = 'used'").first();
  const revoked = await db.prepare("SELECT COUNT(*) as cnt FROM license_keys WHERE status = 'revoked'").first();
  const expired = await db.prepare("SELECT COUNT(*) as cnt FROM license_keys WHERE status = 'expired'").first();
  const todayActivations = await db.prepare(
    "SELECT COUNT(*) as cnt FROM usage_logs WHERE action = 'activate' AND created_at >= datetime('now', '-1 day')"
  ).first();
  const totalLogs = await db.prepare('SELECT COUNT(*) as cnt FROM usage_logs').first();

  return json({
    total_keys: total.cnt,
    active_keys: active.cnt,
    used_keys: used.cnt,
    revoked_keys: revoked.cnt,
    expired_keys: expired.cnt,
    today_activations: todayActivations.cnt,
    total_logs: totalLogs.cnt,
  });
}

// POST /api/admin/logs - Get usage logs
async function handleAdminLogs(db, body) {
  const { key, limit = 100, offset = 0 } = body || {};
  let sql, params;

  if (key) {
    const kh = await hashKey(key);
    const keyRow = await db.prepare('SELECT id FROM license_keys WHERE key_hash = ?').bind(kh).first();
    if (!keyRow) return error('Key not found', 404);
    sql = 'SELECT * FROM usage_logs WHERE key_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?';
    params = [keyRow.id, limit, offset];
  } else {
    sql = 'SELECT usage_logs.*, license_keys.key_prefix FROM usage_logs LEFT JOIN license_keys ON usage_logs.key_id = license_keys.id ORDER BY usage_logs.created_at DESC LIMIT ? OFFSET ?';
    params = [limit, offset];
  }

  const { results } = await db.prepare(sql).bind(...params).all();
  return json({ logs: results });
}

// ============================================================
// Admin Panel HTML
// ============================================================

function adminHTML() {
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>卡密管理系统</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f0f11; color: #e4e4e7; min-height: 100vh; }
  .container { max-width: 1100px; margin: 0 auto; padding: 24px; }
  h1 { font-size: 22px; font-weight: 600; margin-bottom: 4px; }
  .subtitle { color: #71717a; font-size: 13px; margin-bottom: 24px; }
  .card { background: #18181b; border: 1px solid #27272a; border-radius: 8px; padding: 20px; margin-bottom: 16px; }
  .card h2 { font-size: 15px; font-weight: 600; margin-bottom: 12px; color: #a1a1aa; text-transform: uppercase; letter-spacing: 0.5px; }
  .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 8px; }
  .stat-item { background: #27272a; border-radius: 6px; padding: 12px 16px; }
  .stat-value { font-size: 28px; font-weight: 700; color: #fafafa; }
  .stat-label { font-size: 12px; color: #71717a; margin-top: 2px; }
  input, select { background: #27272a; border: 1px solid #3f3f46; color: #e4e4e7; padding: 8px 12px; border-radius: 6px; font-size: 13px; outline: none; width: 100%; }
  input:focus, select:focus { border-color: #6366f1; }
  .input-group { display: flex; gap: 8px; flex-wrap: wrap; }
  .input-group > * { flex: 1; min-width: 120px; }
  button { background: #6366f1; color: #fff; border: none; padding: 8px 16px; border-radius: 6px; font-size: 13px; cursor: pointer; font-weight: 500; white-space: nowrap; transition: background 0.15s; }
  button:hover { background: #5457e5; }
  button.danger { background: #dc2626; }
  button.danger:hover { background: #b91c1c; }
  button.success { background: #16a34a; }
  button.success:hover { background: #15803d; }
  button.warn { background: #ca8a04; }
  button.warn:hover { background: #a16207; }
  button.secondary { background: #3f3f46; }
  button.secondary:hover { background: #52525b; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; padding: 10px 8px; color: #71717a; font-weight: 500; border-bottom: 1px solid #27272a; }
  td { padding: 10px 8px; border-bottom: 1px solid #1f1f23; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 500; }
  .badge-active { background: #064e3b; color: #6ee7b7; }
  .badge-used { background: #1e3a5f; color: #93c5fd; }
  .badge-revoked { background: #450a0a; color: #fca5a5; }
  .badge-expired { background: #422006; color: #fdba74; }
  .key-cell { font-family: "SF Mono", "Fira Code", monospace; font-size: 12px; color: #fbbf24; }
  .hwid-cell { font-family: "SF Mono", "Fira Code", monospace; font-size: 10px; color: #71717a; max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .toast { position: fixed; top: 16px; right: 16px; background: #16a34a; color: #fff; padding: 12px 20px; border-radius: 8px; font-size: 13px; z-index: 1000; opacity: 0; transition: opacity 0.3s; pointer-events: none; }
  .toast.show { opacity: 1; }
  .toast.error { background: #dc2626; }
  .generated-keys { background: #27272a; border-radius: 6px; padding: 12px; margin-top: 12px; max-height: 300px; overflow-y: auto; display: none; }
  .generated-keys.visible { display: block; }
  .generated-keys textarea { width: 100%; min-height: 120px; background: #18181b; border: 1px solid #3f3f46; color: #fbbf24; font-family: "SF Mono", "Fira Code", monospace; font-size: 13px; padding: 10px; border-radius: 4px; resize: vertical; }
  .filter-bar { display: flex; gap: 8px; margin-bottom: 16px; align-items: center; flex-wrap: wrap; }
  .filter-bar select { width: auto; min-width: 120px; }
  .filter-bar input { width: auto; flex: 1; min-width: 150px; }
  .actions { display: flex; gap: 6px; flex-wrap: wrap; }
  .actions button { padding: 5px 10px; font-size: 12px; }
  .section-actions { display: flex; gap: 8px; margin-bottom: 12px; }
  .empty-state { text-align: center; padding: 40px; color: #71717a; }
  .pagination { display: flex; gap: 8px; justify-content: center; margin-top: 16px; align-items: center; }
  .pagination span { color: #71717a; font-size: 12px; }
  .quick-verify { display: flex; gap: 8px; margin-top: 12px; align-items: center; }
  .quick-verify input { flex: 1; }
  .verify-result { margin-top: 8px; font-size: 13px; padding: 8px 12px; border-radius: 6px; display: none; }
  .verify-result.visible { display: block; }
  .verify-result.success { background: #064e3b; color: #6ee7b7; }
  .verify-result.fail { background: #450a0a; color: #fca5a5; }
</style>
</head>
<body>
<div class="container">
  <h1>卡密管理系统</h1>
  <p class="subtitle">License Key Manager &middot; Cloudflare Workers + D1</p>

  <!-- Stats Card -->
  <div class="card">
    <h2>统计概览</h2>
    <div class="stats-grid" id="stats-container">
      <div class="stat-item"><div class="stat-value">-</div><div class="stat-label">总卡密</div></div>
      <div class="stat-item"><div class="stat-value">-</div><div class="stat-label">可用</div></div>
      <div class="stat-item"><div class="stat-value">-</div><div class="stat-label">已使用</div></div>
      <div class="stat-item"><div class="stat-value">-</div><div class="stat-label">已禁用</div></div>
      <div class="stat-item"><div class="stat-value">-</div><div class="stat-label">已过期</div></div>
      <div class="stat-item"><div class="stat-value">-</div><div class="stat-label">今日激活</div></div>
    </div>
  </div>

  <!-- Generate Keys Card -->
  <div class="card">
    <h2>生成卡密</h2>
    <div class="input-group">
      <input type="number" id="gen-count" value="1" min="1" max="100" placeholder="生成数量">
      <input type="text" id="gen-notes" placeholder="备注 (可选)">
      <button onclick="generateKeys()">生成卡密</button>
    </div>
    <div class="generated-keys" id="generated-keys-box">
      <textarea id="generated-keys-text" readonly></textarea>
      <button class="secondary" style="margin-top:8px" onclick="copyGeneratedKeys()">复制全部</button>
      <button class="secondary" style="margin-top:8px" onclick="downloadKeys()">下载 TXT</button>
    </div>
  </div>

  <!-- Quick Verify Card -->
  <div class="card">
    <h2>快速查询</h2>
    <div class="quick-verify">
      <input type="text" id="quick-verify-input" placeholder="输入卡密查询状态...">
      <button onclick="quickVerify()">查询</button>
    </div>
    <div class="verify-result" id="verify-result"></div>
  </div>

  <!-- Keys List Card -->
  <div class="card">
    <h2>卡密列表</h2>
    <div class="filter-bar">
      <select id="filter-status" onchange="loadKeys()">
        <option value="all">全部状态</option>
        <option value="active">可用</option>
        <option value="used">已使用</option>
        <option value="revoked">已禁用</option>
        <option value="expired">已过期</option>
      </select>
      <input type="text" id="filter-search" placeholder="搜索卡密或备注..." oninput="debounceLoadKeys()">
      <button onclick="loadKeys()">刷新</button>
    </div>
    <div id="keys-table-container">
      <table>
        <thead><tr>
          <th>卡密</th><th>状态</th><th>创建时间</th><th>激活时间</th><th>设备ID</th><th>激活次数</th><th>备注</th><th>操作</th>
        </tr></thead>
        <tbody id="keys-tbody"><tr><td colspan="8" class="empty-state">Loading...</td></tr></tbody>
      </table>
    </div>
    <div class="pagination" id="pagination"></div>
  </div>

  <!-- Logs Card -->
  <div class="card">
    <h2>操作日志 (最近50条)</h2>
    <table>
      <thead><tr><th>时间</th><th>卡密</th><th>操作</th><th>IP</th><th>详情</th></tr></thead>
      <tbody id="logs-tbody"><tr><td colspan="5" class="empty-state">Loading...</td></tr></tbody>
    </table>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
  const API_BASE = '';
  let currentPage = 0, pageSize = 50, totalKeys = 0;
  let debounceTimer;

  async function api(path, body) {
    const res = await fetch(API_BASE + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + getPassword() },
      body: body ? JSON.stringify(body) : undefined,
    });
    return res.json();
  }

  function getPassword() {
    let pw = sessionStorage.getItem('admin_pw');
    if (!pw) {
      pw = prompt('请输入管理员密码:');
      if (pw) sessionStorage.setItem('admin_pw', pw);
    }
    return pw || '';
  }

  function showToast(msg, isError) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.className = 'toast' + (isError ? ' error' : '') + ' show';
    setTimeout(() => t.className = 'toast', 2500);
  }

  function formatDate(d) {
    if (!d) return '-';
    try { return new Date(d).toLocaleString('zh-CN'); } catch(e) { return d; }
  }

  function statusBadge(s) {
    const map = { active: 'badge-active', used: 'badge-used', revoked: 'badge-revoked', expired: 'badge-expired' };
    const labels = { active: '可用', used: '已使用', revoked: '已禁用', expired: '已过期' };
    return '<span class="badge ' + (map[s] || '') + '">' + (labels[s] || s) + '</span>';
  }

  async function loadStats() {
    const data = await api('/api/admin/stats');
    if (data.error) return;
    const vals = [data.total_keys, data.active_keys, data.used_keys, data.revoked_keys, data.expired_keys, data.today_activations];
    const items = document.querySelectorAll('#stats-container .stat-value');
    vals.forEach((v, i) => { if (items[i]) items[i].textContent = v; });
  }

  async function loadKeys() {
    const status = document.getElementById('filter-status').value;
    const search = document.getElementById('filter-search').value;
    const data = await api('/api/admin/keys', { status, search, limit: pageSize, offset: currentPage * pageSize });
    if (data.error) { showToast(data.error, true); return; }
    totalKeys = data.total;
    const tbody = document.getElementById('keys-tbody');
    if (!data.keys || data.keys.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" class="empty-state">没有找到卡密</td></tr>';
    } else {
      tbody.innerHTML = data.keys.map(k => '<tr>' +
        '<td class="key-cell">' + k.key_prefix + '</td>' +
        '<td>' + statusBadge(k.status) + '</td>' +
        '<td>' + formatDate(k.created_at) + '</td>' +
        '<td>' + formatDate(k.activated_at) + '</td>' +
        '<td class="hwid-cell" title="' + (k.hwid || '') + '">' + (k.hwid ? k.hwid.substring(0, 24) + '...' : '-') + '</td>' +
        '<td>' + k.activation_count + '</td>' +
        '<td>' + (k.notes || '-') + '</td>' +
        '<td class="actions">' +
          (k.status === 'revoked' ? '<button class="warn" onclick="unrevokeKey(\\'' + k.key_prefix + '\\')">恢复</button>' : '') +
          (k.status !== 'revoked' ? '<button class="danger" onclick="revokeKey(\\'' + k.key_prefix + '\\')">禁用</button>' : '') +
          '<button class="warn" onclick="resetKey(\\'' + k.key_prefix + '\\')">解绑</button>' +
          '<button class="danger" onclick="deleteKey(\\'' + k.key_prefix + '\\')">删除</button>' +
        '</td>' +
      '</tr>').join('');
    }
    renderPagination();
  }

  function renderPagination() {
    const totalPages = Math.ceil(totalKeys / pageSize);
    document.getElementById('pagination').innerHTML =
      '<button onclick="goPage(' + (currentPage - 1) + ')" ' + (currentPage <= 0 ? 'disabled' : '') + '>上一页</button>' +
      '<span>第 ' + (currentPage + 1) + ' / ' + totalPages + ' 页 (共 ' + totalKeys + ' 条)</span>' +
      '<button onclick="goPage(' + (currentPage + 1) + ')" ' + (currentPage >= totalPages - 1 ? 'disabled' : '') + '>下一页</button>';
  }

  function goPage(p) {
    if (p < 0 || p >= Math.ceil(totalKeys / pageSize)) return;
    currentPage = p;
    loadKeys();
  }

  async function loadLogs() {
    const data = await api('/api/admin/logs', { limit: 50 });
    if (data.error) return;
    const tbody = document.getElementById('logs-tbody');
    if (!data.logs || data.logs.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" class="empty-state">暂无日志</td></tr>';
      return;
    }
    tbody.innerHTML = data.logs.map(l => '<tr>' +
      '<td>' + formatDate(l.created_at) + '</td>' +
      '<td class="key-cell">' + (l.key_prefix || '-') + '</td>' +
      '<td>' + l.action + '</td>' +
      '<td>' + (l.ip_address || '-') + '</td>' +
      '<td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + (l.details || '-') + '</td>' +
    '</tr>').join('');
  }

  function debounceLoadKeys() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => { currentPage = 0; loadKeys(); }, 300);
  }

  async function generateKeys() {
    const count = parseInt(document.getElementById('gen-count').value) || 1;
    const notes = document.getElementById('gen-notes').value;
    const data = await api('/api/admin/create', { count, notes });
    if (data.error) { showToast(data.error, true); return; }
    showToast(data.message);
    const box = document.getElementById('generated-keys-box');
    const textarea = document.getElementById('generated-keys-text');
    textarea.value = data.keys.join('\\n');
    box.classList.add('visible');
    loadStats(); loadKeys();
  }

  function copyGeneratedKeys() {
    const textarea = document.getElementById('generated-keys-text');
    textarea.select();
    document.execCommand('copy');
    showToast('已复制到剪贴板');
  }

  function downloadKeys() {
    const text = document.getElementById('generated-keys-text').value;
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'license_keys_' + new Date().toISOString().slice(0, 10) + '.txt';
    a.click();
    URL.revokeObjectURL(url);
  }

  async function revokeKey(prefix) {
    // Need the full key - prompt user
    const fullKey = prompt('请输入完整卡密以确认禁用:\\n(前缀: ' + prefix + ')');
    if (!fullKey) return;
    const data = await api('/api/admin/revoke', { key: fullKey });
    if (data.error) showToast(data.error, true);
    else { showToast(data.message); loadStats(); loadKeys(); }
  }

  async function unrevokeKey(prefix) {
    const fullKey = prompt('请输入完整卡密以恢复:\\n(前缀: ' + prefix + ')');
    if (!fullKey) return;
    const data = await api('/api/admin/unrevoke', { key: fullKey });
    if (data.error) showToast(data.error, true);
    else { showToast(data.message); loadStats(); loadKeys(); }
  }

  async function resetKey(prefix) {
    const fullKey = prompt('请输入完整卡密以解绑:\\n(前缀: ' + prefix + ')');
    if (!fullKey) return;
    const data = await api('/api/admin/reset', { key: fullKey });
    if (data.error) showToast(data.error, true);
    else { showToast(data.message); loadStats(); loadKeys(); }
  }

  async function deleteKey(prefix) {
    const fullKey = prompt('请输入完整卡密以删除 (此操作不可恢复!):\\n(前缀: ' + prefix + ')');
    if (!fullKey) return;
    if (!confirm('确定要永久删除该卡密吗？')) return;
    const data = await api('/api/admin/delete', { key: fullKey });
    if (data.error) showToast(data.error, true);
    else { showToast(data.message); loadStats(); loadKeys(); }
  }

  async function quickVerify() {
    const key = document.getElementById('quick-verify-input').value.trim();
    if (!key) return;
    const res = await fetch(API_BASE + '/api/verify', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key }),
    });
    const data = await res.json();
    const el = document.getElementById('verify-result');
    el.classList.add('visible');
    if (data.valid) {
      el.className = 'verify-result visible success';
      el.innerHTML = '&#10003; 卡密有效 | 前缀: ' + data.key_prefix + ' | 状态: ' + data.status + (data.bound ? ' | 已绑定设备' : ' | 未绑定');
    } else {
      el.className = 'verify-result visible fail';
      el.innerHTML = '&#10007; ' + data.message + ' (状态: ' + data.status + ')';
    }
  }

  // Init
  window.addEventListener('DOMContentLoaded', () => {
    loadStats();
    loadKeys();
    loadLogs();
  });
</script>
</body>
</html>`;
}

// ============================================================
// Router
// ============================================================

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;
    const method = request.method;
    const ip = request.headers.get('CF-Connecting-IP') || '';

    // Handle CORS preflight
    if (method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: corsHeaders() });
    }

    // ---- Admin Panel ----
    if (path === '/admin' || path === '/') {
      return new Response(adminHTML(), {
        headers: { 'Content-Type': 'text/html; charset=utf-8', ...corsHeaders() },
      });
    }

    // ---- Public API ----
    if (path === '/api/verify' && method === 'POST') {
      try {
        const body = await request.json();
        return await handleVerify(env.DB, body, ip);
      } catch (e) {
        return error('Invalid request: ' + e.message, 400);
      }
    }

    if (path === '/api/activate' && method === 'POST') {
      try {
        const body = await request.json();
        return await handleActivate(env.DB, body, ip);
      } catch (e) {
        return error('Invalid request: ' + e.message, 400);
      }
    }

    // ---- Admin API (protected) ----
    if (path.startsWith('/api/admin/') && method === 'POST') {
      if (!checkAdmin(request, env)) {
        return error('未授权访问', 401);
      }

      const adminRoute = path.replace('/api/admin/', '');
      let body = {};
      try {
        if (method === 'POST') {
          const text = await request.text();
          if (text) body = JSON.parse(text);
        }
      } catch (e) {
        return error('Invalid JSON: ' + e.message, 400);
      }

      switch (adminRoute) {
        case 'keys':   return handleAdminKeys(env.DB, body);
        case 'create': return handleAdminCreate(env.DB, body);
        case 'revoke': return handleAdminRevoke(env.DB, body);
        case 'unrevoke': return handleAdminUnrevoke(env.DB, body);
        case 'reset':  return handleAdminReset(env.DB, body);
        case 'delete': return handleAdminDelete(env.DB, body);
        case 'stats':  return handleAdminStats(env.DB);
        case 'logs':   return handleAdminLogs(env.DB, body);
        default:       return error('Unknown admin endpoint', 404);
      }
    }

    // ---- 404 ----
    return error('Not found', 404);
  },
};


