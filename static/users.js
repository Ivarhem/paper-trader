const ROOT=(document.querySelector('meta[name="root-path"]')?.content||'').replace(/\/$/, '');
const API_ROOT=ROOT;
function appUrl(path){return `${ROOT}${path}`;}
function redirectToLogin(){const current=`${location.pathname.replace(ROOT||'', '')}${location.search||''}`||'/users'; location.href=`${appUrl('/login')}?next=${encodeURIComponent(current)}`;}
const $=(id)=>document.getElementById(id);
let currentUser=null;
function basicToken(username,password){return 'Basic '+btoa(unescape(encodeURIComponent(`${username}:${password}`)));}
function authHeaders(){const h={'Content-Type':'application/json'}; const token=sessionStorage.getItem('pt_basic_auth'); if(token) h.Authorization=token; return h;}
function askAdminAuth(){const username=prompt('관리자 아이디를 입력하세요'); if(!username) return false; const password=prompt('관리자 비밀번호를 입력하세요'); if(!password) return false; sessionStorage.setItem('pt_basic_auth', basicToken(username.trim(), password)); return true;}
async function api(path,opt={},retried=false){
  const r=await fetch(`${API_ROOT}${path}`,{headers:{...authHeaders(),...(opt.headers||{})},...opt});
  if(r.status===401 && !retried){
    sessionStorage.removeItem('pt_basic_auth');
    if(askAdminAuth()) return api(path,opt,true);
    redirectToLogin(); return;
  }
  if(r.status===401){redirectToLogin(); return;}
  if(!r.ok){const e=await r.json().catch(()=>({})); throw new Error(e.detail||`HTTP ${r.status}`);}
  return r.json();
}
function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
async function load(){const me=await api('/api/auth/me'); currentUser=me.user.username; const data=await api('/api/admin/users'); render(data.items||[]);}
function render(rows){$('users-table').innerHTML=`<table class="history-table"><thead><tr><th>아이디</th><th>권한</th><th>상태</th><th>생성</th><th>관리</th></tr></thead><tbody>${rows.map(u=>`<tr><td><b>${esc(u.username)}</b>${u.username===currentUser?' <span class="pill">me</span>':''}</td><td>${(u.roles||[]).map(r=>`<span class="pill">${esc(r)}</span>`).join(' ')||'-'}</td><td>${u.active?'활성':'비활성'}</td><td class="hint">${esc(u.created_at||'-')}</td><td><button class="secondary" data-reset="${esc(u.username)}">비번 초기화</button> <button class="danger" data-del="${esc(u.username)}" ${u.username===currentUser?'disabled':''}>삭제</button></td></tr>`).join('')}</tbody></table>`; document.querySelectorAll('[data-reset]').forEach(b=>b.onclick=()=>resetPw(b.dataset.reset)); document.querySelectorAll('[data-del]').forEach(b=>b.onclick=()=>delUser(b.dataset.del));}
function openAdd(){ $('new-username').value=''; $('new-password').value=''; $('new-admin').checked=true; $('user-modal').showModal();}
async function saveUser(){const username=$('new-username').value.trim(); const password=$('new-password').value; const roles=$('new-admin').checked?['admin']:[]; if(!username||!password){alert('아이디와 비밀번호를 입력하세요.'); return;} try{await api('/api/admin/users',{method:'POST',body:JSON.stringify({username,password,roles})}); $('user-modal').close(); await load(); alert('사용자를 등록했습니다.');}catch(e){alert(`사용자 등록 실패: ${e.message}`);}}
async function resetPw(username){const password=prompt(`${username} 새 비밀번호`); if(!password) return; await api(`/api/admin/users/${encodeURIComponent(username)}/reset-password`,{method:'POST',body:JSON.stringify({password})}); alert('비밀번호를 초기화했습니다.');}
async function delUser(username){if(!confirm(`${username} 계정을 삭제할까요?`)) return; await api(`/api/admin/users/${encodeURIComponent(username)}`,{method:'DELETE'}); await load();}
$('add-btn').onclick=openAdd; $('cancel-btn').onclick=()=>$('user-modal').close(); $('save-btn').onclick=saveUser; $('logout-btn').onclick=async()=>{sessionStorage.removeItem('pt_basic_auth'); await api('/api/auth/logout',{method:'POST'}).catch(()=>{}); location.href=appUrl('/login');};
load().catch(e=>{$('users-table').innerHTML=`<p class="error">${esc(e.message)}</p>`;});
