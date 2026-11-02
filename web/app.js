'use strict';
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const DEFAULTS = { tab: 'shot', text: '23 JORDAN', size: 64, spacing: 0, fill: '#111111', stroke: '#ffffff', sw: 0,
  bg: '#ffffff', cols: 3, hideMissing: true, hideSystem: true, favOnly: false, onePerFamily: false, q: '', tags: [],
  sort: 'name', cmp: [] };
const S = Object.assign({}, DEFAULTS, JSON.parse(localStorage.getItem('ff_state') || '{}'));
const save = () => localStorage.setItem('ff_state', JSON.stringify(S));

const TAG_GROUPS = [
  ['字符', ['含数字', '中文(完整)', '中文(部分)', '仅大写', '无拉丁大写']],
  ['粗细', ['细', '常规', '粗', '特粗']],
  ['宽窄', ['窄体', '宽体']],
  ['风格', ['无衬线', '衬线', '手写/书法', '装饰', '等宽', '斜体', '运动风', '符号']],
  ['用途', ['适合号码']],
  ['授权', ['开源可商用', 'Google可商用', '免费可商用(dafont)', '⚠个人使用', '仅供识别']],
  ['来源', ['系统字体', 'dafont']],
];
const DAFONT_SUGGEST = { '手写/书法': [603, 601, 604], '装饰': [112, 101, 115], '衬线': [502], '无衬线': [501],
  '适合号码': [501, 104], '运动风': [713, 104], '等宽': [503], '窄体': [501] };

let FACES = [], BYID = {}, USER = { fav: {}, tags: {}, removed: {} }, VERSION = 0, LOCAL = true, USED = {};

// ------------------------------------------------------------------ helpers
function toast(msg, ms = 2600) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('on');
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('on'), ms);
}
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok && !j.error) j.error = `请求失败 (${r.status})`;
  return j;
}
function hash(s) { let h = 5381; for (const c of s) h = (h * 33 + c.codePointAt(0)) >>> 0; return h.toString(36); }
function uniqChars(s) { return [...new Set([...s])].join(''); }
function missingChars(f, text) {
  const r = f.ranges, out = [];
  for (const ch of new Set([...text])) {
    if (/\s/.test(ch)) continue;
    const cp = ch.codePointAt(0);
    let lo = 0, hi = r.length - 1, ok = false;
    while (lo <= hi) {
      const m = (lo + hi) >> 1;
      if (cp < r[m][0]) hi = m - 1; else if (cp > r[m][1]) lo = m + 1; else { ok = true; break; }
    }
    if (!ok) out.push(ch);
  }
  return out;
}
function faceTags(f) {
  const rm = new Set(USER.removed[f.id] || []);
  return [...f.tags.filter(t => !rm.has(t)), ...(USER.tags[f.id] || [])];
}
const displayName = f => `${f.family} ${f.style}`.trim();
const saveUser = () => api('/api/user', USER);

// ------------------------------------------------------------------ font loading
const loaded = {};
function familyFor(f, text) { return f.big ? `ffs_${f.id}_${hash(uniqChars(text))}` : `ff_${f.id}`; }
function loadFont(f, text) {
  const fam = familyFor(f, text);
  if (!loaded[fam]) {
    const url = f.big ? `/font/${f.id}?text=${encodeURIComponent(uniqChars(text))}` : `/font/${f.id}`;
    loaded[fam] = new FontFace(fam, `url("${url}")`).load().then(ff => { document.fonts.add(ff); return fam; });
  }
  return loaded[fam];
}
function applyFont(card) {
  const f = card._face, text = card._text;
  if (f.ref) {  // recognition-only: no font file, preview is composed server-side from glyph bitmaps
    if (!card._fallback) { card._span.hidden = true; card._fallback = true; setFallback(card); }
    return;
  }
  if (card._applied === familyFor(f, text)) return;
  card.classList.add('loading');
  loadFont(f, text).then(fam => {
    if (familyFor(f, card._text) !== fam) return;
    card._span.style.fontFamily = `"${fam}", "Adobe Blank", sans-serif`;
    card._applied = fam;
    card.classList.remove('loading');
  }).catch(() => {
    card.classList.remove('loading');
    card._span.hidden = true;
    card._fallback = true;
    setFallback(card);
    card._applied = familyFor(f, text);
  });
}
function setFallback(card) {
  let fb = card.querySelector('.sample .fb');
  if (!fb) {
    fb = document.createElement('div'); fb.className = 'fb';
    fb.title = '浏览器无法加载这个字体，改用服务端渲染的图片预览';
    card.querySelector('.sample').appendChild(fb);
  }
  const src = `/render/${card._face.id}?text=${encodeURIComponent(card._text)}&size=96`;
  const probe = new Image();
  probe.onload = () => {
    fb.style.setProperty('--ar', probe.naturalWidth / probe.naturalHeight);
    fb.style.webkitMaskImage = fb.style.maskImage = `url("${src}")`;
  };
  probe.src = src;
}
const visible = new Set();
const io = new IntersectionObserver(entries => {
  for (const e of entries) {
    if (e.isIntersecting) { visible.add(e.target); applyFont(e.target); } else visible.delete(e.target);
  }
}, { rootMargin: '400px' });

