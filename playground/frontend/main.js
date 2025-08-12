let lastEventId = 0;
const seenIds = new Set();
const convPanels = new Map(); // convId -> { panel, consultsRoot, tab, sending, ... }
let activeConvId = null;
const closedConvs = new Set(); // 閉じた会話ID（以降のイベントは無視）
// タブの連番管理（タブ1, タブ2 ...）
let tabSerial = 0;
const tabIndexByConv = new Map();
const roleLabel = { 
  // 日本語の呼称
  idea_ai:'企画アドバイザー',
  writer_ai:'ライターAI',
  proof_ai:'校正AI',
  pm_ai:'全体進行（PM補助）',
  product_manager_ai:'プロダクト企画',
  project_manager_ai:'プロジェクト進行',
  architect_ai:'アーキテクト',
  dev_ai:'開発エンジニア',
  motivator_ai:'統括M'
};
// 追加カスタム職種の日本語名
Object.assign(roleLabel, {
  'cust_bce7cc85': 'CFO 財務責任者',
  'cust_biz_dev_manager': 'ビジネス開発マネージャー',
  'cust_sales_marketing': '営業・マーケティング担当',
  'cust_business_analyst': 'ビジネスアナリスト',
  'cust_market_research': '市場調査アナリスト',
  'cust_competitive_analyst': '競合分析スペシャリスト',
  'cust_financial_analyst': '財務アナリスト',
  'cust_uiux_designer': 'UI/UXデザイナー',
  'cust_legal_compliance': '法務・コンプライアンス担当',
  'cust_tech_lead': '技術リーダー/ソフトウェアアーキテクト',
});
const specialistRoles = new Set(['idea_ai','writer_ai','proof_ai','pm_ai']);

function displayName(role, roleId){
  if(role === 'user') return 'あなた';
  if(role === 'motivator_ai') return roleLabel.motivator_ai || '統括M';
  const id = roleId || role;
  return roleLabel[id] || id;
}

function el(tag, cls, text){ const e = document.createElement(tag); if(cls) e.className=cls; if(text!==undefined) e.textContent=text; return e; }
async function api(path, opts){ const base = { headers:{'Content-Type':'application/json'}, cache:'no-store' }; const merged = { ...base, ...(opts||{}) }; merged.headers = { ...(base.headers||{}), ...((opts&&opts.headers)||{}) }; const res = await fetch(path, merged); if(!res.ok) throw new Error(`HTTP ${res.status}`); return await res.json(); }

