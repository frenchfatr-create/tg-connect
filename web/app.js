
const tg = window.Telegram?.WebApp;
tg?.ready(); tg?.expand();
const initData = tg?.initData || "";
const $ = id => document.getElementById(id);
let cache = {chats:[], messages:[]};
const names = {overview:"Обзор",chats:"Чаты",journal:"Журнал",connections:"Подключения",settings:"Настройки"};
const toast = t => { $("toast").textContent=t; $("toast").classList.add("show"); setTimeout(()=>$("toast").classList.remove("show"),2300); };
async function api(path, options={}) {
  const r=await fetch(path,{...options,headers:{"Content-Type":"application/json","X-Telegram-Init-Data":initData,...(options.headers||{})}});
  if(r.status===401) throw new Error("Нет доступа. Открой Mini App из Telegram под аккаунтом владельца.");
  if(!r.ok) throw new Error((await r.text())||("HTTP "+r.status));
  return r.json();
}
const esc = v => String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function showPage(name){
  document.querySelectorAll(".page").forEach(p=>p.classList.remove("show"));
  $("page-"+name).classList.add("show");
  document.querySelectorAll(".nav").forEach(b=>b.classList.toggle("active",b.dataset.page===name));
  $("pageTitle").textContent=names[name];
}
document.querySelectorAll(".nav").forEach(b=>b.addEventListener("click",()=>showPage(b.dataset.page)));
document.querySelectorAll("[data-goto]").forEach(b=>b.addEventListener("click",()=>showPage(b.dataset.goto)));
async function saveLogging(checked){
  await api("/api/settings",{method:"POST",body:JSON.stringify({logging_enabled:checked})});
  $("logging").checked=checked; $("loggingSettings").checked=checked;
  $("loggingBadge").textContent=checked?"ВКЛЮЧЁН":"ВЫКЛЮЧЕН";
  toast(checked?"Журнал включён":"Журнал выключен");
}
function renderChats(chats){
  $("chatTotal").textContent=chats.length;
  $("chatsList").innerHTML=chats.length?chats.map(c=>`<div class="data-row"><div><b>${esc(c.title||c.chat_id)}</b><p>ID: ${esc(c.chat_id)}</p><small>Последнее сообщение: ${esc(c.last_message||"нет данных")}</small></div><span class="pill">${c.message_count} записей</span></div>`).join(""):'<div class="list-empty">Пока нет чатов. Подключи бота в настройках Telegram Business и дождись нового сообщения.</div>';
}
function renderMessages(items,target){
  $(target).innerHTML=items.length?items.map(m=>`<div class="data-row"><div><b>${esc(m.sender_name||"Участник")}</b> <span class="pill">${esc(m.content_type||"message")}</span> ${m.deleted?'<span class="pill" style="color:#ff9da5">удалено</span>':""} ${m.edited?'<span class="pill">изменено</span>':""}<p>${esc(m.text||"[сообщение без текста]")}</p><small>${esc(m.chat_title||m.chat_id)} · ${esc(m.message_date||"")}</small></div></div>`).join(""):'<div class="list-empty">Записей пока нет.</div>';
}
async function loadMessages(){
  const q=encodeURIComponent($("search").value||"");
  const d=$("deletedFilter").value;
  const items=await api(`/api/messages?limit=100&q=${q}&deleted=${d}`);
  cache.messages=items; renderMessages(items,"journalList"); renderMessages(items.slice(0,5),"recent");
}
async function load(){
  try{
    const [stats,chats,settings,connections]=await Promise.all([api("/api/stats"),api("/api/chats"),api("/api/settings"),api("/api/connections")]);
    cache.chats=chats;
    $("mConnections").textContent=stats.connections; $("mChats").textContent=stats.chats;
    $("mMessages").textContent=stats.messages; $("mDeleted").textContent=stats.deletions;
    $("logging").checked=settings.logging_enabled; $("loggingSettings").checked=settings.logging_enabled;
    $("loggingBadge").textContent=settings.logging_enabled?"ВКЛЮЧЁН":"ВЫКЛЮЧЕН";
    renderChats(chats);
    $("connectionsList").innerHTML=connections.length?connections.map(c=>`<div class="data-row"><div><b>${esc(c.connection_id)}</b><p>Владелец ID: ${esc(c.user_id)}</p><small>Обновлено: ${esc(c.updated_at||"")}</small></div><span class="pill ${c.enabled?"blue":""}">${c.enabled?"Активно":"Отключено"}</span></div>`).join(""):'<div class="list-empty">Подключения ещё не обнаружены.</div>';
    await loadMessages();
    $("updatedAt").textContent="Обновлено: "+new Date().toLocaleTimeString("ru-RU");
  }catch(e){toast(e.message);}
}
$("logging").addEventListener("change",e=>saveLogging(e.target.checked).catch(err=>toast(err.message)));
$("loggingSettings").addEventListener("change",e=>saveLogging(e.target.checked).catch(err=>toast(err.message)));
$("reload").addEventListener("click",load); $("refreshJournal").addEventListener("click",loadMessages);
$("search").addEventListener("input",()=>{clearTimeout(window.searchTimer);window.searchTimer=setTimeout(()=>loadMessages().catch(e=>toast(e.message)),350)});
$("deletedFilter").addEventListener("change",()=>loadMessages().catch(e=>toast(e.message)));
$("clearJournal").addEventListener("click",async()=>{
  if(!confirm("Точно удалить все записи журнала? Это действие нельзя отменить."))return;
  try{await api("/api/journal/clear",{method:"POST",body:JSON.stringify({confirm:"CLEAR_TG_CONTROL_JOURNAL"})});toast("Журнал очищен");await load();}catch(e){toast(e.message);}
});
load();