// ------------------------------------------------------------------ cards
function makeCard(f, text) {
  const card = $('#tplCard').content.firstElementChild.cloneNode(true);
  card._face = f; card._span = card.querySelector('.txt');
  card.querySelector('.name').textContent = displayName(f);
  card.querySelector('.name').title = f.full;
  setCardText(card, text);
  updateCardMeta(card);
  card.querySelector('.acts').addEventListener('click', e => {
    const a = e.target.dataset.a; if (a) cardAction(a, f, card);
  });
  io.observe(card);
  return card;
}
function setCardText(card, text) {
  card._text = text;
  card._span.textContent = text;
  const miss = missingChars(card._face, text);
  card.classList.toggle('missing', miss.length > 0);
  card.querySelector('.miss').textContent = miss.length ? `缺字：${miss.slice(0, 12).join(' ')}` : '';
  if (card._fallback) setFallback(card);
  else if (card._face.big && visible.has(card)) scheduleBig(card);
}
let bigTimer, bigQueue = new Set();
function scheduleBig(card) {
  bigQueue.add(card); clearTimeout(bigTimer);
  bigTimer = setTimeout(() => { bigQueue.forEach(applyFont); bigQueue.clear(); }, 350);
}
function updateCardMeta(card) {
  const f = card._face, tags = faceTags(f);
  const warn = (f.ref ? '<span class="warn">仅供识别 · 需自行获取授权 · </span>' : '') +
    (tags.includes('⚠个人使用') ? '<span class="warn">⚠个人使用 · </span>' : '');
  const used = USED[f.id] ? ` · 选用 ${USED[f.id]} 次` : '';
  card.querySelector('.sub').innerHTML = warn + esc(tags.filter(t => t !== '⚠个人使用' && t !== '仅供识别').slice(0, 6).join(' · ')) + used;
  const fav = card.querySelector('[data-a=fav]');
  fav.textContent = USER.fav[f.id] ? '★' : '☆'; fav.classList.toggle('on', !!USER.fav[f.id]);
  card.querySelector('[data-a=cmp]').classList.toggle('on', S.cmp.includes(f.id));
  if (!LOCAL || f.ref) card.querySelectorAll('[data-a=install],[data-a=reveal]').forEach(b => b.hidden = true);
  card.querySelector('[data-a=get]').hidden = !f.ref;
}
function refreshMeta(fid) {
  $$('.card').forEach(c => { if (!fid || c._face.id === fid) updateCardMeta(c); });
}