// 10文字/秒のスピードでタイプ表示する（時間ベースで滑らかに）
function typeTo(node, fullText, cps = 20, onTick){
  const text = String(fullText||'');
  const start = performance.now();
  const perCharMs = 1000 / Math.max(1, cps);
  let stopped = false;
  function frame(t){
    if(stopped) return;
    const elapsed = t - start;
    const chars = Math.floor(elapsed / perCharMs);
    const slice = Math.min(chars, text.length);
    node.textContent = text.slice(0, slice);
    if(typeof onTick === 'function') onTick();
    if(slice >= text.length){ return; }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
  return { stop(){ stopped = true; node.textContent = text; if(typeof onTick==='function') onTick(); } };
}

function ensureConsult(state, roleId){
  if(state.consults.has(roleId)) return state.consults.get(roleId);
  const wrap = el('div','consult');
  const head = el('header',null,`相談窓: ${roleLabel[roleId]||roleId}`);
  const stream = el('div','cstream');
  wrap.append(head, stream);
  // 会話ごとの右カラムルートに配置
  state.consultsRoot.append(wrap);
  const c = { wrap, stream, roleId };
  state.consults.set(roleId, c);
  return c;
}

function ensureTab(convId, label){
  const tabbar = document.getElementById('tabbar');
  if(!tabbar) return null;
  let tab = tabbar.querySelector(`[data-conv="${convId}"]`);
  if(tab){
    // 既存タブも期待表示に整える
    if(!tabIndexByConv.has(convId)) tabIndexByConv.set(convId, ++tabSerial);
    const expect = `タブ${tabIndexByConv.get(convId)}`;
    const ttl = tab.querySelector('.ttl');
    if(ttl && !/^タブ\d+/.test(ttl.textContent||'')) ttl.textContent = expect;
    return tab;
  }
  tab = document.createElement('button');
  tab.className = 'tab';
  tab.dataset.conv = convId;
  tab.title = convId;
  if(!tabIndexByConv.has(convId)) tabIndexByConv.set(convId, ++tabSerial);
  const tabText = `タブ${tabIndexByConv.get(convId)}`;
  tab.innerHTML = `<span class="ttl">${tabText}</span>`;
  const close = document.createElement('button');
  close.className = 'close';
  close.type = 'button';
  close.textContent = '×';
  close.addEventListener('click', (e)=>{ e.stopPropagation(); closeTab(convId); });
  tab.appendChild(close);
  tab.addEventListener('click', ()=> setActive(convId));
  tabbar.appendChild(tab);
  return tab;
}

function topicTitleFrom(text){
  const t = String(text||'').replace(/https?:\/\/\S+/g, '');
  const first = t.split(/\r?\n|。|！|!|？|\?|、/)[0].trim();
  const cleaned = first.replace(/[\[\]\(\)『』“”"'<>]/g,'').replace(/^[-*・●\d\.\)\(]+\s*/, '');
  const s = cleaned || t.trim();
  const max = 14; // 日本語14文字前後
  return s ? (s.length>max ? s.slice(0,max)+'…' : s) : '';
}

function setActive(convId){
  activeConvId = convId;
  // パネルの表示切替
  for(const [cid, st] of convPanels.entries()){
    const on = (cid === convId);
    st.panel.classList.toggle('hidden', !on);
    if(st.consultsRoot) st.consultsRoot.classList.toggle('hidden', !on);
  }
  // タブのactive切替
  const tabbar = document.getElementById('tabbar');
  if(tabbar){
    for(const t of tabbar.querySelectorAll('.tab')){
      t.classList.toggle('active', t.dataset.conv === convId);
    }
  }
}

function closeTab(convId){
  const st = convPanels.get(convId);
  if(!st) return;
  // DOM除去
  try{ st.panel.remove(); }catch{ }
  try{ st.consultsRoot.remove(); }catch{ }
  convPanels.delete(convId);
  // この会話の今後のイベントは無視（再出現防止）
  closedConvs.add(convId);
  // タブ除去
  const tabbar = document.getElementById('tabbar');
  const tab = tabbar && tabbar.querySelector(`[data-conv="${convId}"]`);
  if(tab) tab.remove();
  // アクティブ切替
  if(activeConvId === convId){
    const next = convPanels.keys().next();
    if(!next.done){ setActive(next.value); }
    else { newConversation(); }
  }
}

function ensurePanel(convId){
  // 閉じられた会話は再生成しない
  if(closedConvs.has(convId)) return { convId, panel: document.createElement('div'), consultsRoot: document.createElement('div'), streamMain: document.createElement('div'), consults:new Map(), inp: { value:'', focus(){}, }, btn: document.createElement('button'), titleEl: null, sending:false, customTitle:false, tab:null };
  if(convPanels.has(convId)) return convPanels.get(convId);
  const leftCol = document.getElementById('col-left') || document.getElementById('board');
  const rightRoot = document.getElementById('col-right-root') || document.getElementById('board');
  // 左: メイン会話パネル
  const panel = el('section','panel');
  panel.dataset.conv = convId;
  const ph = el('header');
  const titleEl = el('span','small',`Conversation ${convId.slice(0,8)}`);
  ph.append(titleEl);
  const body = el('div','panel-body');
  const streamMain = el('div','stream main');
  // 右: 会話専用の相談ルート
  const consultsRoot = el('section','col');
  consultsRoot.dataset.conv = convId;
  // 入力
  const form = el('div','inputbar');
  const inp = el('input'); inp.placeholder='議題やメッセージを入力…';
  const btn = el('button',null,'送信');
  form.append(inp, btn);
  body.append(streamMain);
  panel.append(ph, body, form);
  // DOMへ
  leftCol.prepend(panel);
  rightRoot.prepend(consultsRoot);
  const state = { panel, streamMain, consultsRoot, inp, btn, convId, consults: new Map(), lastOptimisticUser: null, typingQueues: new Map(), titleEl, sending:false, customTitle:true, tab:null };
  convPanels.set(convId, state);
  btn.addEventListener('click', (e)=>{ e.preventDefault(); send(state); });
  inp.addEventListener('keydown', (e)=>{ if(e.key==='Enter'){ e.preventDefault(); send(state); } });
  // タブも用意
  const tab = ensureTab(convId);
  state.tab = tab;
  // 新規は非アクティブで開始し、呼び出し側でsetActiveする
  state.panel.classList.add('hidden');
  state.consultsRoot.classList.add('hidden');
  return state;
}

function addMainMsg(state, role, text, { animate=false, optimistic=false } = {}){
  const div = el('div', `msg ${role}`);
  const name = el('span','name', displayName(role)); // 吹き出し内に限定表示（固定フッター等は未使用）
  const body = el('div','text', animate ? '' : text);
  div.append(name, body);
  state.streamMain.append(div);
  if(animate){
    typeTo(body, text, 20, ()=>{ state.streamMain.scrollTop = state.streamMain.scrollHeight; });
  }
  if(optimistic){ div.dataset.optimistic = '1'; }
  state.streamMain.scrollTop = state.streamMain.scrollHeight;
  return div;
}
function addConsultMsg(state, roleId, role, text, { animate=false } = {}){
  const c = ensureConsult(state, roleId);
  const div = el('div', `msg ${role}`);
  const name = el('span','name', displayName(role, role==='motivator_ai' ? 'motivator_ai' : roleId));
  const body = el('div','text', animate ? '' : text);
  div.append(name, body);
  // 役割ごとにキュー化し、タイプ表示が完了するまで次の吹き出しを出さない
  const qKey = roleId;
  if(!state.typingQueues.has(qKey)) state.typingQueues.set(qKey, Promise.resolve());
  const enqueue = state.typingQueues.get(qKey);
  state.typingQueues.set(qKey, enqueue.then(()=> new Promise(resolve=>{
    c.stream.append(div);
    const done = ()=>{ c.stream.scrollTop = c.stream.scrollHeight; resolve(); };
    if(animate){
      typeTo(body, text, 20, ()=>{ c.stream.scrollTop = c.stream.scrollHeight; });
      // 最長でもテキスト長/20cps + 1sで解放
      const est = Math.ceil((String(text||'').length/20)*1000) + 1000;
      setTimeout(done, est);
    } else {
      done();
    }
    c.stream.scrollTop = c.stream.scrollHeight;
  })));
}

async function newConversation(){
  const data = await api('/api/init', { method:'POST' });
  const convId = data.conversation_id;
  closedConvs.delete(convId);
  const st = ensurePanel(convId);
  // 初期イベント表示
  for(const ev of data.events){ handleEvent(st, ev); }
  // タブ生成＆アクティブ化
  ensureTab(convId);
  setActive(convId);
  bindRecUI();
}

function handleEvent(state, ev){
  if(!ev || typeof ev.id !== 'number') return;
  if(seenIds.has(ev.id)) return; // de-dup
  seenIds.add(ev.id);
  lastEventId = Math.max(lastEventId, ev.id);

  let lane = ev.lane || 'main';
  if(ev.lane == null && specialistRoles.has(ev.role)){ lane = `consult:${ev.role}`; }
  if(lane === 'main'){
    // 楽観的描画で表示済みのユーザー送信は重複させない
    const norm = (s)=> String(s||'').trim();
    if(ev.role === 'user' && (
      (state.lastOptimisticUser && norm(state.lastOptimisticUser.text) === norm(ev.text) && (Date.now() - state.lastOptimisticUser.ts) < 20000)
      || (state.userEcho && norm(state.userEcho) === norm(ev.text))
    )){
      state.userEcho = null;
      // 既に出しているのでスキップ
      // ただし、タブ名の自動命名はここでも行う（重複スキップ時は実行されないため）
      if(!state.customTitle){
        const tt = topicTitleFrom(ev.text);
        if(tt){
          if(state.tab){ const ttl = state.tab.querySelector('.ttl'); if(ttl) ttl.textContent = tt; }
          if(state.titleEl){ state.titleEl.textContent = tt; }
          state.customTitle = true;
        }
      }
    } else {
      const animate = (ev.role !== 'user');
      const bubble = addMainMsg(state, ev.role, ev.text, { animate });
      // 継続UI（司会のプロンプト時）
      if(ev.role === 'motivator_ai' && /議論を継続しますか？/.test(ev.text)){
        try{
          const cont = el('div','continue-ui');
          const mem = Array.from(state.consults.keys());
          if(mem.length){
            const list = el('div','continue-list');
            mem.forEach(rid=>{
              const lab = el('label');
              const cb = document.createElement('input'); cb.type='checkbox'; cb.value=rid; cb.checked=false;
              const nm = roleLabel[rid] || rid;
              lab.append(cb, document.createTextNode(' '+nm));
              list.append(lab);
            });
            const btns = el('div','continue-actions');
            const go = el('button',null,'指定で継続');
            const all = el('button',null,'全員で継続');
            go.disabled = true;
            list.addEventListener('change', ()=>{
              const any = list.querySelectorAll('input[type="checkbox"]:checked').length>0;
              go.disabled = !any;
            });
            go.onclick = ()=>{
              const picks = Array.from(list.querySelectorAll('input[type="checkbox"]:checked')).map(i=> i.value);
              if(!picks.length) return;
              const names = picks.map(rid=> roleLabel[rid] || rid);
              const text = names.length===1 ? `${names[0]}だけ継続` : `${names.join('と')}で継続`;
              sendText(state, text);
              cont.remove();
            };
            all.onclick = ()=>{ sendText(state, 'はい'); cont.remove(); };
            btns.append(go, all);
            cont.append(el('div','small','継続する担当を選んでください'), list, btns);
          } else {
            cont.append(el('div','small','現在、相談窓がありません。おすすめAIから追加してください。'));
          }
          bubble.appendChild(cont);
        }catch(_e){ }
      }
  // タブ名は固定連番運用に変更（トピックでのリネームは行わない）
    }
  } else if(lane.startsWith('consult:')){
    const roleId = lane.split(':')[1] || 'unknown';
    const animate = (ev.role !== 'user');
    addConsultMsg(state, roleId, ev.role, ev.text, { animate });
  }
}

async function send(state){
  const text = state.inp.value.trim(); if(!text || state.sending) return; state.inp.value='';
  state.sending = true;
  // まず即座に自分のメッセージを表示（楽観的UI）
  addMainMsg(state, 'user', text, { animate:false, optimistic:true });
  state.lastOptimisticUser = { text, ts: Date.now() };
  state.userEcho = String(text).trim();
  try{ const data = await api('/api/message',{ method:'POST', body: JSON.stringify({ conversation_id: state.convId, text })}); for(const ev of data.events){ const st = ensurePanel(ev.conv_id || state.convId); handleEvent(st, ev); } }
  catch(e){ addMainMsg(state,'motivator_ai',`送信エラー: ${e.message}`);} finally { state.sending = false; }
}

async function sendText(state, text){
  if(!text || state.sending) return;
  state.sending = true;
  addMainMsg(state, 'user', text, { animate:false, optimistic:true });
  state.lastOptimisticUser = { text, ts: Date.now() };
  state.userEcho = String(text).trim();
  try{ const data = await api('/api/message',{ method:'POST', body: JSON.stringify({ conversation_id: state.convId, text })}); for(const ev of data.events){ const st = ensurePanel(ev.conv_id || state.convId); handleEvent(st, ev); } }
  catch(e){ addMainMsg(state,'motivator_ai',`送信エラー: ${e.message}`);} finally { state.sending = false; }
}

let pollTimer = null;
async function poll(){
  try{
    const data = await api(`/api/feed?since=${lastEventId}`);
    const events = data.events||[];
    for(const ev of events){
      if(closedConvs.has(ev.conv_id)){
        // 再出現させない。IDは進めて既読化。
        if(typeof ev.id === 'number' && !seenIds.has(ev.id)){ seenIds.add(ev.id); lastEventId = Math.max(lastEventId, ev.id); }
        continue;
      }
      const st = ensurePanel(ev.conv_id);
      handleEvent(st, ev);
    }
    const stTxt = document.getElementById('status'); if(stTxt) stTxt.textContent = `live · last=${lastEventId}`;
  } catch(e){ const stTxt = document.getElementById('status'); if(stTxt) stTxt.textContent = `offline (${e.message})`; }
}

// レコメンドUI
function bindRecUI(){
  const btn = document.getElementById('rec-btn');
  const modal = document.getElementById('rec-modal');
  const close = document.getElementById('rec-close');
  const newBtn = document.getElementById('rec-new');
  const grid = document.getElementById('rec-grid');
  if(!btn || !modal || !close || !grid) return;
  // ボタンの既定動作を無効化し、確実に開く
  btn.setAttribute('type','button');
  btn.onclick = async (ev)=>{
    ev.preventDefault();
  const state = convPanels.get(activeConvId) || (Array.from(convPanels.values())[0]);
  if(!state){ return; }
    modal.hidden = false; modal.classList.add('open');
    grid.innerHTML='読み込み中…';
    // 選択数/ボタン状態を更新する関数
    const updateBulkBar = ()=>{
      const sel = grid.querySelectorAll('.card.selected[data-role-id][data-addable="1"]');
      const cnt = modal.querySelector('.bulkbar [data-role="count"]');
      if(cnt) cnt.textContent = `選択: ${sel.length}件`;
      const applyBtn = modal.querySelector('.bulkbar .bulk-apply');
      if(applyBtn) applyBtn.disabled = sel.length === 0;
    };
    // 一括参加バー
    let bulkbar = modal.querySelector('.bulkbar');
    if(!bulkbar){
      bulkbar = el('div','bulkbar');
      const left = el('div','small','選択: 0件');
      left.dataset.role="count";
      const right = el('div');
      const selectAllBtn = el('button','bulk-select-all','全選択');
      const clearBtn = el('button','bulk-clear','解除');
      const bulkBtn = el('button','bulk-apply','一括参加');
      bulkBtn.disabled = true;
      // 全選択/解除
      selectAllBtn.addEventListener('click', ()=>{
        const cards = Array.from(grid.querySelectorAll('.card[data-role-id][data-addable="1"]'));
        cards.forEach(c=>{
          c.classList.add('selected');
          const cb = c.querySelector('input.select-box');
          if(cb && !cb.disabled) cb.checked = true;
        });
        updateBulkBar();
      });
      clearBtn.addEventListener('click', ()=>{
        const cards = Array.from(grid.querySelectorAll('.card.selected[data-role-id]'));
        cards.forEach(c=>{
          c.classList.remove('selected');
          const cb = c.querySelector('input.select-box');
          if(cb) cb.checked = false;
        });
        updateBulkBar();
      });
      bulkBtn.addEventListener('click', async ()=>{
        const sel = Array.from(grid.querySelectorAll('.card.selected[data-role-id]')).map(c=> c.dataset.roleId);
        if(!sel.length) return;
        try{
          const resp = await api('/api/add-agents', { method:'POST', body: JSON.stringify({ conversation_id: state.convId, role_ids: sel })});
          for(const ev of (resp.events||[])){ handleEvent(state, ev); }
          modal.classList.remove('open'); modal.hidden = true;
        }catch(e){ alert('一括追加失敗: '+e.message); }
      });
      right.append(selectAllBtn, clearBtn, bulkBtn);
      bulkbar.append(left, right);
      modal.querySelector('.modal-body').append(bulkbar);
    }
    try{
      // 推奨一覧の取得は新API→404時に旧APIへフェールバック
      const fetchRec = async ()=>{
        try{
          return await api('/api/recommend_v2?limit=30');
        }catch(e){
          if(String(e.message||'').includes('404')){
            return await api('/api/recommend?limit=30');
          }
          throw e;
        }
      };
  const [recRaw, presets] = await Promise.all([ fetchRec(), api('/api/presets') ]);
  grid.innerHTML='';

      // フェーズ帯を明示（1段=横スクロールの行）
  // 以前のホワイトリスト/ブラックリストは撤廃し、バックエンド提供の全ロールを表示する
      const phases = [
        { title:'フェーズ1', cls:'phase-1', ids:new Set(['product_manager_ai','project_manager_ai','architect_ai','dev_ai','pm_ai']) },
        { title:'フェーズ2', cls:'phase-2', ids:new Set(['idea_ai','cust_biz_dev_manager','cust_sales_marketing','cust_business_analyst','cust_market_research','cust_competitive_analyst']) },
        { title:'フェーズ3', cls:'phase-3', ids:new Set(['cust_bce7cc85','cust_financial_analyst','cust_uiux_designer','cust_legal_compliance','cust_tech_lead']) },
      ];
      for(const ph of phases){
        const section = el('section',`phase ${ph.cls}`);
        const h = el('h3',null, ph.title);
        const g = el('div','phase-grid');
        section.append(h,g);
        grid.append(section);
        ph._grid = g;
      }

  // タイトルから簡易アイコンを自動生成（プレビュー用）
  const genIconFromTitle = (title)=>{
    const t = (title||'').toLowerCase();
    const rules = [
      [/企画|アイデ|product|価値|戦略/, '💡'],
      [/プロジェ|進行|管理|wbs|task/, '📋'],
      [/設計|アーキ|architecture|構成/, '📐'],
      [/開発|エンジ|dev|コード|api/, '💻'],
      [/文章|ライタ|執筆|ドキュ|文書/, '✍️'],
      [/市場|マーケ|調査|分析|データ/, '📊'],
      [/品質|テスト|検証|qa/, '✅'],
      [/セキュ|安全|認証/, '🔒'],
      [/ux|デザイン|体験|ui/, '🎯'],
      [/運用|sre|保守|サポート/, '🛟'],
    ];
    let emoji = '💠';
    for(const [re, e] of rules){ if(re.test(t)){ emoji = e; break; } }
    let h = 0; const s = title||''; for(let i=0;i<s.length;i++){ h = (h*31 + s.charCodeAt(i)) % 360; }
    return { bg: `hsl(${h}, 60%, 35%)`, emoji };
  };

      // 右上「新規作成」ボタンを使ったミニフォーム
      if(newBtn){
        newBtn.onclick = ()=>{
          const form = document.createElement('dialog');
          form.className = 'mini-dialog';
          form.innerHTML = '<form method="dialog" style="display:flex;flex-direction:column;gap:6px;min-width:320px">'
            +'<h3 style="margin:0 0 4px 0">新規AIを作成</h3>'
            +'<input name="title" placeholder="表示名" required />'
            +'<input name="persona" placeholder="性格/キャラ（任意）" />'
            +'<input name="tone" placeholder="口調/文体（任意）" />'
            +'<input name="catchphrase" placeholder="口癖（任意）" />'
            +'<input name="domain" placeholder="専門領域（任意）" />'
            +'<select name="api"><option value="openai">ChatGPT (OpenAI)</option><option value="anthropic">Claude (Anthropic)</option><option value="gemini">Gemini (Google)</option></select>'
            +'<div style="display:flex;gap:8px;justify-content:flex-end"><button value="cancel">キャンセル</button><button value="ok">作成</button></div>'
            +'</form>';
          document.body.appendChild(form);
          form.showModal();
          form.addEventListener('close', async ()=>{
            if(form.returnValue !== 'ok'){ form.remove(); return; }
            const fd = new FormData(form.querySelector('form'));
            const payload = {
              title: (fd.get('title')||'').toString().trim(),
              persona: (fd.get('persona')||'').toString().trim() || undefined,
              tone: (fd.get('tone')||'').toString().trim() || undefined,
              catchphrase: (fd.get('catchphrase')||'').toString().trim() || undefined,
              domain: (fd.get('domain')||'').toString().trim() || undefined,
              recommended_api: (fd.get('api')||'openai').toString()
            };
            if(!payload.title){ alert('表示名は必須です'); form.remove(); return; }
            try{
              const resp = await api('/api/roles',{ method:'POST', body: JSON.stringify(payload)});
              alert('作成しました: '+(resp.role?.title||resp.role?.id));
              form.remove();
              modal.hidden = true; modal.classList.remove('open');
            }catch(e){ alert('作成失敗: '+e.message); form.remove(); }
          });
        };
      }

  // 個別カード（AI名を見出しに）
  // 新仕様ロールのみをホワイトリストで表示（バックエンドが旧実装でもクライアントで除外）
  const recRoles = (recRaw.roles||[]);
  const loose = [];
  for(const r of recRoles){
        const card = el('div','card');
        card.dataset.roleId = r.id;
        // 右上チェックボックス
        const selChk = document.createElement('input');
        selChk.type = 'checkbox';
        selChk.className = 'select-box';
        // アイコン
        const icon = el('div','icon');
        icon.style.background = (r.icon && r.icon.bg) || '#233';
        icon.textContent = (r.icon && r.icon.emoji) || '';
        // 表示は日本語の呼称を優先
        const displayName = roleLabel[r.id] || r.title || r.id;
        const titleEl = el('div','title', displayName);
        card.append(titleEl);
        card.append(icon);
        card.append(el('div','desc', r.description||''));
  const join = el('button',null,'参加');
  if(r.addable === false || r.orchestrator){
          join.hidden = true; // 統括Mなどは追加不可、設定のみ
        } else {
          join.onclick = async ()=>{
            try{
              const resp = await api('/api/add-agent',{ method:'POST', body: JSON.stringify({ conversation_id: state.convId, role_id: r.id })});
              for(const ev of (resp.events||[])){ handleEvent(state, ev); }
              modal.classList.remove('open');
              modal.hidden = true;
            }catch(e){ alert('追加失敗: '+e.message); }
          };
        }
  // 追加可否をデータ属性に入れておく（全選択で使用）
  card.dataset.addable = join.hidden ? '0' : '1';
        // チェックボックスで選択トグル
        if(card.dataset.addable === '0') selChk.disabled = true;
        selChk.addEventListener('change', ()=>{
          if(selChk.disabled) return;
          card.classList.toggle('selected', selChk.checked);
          updateBulkBar();
        });
        // 簡易設定フォーム（折りたたみ）
  const configBtn = el('button',null,'設定');
  const formWrap = el('div','config-form');
        formWrap.hidden = true;
  const inTitle = el('input'); inTitle.placeholder = '表示名'; inTitle.value = displayName;
        // 利用API選択（既存値を初期値に）
        const inApi = el('select');
        ;[
          {v:'openai', label:'ChatGPT (OpenAI)'},
          {v:'anthropic', label:'Claude (Anthropic)'},
          {v:'gemini', label:'Gemini (Google)'}
        ].forEach(({v,label})=>{ const o = el('option'); o.value=v; o.textContent=label; inApi.append(o); });
        inApi.value = (r.recommended_api||'openai').toLowerCase();
  const inPersona = el('input'); inPersona.placeholder = '性格/キャラ'; inPersona.value = r.personality||'';
  const inTone = el('input'); inTone.placeholder = '口調/文体'; inTone.value = r.tone||'';
  const inCatch = el('input'); inCatch.placeholder = '口癖（任意）'; inCatch.value = r.catchphrase||'';
  const inDomain = el('input'); inDomain.placeholder = '専門領域（任意）'; inDomain.value = r.domain||'';
        const saveBtn = el('button',null,'保存');
        const cancelBtn = el('button',null,'取消');
        cancelBtn.type = 'button';
        saveBtn.type = 'button';
  // プロフィール表示（閲覧）
  const profile = el('div','profile');
  const profLines = [];
  const ap = (r.recommended_api||'openai').toLowerCase();
  profLines.push(`API: ${ap}`);
  if(r.personality){ profLines.push(`性格/キャラ: ${r.personality}`); }
  if(r.tone){ profLines.push(`口調/文体: ${r.tone}`); }
  if(r.catchphrase){ profLines.push(`口癖: ${r.catchphrase}`); }
  if(r.domain){ profLines.push(`専門領域: ${r.domain}`); }
  profile.textContent = profLines.join(' / ');
  // 設定ボタンで編集モードに切り替え
  configBtn.onclick = ()=>{ formWrap.hidden = !formWrap.hidden; profile.hidden = !formWrap.hidden; };
        cancelBtn.onclick = ()=>{ formWrap.hidden = true; };
    const notice = el('div','small');
    formWrap.append(notice);
    saveBtn.onclick = async ()=>{
          const payload = {
            title: inTitle.value.trim()||displayName,
      recommended_api: inApi.value,
            persona: inPersona.value.trim(),
            tone: inTone.value.trim(),
            catchphrase: inCatch.value.trim(),
            domain: inDomain.value.trim()
          };
          try{
            const resp = await api(`/api/roles/${r.id}`, { method:'PUT', body: JSON.stringify(payload)});
            const updated = resp.role || {};
            titleEl.textContent = payload.title;
            if(updated.icon){
              icon.style.background = updated.icon.bg || icon.style.background;
              icon.textContent = updated.icon.emoji || icon.textContent;
            }
    // プロフィール/表示を更新
    const ap2 = String(payload.recommended_api||'openai').toLowerCase();
    providerTag.textContent = 'API: ' + ap2;
    const lines = [];
    lines.push(`API: ${ap2}`);
    if(payload.persona){ lines.push(`性格/キャラ: ${payload.persona}`); }
    if(payload.tone){ lines.push(`口調/文体: ${payload.tone}`); }
    if(payload.catchphrase){ lines.push(`口癖: ${payload.catchphrase}`); }
    if(payload.domain){ lines.push(`専門領域: ${payload.domain}`); }
    profile.textContent = lines.join(' / ');
      notice.textContent = '保存しました';
      setTimeout(()=>{ notice.textContent=''; formWrap.hidden = true; }, 800);
          }catch(e){ alert('更新失敗: '+e.message); }
        };
  const btnRow = el('div');
  btnRow.append(join, configBtn);
  formWrap.append(inApi, inTitle, inPersona, inTone, inCatch, inDomain);
  // プロフィール（閲覧専用）とAPIタグ
  const providerTag = el('div','small','API: '+((r.recommended_api||'openai').toLowerCase()));
  card.append(providerTag, profile);
        // チェックボックスは最後に追加（CSSで右上に配置）
        card.append(selChk);
        const formBtnRow = el('div','config-actions');
        formBtnRow.append(saveBtn, cancelBtn);
        formWrap.append(formBtnRow);
        card.append(btnRow, formWrap);
        // 所属フェーズのグリッドに追加
        let placed = false;
        for(const ph of phases){
          if(ph.ids.has(r.id)){
            ph._grid.append(card);
            placed = true; break;
          }
        }
        if(!placed){ loose.push(card); }
      }
      // looseを最後に
      if(loose.length){
        const section = el('section','phase phase-3');
        section.append(el('h3',null,'その他'));
        const g = el('div','phase-grid');
        section.append(g);
        for(const c of loose) g.append(c);
        grid.append(section);
      }
    }catch(e){ grid.innerHTML = '取得エラー: '+e.message; }
  };
  const hideModal = ()=>{ modal.classList.remove('open'); modal.hidden = true; };
  close.onclick = hideModal;
  // 背景クリックで閉じる（中身クリックは閉じない）
  modal.addEventListener('click', (e)=>{ if(e.target === modal) hideModal(); });
  // Escキーで閉じる
  window.addEventListener('keydown', (e)=>{ if(e.key === 'Escape' && !modal.hidden) hideModal(); });
}

window.addEventListener('DOMContentLoaded', async ()=>{
  const newBtn = document.getElementById('new-conv'); if(newBtn) newBtn.addEventListener('click', newConversation);
  // 統括設定
  const orchBtn = document.getElementById('orch-btn');
  const orchModal = document.getElementById('orch-modal');
  const orchClose = document.getElementById('orch-close');
  const oOpen = document.getElementById('orch-opening');
  const oF = document.getElementById('orch-followups');
  const oS = document.getElementById('orch-summary');
  const oR = document.getElementById('orch-ack-research');
  const oP = document.getElementById('orch-ack-plan');
  const oT = document.getElementById('orch-ack-tech');
  const oG = document.getElementById('orch-ack-gtm');
  const oX = document.getElementById('orch-ack-general');
  if(orchBtn && orchModal){
    orchBtn.onclick = async ()=>{
      orchModal.hidden = false; orchModal.classList.add('open');
      try{
        const data = await api('/api/orchestrator');
        const s = data.settings || {};
        oOpen && (oOpen.value = s.opening_message || '');
        oF && (oF.value = s.followup_turns || 5);
        oS && (oS.value = s.summary_style || 'default');
        const a = s.acks || {};
        oR && (oR.value = a.research || '');
        oP && (oP.value = a.plan || '');
        oT && (oT.value = a.tech || '');
        oG && (oG.value = a.gtm || '');
        oX && (oX.value = a.general || '');
      }catch(e){ alert('取得失敗: '+e.message); }
    };
  }
  if(orchClose && orchModal){ orchClose.onclick = ()=>{ orchModal.classList.remove('open'); orchModal.hidden = true; }; }
  const orchSave = document.getElementById('orch-save');
  if(orchSave){
    orchSave.onclick = async ()=>{
      const payload = {
        opening_message: oOpen ? (oOpen.value||'') : undefined,
        followup_turns: oF ? Number(oF.value||5) : undefined,
        summary_style: oS ? (oS.value||'default') : undefined,
        acks: {
          research: oR ? (oR.value||'') : '',
          plan: oP ? (oP.value||'') : '',
          tech: oT ? (oT.value||'') : '',
          gtm: oG ? (oG.value||'') : '',
          general: oX ? (oX.value||'') : '',
        }
      };
      try{
        await api('/api/orchestrator', { method:'PUT', body: JSON.stringify(payload) });
        alert('保存しました');
        orchModal.classList.remove('open'); orchModal.hidden = true;
      }catch(e){ alert('保存失敗: '+e.message); }
    };
  }
  await newConversation();
  pollTimer = setInterval(poll, 1200);
});