async function cardAction(a, f, card) {
  const hid = card.closest('#matchGrid') ? CUR.hid : null;
  if (a === 'fav') {
    if (USER.fav[f.id]) delete USER.fav[f.id]; else USER.fav[f.id] = 1;
    saveUser(); refreshMeta(f.id); if (S.favOnly) renderWall();
    if (hid && USER.fav[f.id]) api('/api/choose', { hid, id: f.id, action: '收藏' });
  } else if (a === 'cmp') {
    const i = S.cmp.indexOf(f.id);
    if (i >= 0) S.cmp.splice(i, 1); else if (S.cmp.length >= 6) return toast('最多对比 6 个字体'); else S.cmp.push(f.id);
    save(); refreshMeta(f.id); renderCmpBar();
  } else if (a === 'copy') {
    await navigator.clipboard.writeText(displayName(f)).catch(() => {});
    toast(`已复制：${displayName(f)}（PostScript 名：${f.ps || '无'}）`);
    if (hid) api('/api/choose', { hid, id: f.id, action: '复制名' });
  } else if (a === 'install') {
    const r = await api('/api/install', { id: f.id, hid });
    toast(r.msg || r.error);
    if (r.ok && faceTags(f).includes('⚠个人使用')) setTimeout(() => toast('⚠ 这个字体标注为仅限个人使用，用于定制商品前请先购买商用授权', 5000), 2700);
  } else if (a === 'reveal') {
    api('/api/reveal', { id: f.id });
  } else if (a === 'get') {
    const url = f.dafont && f.dafont.url ? f.dafont.url : `https://www.google.com/search?q=${encodeURIComponent('"' + f.family + '" font')}`;
    window.open(url, '_blank');
    if (hid) api('/api/choose', { hid, id: f.id, action: '去获取' });
  } else if (a === 'info') {
    showInfo(f);
  }
}

// ------------------------------------------------------------------ wall
const wallCards = {};
function applyVars(el, o) {
  el.style.setProperty('--size', o.size + 'px');
  el.style.setProperty('--ls', o.spacing + 'px');
  el.style.setProperty('--fill', o.fill);
  el.style.setProperty('--stroke', o.stroke);
  el.style.setProperty('--sw', o.sw + 'px');
  el.style.setProperty('--bg', o.bg);
  el.style.setProperty('--cols', o.cols);
}
function filteredFaces() {
  const text = S.text || 'Aa', q = S.q.trim().toLowerCase();
  const groups = {};
  for (const t of S.tags) {
    const g = (TAG_GROUPS.find(([, ts]) => ts.includes(t)) || [t.startsWith('dafont:') ? 'dafont分类' : '我的标签'])[0];
    (groups[g] = groups[g] || []).push(t);
  }
  let list = FACES.filter(f => {
    const tags = faceTags(f);
    if (S.hideSystem && tags.includes('系统字体') && !S.tags.includes('系统字体')) return false;
    if (S.favOnly && !USER.fav[f.id]) return false;
    if (q && !`${f.family} ${f.style} ${f.full} ${f.ps}`.toLowerCase().includes(q)) return false;
    for (const ts of Object.values(groups)) if (!ts.some(t => tags.includes(t))) return false;
    if (S.hideMissing && missingChars(f, text).length) return false;
    return true;
  });
  const key = {
    name: f => displayName(f).toLowerCase(), ink: f => -(f.ink || 0), aspect: f => f.aspect || 9,
    added: f => -f.added, used: f => -(USED[f.id] || 0),
  }[S.sort];
  list.sort((a, b) => { const x = key(a), y = key(b); return x < y ? -1 : x > y ? 1 : 0; });
  if (S.onePerFamily) {
    const seen = new Map();
    for (const f of list) {
      const k = f.family.toLowerCase(), cur = seen.get(k);
      if (!cur || (/^regular$/i.test(f.style) && !/^regular$/i.test(cur.style))) seen.set(k, f);
    }
    const keep = new Set(seen.values());
    list = list.filter(f => keep.has(f));
  }
  return list;
}
function renderWall() {
  const list = filteredFaces(), wall = $('#wall'), text = S.text || 'Aa';
  const frag = document.createDocumentFragment();
  for (const f of list) {
    let c = wallCards[f.id];
    if (!c) c = wallCards[f.id] = makeCard(f, text);
    else if (c._text !== text) setCardText(c, text);
    frag.appendChild(c);
  }
  wall.replaceChildren(frag);
  $('#count').textContent = `显示 ${list.length} / ${FACES.length} 个字形`;
  renderTagFilters();
}
function renderTagFilters() {
  const counts = {};
  for (const f of FACES) for (const t of faceTags(f)) counts[t] = (counts[t] || 0) + 1;
  const custom = Object.keys(counts).filter(t => !TAG_GROUPS.some(([, ts]) => ts.includes(t)));
  const groups = [...TAG_GROUPS, ['dafont分类', custom.filter(t => t.startsWith('dafont:'))],
    ['我的标签', custom.filter(t => !t.startsWith('dafont:'))]];
  $('#tagFilters').innerHTML = groups.filter(([, ts]) => ts.some(t => counts[t])).map(([g, ts]) =>
    `<div class="tg"><b>${esc(g)}</b>${ts.filter(t => counts[t]).map(t =>
      `<span class="chip${S.tags.includes(t) ? ' on' : ''}" data-tag="${esc(t)}">${esc(t.replace('dafont:', ''))}<span class="n">${counts[t]}</span></span>`).join('')}</div>`).join('');
}

// ------------------------------------------------------------------ toolbar
function bindToolbar() {
  const ids = ['text', 'size', 'spacing', 'fill', 'stroke', 'sw', 'bg', 'cols'];
  for (const id of ids) {
    const el = $('#' + id); el.value = S[id];
    el.addEventListener('input', () => {
      S[id] = ['size', 'spacing', 'sw', 'cols'].includes(id) ? +el.value : el.value;
      save(); applyVars($('#wall'), S);
      if (id === 'text') onTextChange();
      if (!$('#useQColors').checked || !CUR.colors) applyVars($('#matchGrid'), Object.assign({}, S));
      else applyVars($('#matchGrid'), Object.assign({}, S, matchColors()));
    });
  }
  $$('.presets button').forEach(b => b.onclick = () => { $('#text').value = S.text = b.dataset.preset; save(); onTextChange(); });
  applyVars($('#wall'), S); applyVars($('#matchGrid'), S);
}
let textTimer;
function onTextChange() {
  clearTimeout(textTimer);
  textTimer = setTimeout(() => {
    if (S.tab === 'wall') renderWall();
    else Object.values(wallCards).forEach(c => c._text !== S.text && setCardText(c, S.text || 'Aa'));
  }, 120);
}
function bindSidebar() {
  for (const id of ['hideMissing', 'hideSystem', 'favOnly', 'onePerFamily']) {
    const el = $('#' + id); el.checked = S[id];
    el.onchange = () => { S[id] = el.checked; save(); renderWall(); };
  }
  $('#q').value = S.q; $('#q').oninput = e => { S.q = e.target.value; save(); renderWall(); };
  $('#sort').value = S.sort; $('#sort').onchange = e => { S.sort = e.target.value; save(); renderWall(); };
  $('#tagFilters').onclick = e => {
    const c = e.target.closest('.chip'); if (!c) return;
    const t = c.dataset.tag, i = S.tags.indexOf(t);
    if (i >= 0) S.tags.splice(i, 1); else S.tags.push(t);
    save(); renderWall();
  };
  $('#btnClearTags').onclick = () => { S.tags = []; save(); renderWall(); };
}

// ------------------------------------------------------------------ tabs
function showTab(t) {
  S.tab = t; save();
  $$('nav button').forEach(b => b.classList.toggle('on', b.dataset.tab === t));
  $$('.tab').forEach(s => s.classList.toggle('on', s.id === 'tab-' + t));
  $('#toolbar').hidden = t === 'hist';
  if (t === 'wall') renderWall();
  if (t === 'hist') renderHistory();
}

// ------------------------------------------------------------------ compare & info
function renderCmpBar() {
  S.cmp = S.cmp.filter(id => BYID[id]);
  $('#cmpBar').hidden = !S.cmp.length;
  $('#cmpChips').innerHTML = S.cmp.map(id => `<span class="chip">${esc(displayName(BYID[id]))}</span>`).join('');
}
function openModal(html) { $('#modalBody').innerHTML = html; $('#modal').hidden = false; }
$('#modal').addEventListener('click', e => { if (e.target.id === 'modal' || e.target.classList.contains('close')) $('#modal').hidden = true; });
document.addEventListener('keydown', e => { if (e.key === 'Escape') $('#modal').hidden = true; });

function showCompare() {
  const text = (S.tab === 'shot' && $('#qtext').value) || S.text || 'Aa';
  openModal(`<h3>并排对比</h3><div id="cmpList" class="grid" style="--cols:1"></div>`);
  const box = $('#cmpList'); applyVars(box, Object.assign({}, S, { size: Math.max(S.size, 90), cols: 1 }));
  for (const id of S.cmp) box.appendChild(makeCard(BYID[id], text));
}
function showInfo(f) {
  const tags = faceTags(f), df = f.dafont;
  openModal(`<h3>${esc(displayName(f))}</h3>
    <div id="infoCard" class="grid" style="--cols:1"></div>
    <h4>标签 <span class="muted">（点 × 去掉自动标签；可添加自定义标签，如“球衣常用”“客户A指定”）</span></h4>
    <div id="infoTags">${tags.map(t => `<span class="chip on">${esc(t)} <b data-rm="${esc(t)}">×</b></span>`).join('')}</div>
    <input id="newTag" placeholder="新标签，回车添加"> 
    <h4>信息</h4><div class="kv">
      <b>全名</b><span>${esc(f.full)}</span><b>PostScript</b><span>${esc(f.ps)}</span>
      <b>设计者</b><span>${esc(f.designer || f.vendor)}</span><b>版本</b><span>${esc(f.version)}</span>
      ${df ? `<b>dafont</b><span><a href="${esc(df.url)}" target="_blank">${esc(df.name)}</a> · ${esc(df.author)} · ${esc((df.cats || []).join('/'))}</span><b>dafont 授权</b><span>${esc(df.license)}</span>` : ''}
      <b>授权说明</b><span>${esc(f.license || '（字体内未写明）')} ${f.license_url ? `<a href="${esc(f.license_url)}" target="_blank">链接</a>` : ''}</span>
      <b>版权</b><span>${esc(f.copyright)}</span>
      <b>文件</b><span>${f.ref ? '未附带字体文件（仅供识别）：商用前请向作者或字体公司购买授权，系统字体请使用自己电脑上已授权的版本' : `${esc(f.path)}（${(f.size / 1024).toFixed(0)} KB）`}</span>
      ${f.dups.length ? `<b>重复文件</b><span>${f.dups.map(esc).join('<br>')}</span>` : ''}
    </div>`);
  const box = $('#infoCard'); applyVars(box, Object.assign({}, S, { cols: 1, size: Math.max(S.size, 80) }));
  box.appendChild(makeCard(f, S.text || 'Aa'));
  $('#infoTags').onclick = e => {
    const t = e.target.dataset.rm; if (!t) return;
    const mine = USER.tags[f.id] || [];
    if (mine.includes(t)) USER.tags[f.id] = mine.filter(x => x !== t);
    else (USER.removed[f.id] = USER.removed[f.id] || []).push(t);
    saveUser(); refreshMeta(f.id); showInfo(f);
  };
  $('#newTag').onkeydown = e => {
    const t = e.target.value.trim(); if (e.key !== 'Enter' || !t) return;
    (USER.tags[f.id] = USER.tags[f.id] || []).push(t);
    if (USER.removed[f.id]) USER.removed[f.id] = USER.removed[f.id].filter(x => x !== t);
    saveUser(); refreshMeta(f.id); showInfo(f);
  };
}

// ------------------------------------------------------------------ screenshot search
const CUR = { img: null, sel: null, hid: null, colors: null, results: [], text: '' };
function loadImageFile(blob) {
  const url = URL.createObjectURL(blob), img = new Image();
  img.onload = () => startShot(img);
  img.src = url;
}
async function startShot(img) {
  showTab('shot');
  CUR.img = img; CUR.hid = null;
  $('#drop').hidden = true; $('#shotWork').hidden = false; $('#online').hidden = true;
  $('#matchGrid').replaceChildren(); $('#matchInfo').textContent = '';
  const cv = $('#cv'), maxW = Math.min(820, $('.shotLeft').clientWidth || 820), maxH = 420;
  const k = Math.min(1, maxW / img.width, maxH / img.height);
  cv.width = Math.round(img.width * k); cv.height = Math.round(img.height * k); cv._k = k;
  cv.getContext('2d').drawImage(img, 0, 0, cv.width, cv.height);
  setSel([0, 0, img.width, img.height]);
  $('#ocrInfo').textContent = '正在识别文字…';
  const r = await api('/api/ocr', { image: cropURL([0, 0, img.width, img.height]) });
  const items = (r.items || []).filter(i => i.text.trim());
  if (items.length) {
    const best = items.reduce((a, b) => (b.box[2] * b.box[3] > a.box[2] * a.box[3] ? b : a));
    const [x, y, w, h] = best.box, p = h * 0.15;
    setSel([x - p, y - p, w + 2 * p, h + 2 * p]);
    $('#qtext').value = best.text.trim();
    $('#ocrInfo').textContent = `识别到 ${items.length} 处文字，已自动框选最大的一处` + (items.length > 1 ? '（其他：' + items.filter(i => i !== best).map(i => i.text).slice(0, 4).join('、') + '）' : '');
  } else {
    $('#qtext').value = '';
    $('#ocrInfo').textContent = '没有识别出文字：可以手动填写，或留空让程序按字形猜。建议在图上框选文字区域。';
  }
  findLocal();
}
function setSel(b) {
  const img = CUR.img;
  let [x, y, w, h] = b;
  x = Math.max(0, x); y = Math.max(0, y); w = Math.min(img.width - x, w); h = Math.min(img.height - y, h);
  CUR.sel = [x, y, w, h];
  const k = $('#cv')._k, s = $('#sel');
  Object.assign(s.style, { left: x * k + 'px', top: y * k + 'px', width: w * k + 'px', height: h * k + 'px' });
}
function cropURL(b) {
  const [x, y, w, h] = b.map(Math.round), c = document.createElement('canvas');
  c.width = Math.max(1, w); c.height = Math.max(1, h);
  c.getContext('2d').drawImage(CUR.img, x, y, w, h, 0, 0, w, h);
  return c.toDataURL('image/png');
}
function bindCropper() {
  const wrap = $('#cvWrap'); let start = null;
  wrap.addEventListener('mousedown', e => {
    const r = $('#cv').getBoundingClientRect(), k = $('#cv')._k;
    start = [(e.clientX - r.left) / k, (e.clientY - r.top) / k];
  });
  window.addEventListener('mousemove', e => {
    if (!start) return;
    const r = $('#cv').getBoundingClientRect(), k = $('#cv')._k;
    const x = (e.clientX - r.left) / k, y = (e.clientY - r.top) / k;
    setSel([Math.min(x, start[0]), Math.min(y, start[1]), Math.abs(x - start[0]), Math.abs(y - start[1])]);
  });
  window.addEventListener('mouseup', async () => {
    if (!start) return; start = null;
    if (CUR.sel[2] < 6 || CUR.sel[3] < 6) return;
    $('#ocrInfo').textContent = '正在识别框选区域…';
    const r = await api('/api/ocr', { image: cropURL(CUR.sel) });
    const t = (r.items || []).map(i => i.text).join(' ').trim();
    if (t) $('#qtext').value = t;
    $('#ocrInfo').textContent = t ? `框选区域识别为“${t}”` : '框选区域没识别出文字，保留原来的文字';
    findLocal();
  });
}
function matchColors() {
  const c = CUR.colors; if (!c) return {};
  const o = { fill: c.fill, bg: c.bg, stroke: c.outline || S.stroke, sw: 0 };
  if (c.outline) o.sw = Math.max(1, Math.round(c.outline_rel * S.size * 0.7));
  return o;
}
const BAND_NAME = { high: '很像', mid: '可能', low: '不像' };
async function findLocal() {
  const btn = $('#btnFind'); btn.disabled = true; btn.textContent = '查找中…';
  const text = $('#qtext').value.trim();
  const r = await api('/api/match', { image: cropURL(CUR.sel), text });
  btn.disabled = false; btn.textContent = '在本地字体库查找';
  if (r.error) { $('#matchInfo').textContent = r.error; return; }
  Object.assign(CUR, { hid: r.hid, colors: r.colors, results: r.results, text: text || r.guess, clean: r.clean });
  $('#maskImg').src = r.mask;
  $('#cleanImg').src = r.clean;
  const c = r.colors;
  $('#qcolors').innerHTML = `背景<span style="background:${c.bg}"></span>文字<span style="background:${c.fill}"></span>` +
    (c.outline ? `描边<span style="background:${c.outline}"></span>` : '');
  const shown = CUR.text || 'Aa';
  const topBand = r.results.length ? r.results[0].band : 'low';
  $('#matchInfo').innerHTML = `在 <b>${r.candidates}</b> 个${text ? '能写出“' + esc(text) + '”的' : ''}本地字体里，最像的 ${r.results.length} 个如下` +
    (!text && r.guess ? `（未提供文字，按字形猜测为“${esc(r.guess)}”）` : '') +
    `。都不像？<button class="link" id="goOnline">去网上找 ↓</button>` +
    (topBand === 'low' ? '<div class="hint low">最高分偏低：本地库里很可能没有这个字体，建议直接去网上找。</div>'
      : topBand === 'mid' ? '<div class="hint mid">没有特别吻合的结果，请目视核对；不像就去网上找。</div>' : '');
  $('#goOnline').onclick = () => $('#online').scrollIntoView({ behavior: 'smooth' });
  const grid = $('#matchGrid');
  applyVars(grid, Object.assign({}, S, $('#useQColors').checked ? matchColors() : {}));
  grid.replaceChildren(...r.results.map((x, i) => {
    const card = makeCard(BYID[x.id], shown);
    const rk = card.querySelector('.rank');
    rk.textContent = `#${i + 1} · ${BAND_NAME[x.band]} ${Math.max(0, x.score * 100).toFixed(0)}`;
    rk.classList.add(x.band);
    return card;
  }));
  setupOnline(r.results);
}

// ------------------------------------------------------------------ online
let CATS = [], ONLINE_SEL = new Set();
async function setupOnline(results) {
  if (!CATS.length) CATS = await api('/api/dafont/cats');
  const votes = {};
  for (const x of results.slice(0, 5)) for (const t of faceTags(BYID[x.id])) {
    for (const c of DAFONT_SUGGEST[t] || []) votes[c] = (votes[c] || 0) + 1;
    if (t.startsWith('dafont:')) { const c = CATS.find(c => c.name === t.slice(7)); if (c) votes[c.id] = (votes[c.id] || 0) + 3; }
  }
  const top = Object.entries(votes).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([c]) => +c);
  ONLINE_SEL = new Set(top.length ? top : [501, 104, 112]);
  const groups = {};
  for (const c of CATS) (groups[c.group] = groups[c.group] || []).push(c);
  $('#catChips').innerHTML = Object.entries(groups).map(([g, cs]) => `<span class="grp"><b>${g}</b>${cs.map(c =>
    `<span class="chip${ONLINE_SEL.has(c.id) ? ' on' : ''}" data-cat="${c.id}">${c.name}</span>`).join('')}</span>`).join('');
  $('#online').hidden = false;
  updateDafontLink();
}
function updateDafontLink() {
  const c = [...ONLINE_SEL][0] || 501;
  $('#lnkDafont').href = `https://www.dafont.com/theme.php?cat=${c}&text=${encodeURIComponent($('#qtext').value || CUR.text || '')}`;
}
$('#catChips').addEventListener('click', e => {
  const c = e.target.closest('.chip'); if (!c) return;
  const id = +c.dataset.cat;
  if (ONLINE_SEL.has(id)) ONLINE_SEL.delete(id); else if (ONLINE_SEL.size >= 6) return toast('最多选 6 个分类'); else ONLINE_SEL.add(id);
  c.classList.toggle('on', ONLINE_SEL.has(id)); updateDafontLink();
});
async function cleanBlob() { return (await fetch(CUR.clean)).blob(); }
function saveClean() {
  const a = document.createElement('a');
  a.href = CUR.clean; a.download = `字形-${(CUR.text || 'text').replace(/[^\w\u4e00-\u9fff-]+/g, '_')}.png`; a.click();
}
document.querySelectorAll('#online [data-site]').forEach(btn => btn.onclick = async () => {
  let copied = false;
  try {  // clipboard needs a secure context (localhost); LAN access over http falls back to saving the file
    await navigator.clipboard.write([new ClipboardItem({ 'image/png': cleanBlob() })]);  // promise form keeps Safari's user activation
    copied = true;
  } catch (e) { saveClean(); }
  window.open(btn.dataset.site, '_blank');
  toast(copied ? '图片已复制：在网站的上传区按 ⌘V 粘贴' : '没能复制到剪贴板，已保存图片，请在网站上上传这张图', 5000);
});
$('#btnSaveClean').onclick = saveClean;

// ------------------------------------------------------------------ history
async function renderHistory() {
  const list = await api('/api/history');
  $('#histList').innerHTML = list.length ? list.map(h => {
    const d = new Date(h.ts * 1000).toLocaleString('zh-CN');
    const top = h.top.slice(0, 3).map(id => BYID[id] ? displayName(BYID[id]) : '(已删除)').join('、');
    const chosen = h.chosen.map(c => `${c.action}：${c.label || (BYID[c.id] ? displayName(BYID[c.id]) : c.id)}`).join('；');
    return `<div class="h"><img src="/hist/${h.id}.png" data-hid="${h.id}" title="点击重新查找">
      <div><div><b>${esc(h.text || h.guess || '（无文字）')}</b> <span class="muted">${d}</span></div>
      <div class="muted">本地前三：${esc(top)}</div><div>${chosen ? '✅ ' + esc(chosen) : '<span class="muted">未选用</span>'}</div></div></div>`;
  }).join('') : '<div class="muted">还没有截图检索记录</div>';
}
$('#histList').addEventListener('click', async e => {
  const hid = e.target.dataset.hid; if (!hid) return;
  loadImageFile(await (await fetch(`/hist/${hid}.png`)).blob());
});

// ------------------------------------------------------------------ input wiring
document.addEventListener('paste', e => {
  const item = [...(e.clipboardData?.items || [])].find(i => i.type.startsWith('image/'));
  if (item) { e.preventDefault(); loadImageFile(item.getAsFile()); }
});
const drop = $('#drop');
['dragover', 'dragenter'].forEach(t => document.addEventListener(t, e => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach(t => document.addEventListener(t, e => { e.preventDefault(); drop.classList.remove('over'); }));
document.addEventListener('drop', e => { const f = e.dataTransfer.files[0]; if (f && f.type.startsWith('image/')) loadImageFile(f); });
$('#file').onchange = e => e.target.files[0] && loadImageFile(e.target.files[0]);
$('#btnFind').onclick = findLocal;
$('#qtext').onkeydown = e => { if (e.key === 'Enter') findLocal(); };
$('#btnNewShot').onclick = () => { $('#drop').hidden = false; $('#shotWork').hidden = true; $('#matchGrid').replaceChildren(); $('#matchInfo').textContent = ''; $('#online').hidden = true; };
$('#useQColors').onchange = e => applyVars($('#matchGrid'), Object.assign({}, S, e.target.checked ? matchColors() : {}));
$('#btnCmp').onclick = showCompare;
$('#btnCmpClear').onclick = () => { S.cmp = []; save(); renderCmpBar(); refreshMeta(); };
$$('nav button').forEach(b => b.onclick = () => showTab(b.dataset.tab));

// ------------------------------------------------------------------ boot
async function loadFonts() {
  const [d, user, hist] = await Promise.all([api('/api/fonts'), api('/api/user'), api('/api/history')]);
  FACES = d.faces; BYID = Object.fromEntries(FACES.map(f => [f.id, f])); VERSION = d.version;
  USER = Object.assign({ fav: {}, tags: {}, removed: {} }, user);
  USED = {};
  for (const h of hist) for (const c of h.chosen) if (c.id) USED[c.id] = (USED[c.id] || 0) + 1;
  for (const k of Object.keys(wallCards)) { io.unobserve(wallCards[k]); delete wallCards[k]; }
  const s = d.stats || {};
  const nref = FACES.filter(f => f.ref).length;
  $('#status').textContent = `${s.faces || FACES.length} 个字形` + (nref ? ` + ${nref} 个仅供识别` : '') +
    ` · ${s.families || ''} 个家族 · 已去重 ${s.duplicate_files || 0} 个重复文件`;
  if (S.tab === 'wall') renderWall();
  renderCmpBar();
}
async function pollStatus() {
  const st = await api('/api/status').catch(() => null);
  if (st) {
    LOCAL = st.local;
    if (st.version !== VERSION && st.status === 'ready') {
      await loadFonts();
      if (st.added > 0) toast(`字体库已更新：新增 ${st.added} 个字形`);
    }
  }
  setTimeout(pollStatus, 8000);
}
(async function boot() {
  bindToolbar(); bindSidebar(); bindCropper();
  await loadFonts();
  showTab(S.tab);
  pollStatus();
})();
