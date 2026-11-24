// StreamClipper UI. Стан: P (перевірене посилання), CH (канал), cur (поточна задача).
const UIV='2.19.3';const $=id=>document.getElementById(id);
let THFAIL=false,LANG='uk',SET={},P=null,CH=null,cur={},img=null,cam=[0,0,.25,.25],poll=null,WEDIT=false,WLIST=[],NOTIFIED={};
const st={etm:'lyrics',egrade:'auto',estyle:'auto',ear:'4:3',emode:'normal',sspeed:'auto',mode:'best',layout:'blur',per:'30',typ:'archive',sp:'classic',ss:'m',spos:'auto',sw:'3',slang:'uk'};
const t=(k,...a)=>{let s=(I18N[LANG]||I18N.uk)[k];if(s===undefined)s=I18N.uk[k]??k;return s.replace(/\{(\d)\}/g,(_,i)=>a[i]??'')};
const ic=n=>`<svg class="i"><use href="#${n}"/></svg>`;
const DEV=/Mobi|Android|iPhone|iPad/i.test(navigator.userAgent)?'phone':'pc';
async function api(u,b){const r=await fetch(u,b===undefined?{headers:{'X-Device':DEV}}:{method:'POST',headers:{'Content-Type':'application/json','X-Device':DEV},body:JSON.stringify(b)});const j=await r.json();if(!r.ok||j.error)throw new Error(t(j.error)||j.error||r.status);return j}
const fileUrl=(p,dl)=>'/file?p='+encodeURIComponent(p)+(dl?'&dl=1':'');
const thumbUrl=u=>u?'/thumb?u='+encodeURIComponent(u):'';
const hms=s=>{s=Math.round(s||0);const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;return h+':'+String(m).padStart(2,'0')+':'+String(x).padStart(2,'0')};
const human=m=>{const u={uk:['хв','год'],ru:['мин','ч'],en:['min','h']}[LANG]||['min','h'];return m<1?'< 1 '+u[0]:m<60?Math.round(m)+' '+u[0]:(m/60).toFixed(1).replace('.0','')+' '+u[1]};
const gb=b=>b<1e9?Math.round(b/1e6)+' MB':(b/1e9).toFixed(1)+' GB';
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const num=n=>Number(n||0).toLocaleString(LANG==='en'?'en-US':LANG==='ru'?'ru-RU':'uk-UA');
const dt=s=>s?new Date(s).toLocaleDateString(LANG==='en'?'en-US':LANG==='ru'?'ru-RU':'uk-UA',{day:'numeric',month:'short',year:'numeric'}):'';
const sec=v=>String(v).split(':').map(Number).reduce((a,b)=>a*60+b,0);

function applyI18n(){document.documentElement.lang=LANG;document.querySelectorAll('[data-t]').forEach(e=>e.textContent=t(e.dataset.t));document.querySelectorAll('[data-tp]').forEach(e=>e.placeholder=t(e.dataset.tp));
  modeUI();
  if(P){plan();$('tu').textContent=[P.info.uploader,{twitch:'Twitch',youtube:'YouTube',kick:'Kick',file:t('file')}[P.info.platform]].filter(Boolean).join(' · ')} if(CH)listVids(); hintF(); draw();}
function seg(id,key,cb){$(id).querySelectorAll('button').forEach(b=>{b.onclick=e=>{e.preventDefault();st[key]=b.dataset.v;mark(id,key);cb&&cb()}})}
function mark(id,key){$(id).querySelectorAll('button').forEach(b=>b.classList.toggle('on',b.dataset.v===String(st[key])))}
function tab(n){document.querySelectorAll('.page').forEach(p=>p.classList.toggle('hide',p.id!=='p-'+n));document.querySelectorAll('#tabs button').forEach(b=>b.classList.toggle('on',b.dataset.tab===n));
  if(n==='channels')loadWatch();if(n==='queue')loadQueue();if(n==='lib')loadLib();if(n==='set')loadSet();
  try{history.replaceState(null,'','#'+n)}catch(e){}window.scrollTo({top:0,behavior:'instant'})}  // replaceState: зміна hash більше не стрибає до елемента з таким id (#lib) і не смикає сторінку на iPhone
document.querySelectorAll('#tabs button').forEach(b=>b.onclick=()=>tab(b.dataset.tab));
let CNT={best:40,seq:0,compile:8,teaser:30,edit:15},PREVM='best',EM=null;
function modeUI(){if(!EM&&$('emname'))$('emname').textContent=t('need_music');$('cntl').textContent=t({seq:'parts',compile:'comp_len',best:'max_clips',teaser:'teaser_len',edit:'edit_len'}[st.mode]);$('s2').classList.toggle('cmode',st.mode==='compile');$('s2').classList.toggle('tmode',st.mode==='teaser'||st.mode==='edit');$('s2').classList.toggle('emode',st.mode==='edit');
  const land=st.mode==='compile';if((pc.width===960)!==land){pc.width=land?960:540;pc.height=land?540:960}draw()}
seg('mode','mode',()=>{CNT[PREVM]=+$('cnt').value;$('cnt').value=CNT[st.mode];PREVM=st.mode;modeUI();plan()});
seg('layout','layout',()=>{draw();plan();hintF()});
seg('per','per',listVids);seg('typ','typ',listVids);
['sp','ss','spos','sw'].forEach(id=>seg(id,id,draw));
seg('etm','etm',()=>{$('etext').classList.toggle('hide',st.etm!=='custom');plan()});seg('egrade','egrade');seg('estyle','estyle');seg('ear','ear');seg('emode','emode');
seg('sspeed','sspeed',async()=>{await saveSet({speed:st.sspeed});loadSet()});
seg('slang','slang',async()=>{LANG=st.slang;applyI18n();await saveSet({lang:LANG})});
['mn','mx','cnt','subs','model','livem','scaps','sfont','emstart'].forEach(i=>$(i).addEventListener('input',()=>{plan();draw()}));
document.querySelectorAll('.extras input[type=checkbox]').forEach(x=>x.addEventListener('change',xcount));
function xcount(){const n=[...document.querySelectorAll('.extras input[type=checkbox]')].filter(x=>x.checked&&x.id!=='x_speech').length;$('xcount').textContent=n?'· '+n:''}
function setPreset(v){if(!v)return;const [a,b]=v.split('-');$('mn').value=a;$('mx').value=b;plan()}
$('url').addEventListener('keydown',e=>{if(e.key==='Enter')doProbe()});

async function health(){try{const h=await api('/api/health');SET=h.settings;LANG=SET.lang||'uk';st.slang=LANG;mark('slang','slang');applyI18n();
  $('hc').textContent=t(h.ffmpeg?'st_ok':'st_noff');$('hd').className='dot '+(h.ffmpeg?'ok':'bad');if(h.ffmpeg_err)$('perr').textContent=h.ffmpeg_err;
  $('model').value=SET.model||'small';$('x_gpu').checked=!!SET.gpu;$('x_hist').checked=!!SET.history;$('sver').textContent='v'+h.version;$('enick').value=$('enick').value||SET.nick||'';
  if(h.version&&h.version!==UIV){$('updb').classList.remove('hide');$('updb').lastElementChild.textContent=t('restart_need');$('updb').onclick=null}xcount()}
  catch(e){$('hc').textContent=t('st_down');$('hd').className='dot bad'}}

// ---------------- посилання / канал / превʼю
function busy(on){$('pbtn').disabled=on;$('pbtn').textContent=t(on?'opening':'open')}
async function doProbe(u,live){
  u=u||$('url').value.trim();if(!u)return;$('perr').textContent='';busy(true);
  try{const r=await api('/api/probe',{url:u,live:!!live});if(r.info.kind==='channel'){CH=r;showChannel()}else{showPreview(r);if(typeof saveDraft==='function')saveDraft()}}
  catch(e){$('perr').textContent=e.message}
  busy(false);
}
function showChannel(){const i=CH.info;$('sch').classList.remove('hide');['s2','s3','s4','srv'].forEach(x=>$(x).classList.add('hide'));
  $('chn').textContent='· '+(i.title||i.uploader);$('lv').classList.toggle('hide',!i.live);
  if(i.live){$('lvt').textContent=i.live.title||i.uploader;$('lvav').src=thumbUrl(i.avatar);$('lvav').classList.toggle('hide',!i.avatar)}
  const has=x=>i.videos.some(v=>v.type===x);st.typ=has('archive')?'archive':'all';mark('per','per');mark('typ','typ');listVids();$('sch').scrollIntoView({behavior:'smooth',block:'start'})}
function listVids(){if(!CH)return;const days=+st.per,now=Date.now();
  const L=CH.info.videos.filter(v=>(st.typ==='all'||v.type===st.typ)&&(!days||!v.created||now-new Date(v.created)<days*864e5));
  $('vl').innerHTML=L.length?L.map(v=>`<div class="v" data-u="${esc(v.url)}">${v.thumb?`<img loading="lazy" src="${thumbUrl(v.thumb)}" alt="">`:'<span class="ph"></span>'}<div class="t"><div class="ti">${esc(v.title)||t('untitled')}</div>
    <div class="meta tc"><span>${dt(v.created)}</span><span>${hms(v.duration)}</span><span>${num(v.views)} ${t('views')}</span>${v.game?`<span>${esc(v.game)}</span>`:''}</div></div><span class="ty">${v.recording?'<span class="dot live" style="display:inline-block"></span> ':''}${t('ty_'+v.type)}</span>${ic('right')}</div>`).join('')
    :`<div class="empty">${t('nothing')}</div>`;
  $('vl').querySelectorAll('.v').forEach(el=>el.onclick=()=>{el.style.background='var(--raised)';doProbe(el.dataset.u)})}
function pickLive(){doProbe(CH.info.url,true)}
function backToChannel(){$('s2').classList.add('hide');$('sch').scrollIntoView({behavior:'smooth'})}

function showPreview(r){
  P=r;const i=r.info,prof=r.profile||{},po=prof.opts||{};
  $('s2').classList.remove('hide');['s3','s4','srv'].forEach(x=>$(x).classList.add('hide'));$('bk').classList.toggle('hide',!CH);
  THFAIL=!i.thumbnail;$('th').onerror=()=>{THFAIL=true;if(img)$('th').src=img.src;else $('th').style.visibility='hidden'};
  $('th').src=thumbUrl(i.thumbnail);$('th').style.visibility=i.thumbnail?'visible':'hidden';
  $('tt').textContent=i.title||'';$('tu').textContent=[i.uploader,{twitch:'Twitch',youtube:'YouTube',kick:'Kick',file:t('file')}[i.platform]].filter(Boolean).join(' · ');
  $('td').textContent=i.duration?hms(i.duration):t('live_now');
  const live=['live','live_vod'].includes(i.kind);$('livebox').classList.toggle('hide',!live);
  $('livekind').textContent=live?t(i.kind==='live_vod'?'live_vod_note':'live_rec_note'):'';
  $('golbl').textContent=t(live?'start_live':'start');$('tn').classList.add('hide');
  st.mode=po.mode||(['twitch','kick'].includes(i.platform)||i.has_chat?'best':'seq');
  if(live&&['compile','teaser','edit'].includes(st.mode))st.mode='best';['mcomp','mteas','medit'].forEach(x=>$(x).classList.toggle('hide',live));
  CNT={best:po.max_clips||40,seq:po.seq_limit||0,compile:po.compile_min||8,teaser:po.teaser_sec||30,edit:po.edit_sec||15};PREVM=st.mode;$('cnt').value=CNT[st.mode];$('mn').value=po.min_len||60;$('mx').value=po.max_len||90;
  cam=prof.cam||po.cam||[0,0,.25,.25];st.layout=po.layout||(prof.cam||i.platform==='twitch'||i.platform==='kick'?'cam':'blur');
  const ss=po.sub_style||{};st.sp=ss.preset||'classic';st.ss=ss.size||'m';st.spos=ss.pos||'auto';st.sw=String(ss.words||3);$('scaps').checked=ss.caps!==false;$('sfont').value=ss.font||'Montserrat ExtraBold';
  for(const [id,k] of [['x_review','review'],['x_censor','censor'],['x_hook','hook'],['x_zoom','zoom'],['x_wm','watermark'],['x_cover','cover'],['x_denoise','denoise'],['x_vclips','vclips']])$(id).checked=!!po[k];
  $('x_speech').checked=po.speech_filter!==false;$('x_hist').checked=po.history??!!SET.history;
  $('profbtn').classList.toggle('hide',!i.uploader);
  if(prof.opts){$('tn').textContent=t('prof_applied');$('tn').classList.remove('hide')}
  ['mode','layout','sp','ss','spos','sw','etm','egrade','estyle','ear','emode'].forEach(k=>mark(k,k));modeUI();xcount();plan();img=null;draw();loadFrame(true);
  $('s2').scrollIntoView({behavior:'smooth',block:'start'});
}
function opts(){const kw=$('x_kw').checked?$('kw').value:'';
  return{mode:st.mode,min_len:+$('mn').value,max_len:+$('mx').value,max_clips:st.mode==='best'?(+$('cnt').value||40):40,seq_limit:st.mode==='seq'?+$('cnt').value:0,
  layout:st.layout,cam:st.layout==='cam'?cam.map(v=>+v.toFixed(3)):null,subs:$('subs').value==='1',model:$('model').value,
  live_minutes:+$('livem').value,live_every:+$('livee').value,speech_filter:$('x_speech').checked,review:$('x_review').checked,
  censor:$('x_censor').checked,censor_mode:$('x_censor_mode').value,hook:$('x_hook').checked,zoom:$('x_zoom').checked,watermark:$('x_wm').checked,
  cover:$('x_cover').checked,denoise:$('x_denoise').checked,compile_min:st.mode==='compile'?Math.max(1,+$('cnt').value||8):8,teaser_sec:st.mode==='teaser'?Math.min(90,Math.max(10,+$('cnt').value||30)):30,
  edit_sec:st.mode==='edit'?Math.min(60,Math.max(5,+$('cnt').value||15)):15,music:EM?EM.path:null,music_start:$('emstart').value===''?null:+$('emstart').value,edit_textmode:st.etm,edit_text:$('etext').value,
  edit_grade:st.egrade,edit_style:st.estyle,edit_ar:st.ear,edit_mode:st.emode,edit_ref:st.eref||null,edit_extra:[...document.querySelectorAll('#eextra input:checked')].map(x=>x.value),edit_export:$('eexport').checked,edit_extra_urls:$('eurls').value.split(/\s+/).filter(x=>x.trim()),edit_loop:$('eloop').checked,edit_vocal_ramp:$('evramp').checked,nick:$('enick').value.trim(),edit_behind:$('ebehind').checked,edit_delogo:$('edelogo').checked,edit_mute:$('emute').checked,vclips:$('x_vclips').checked,history:$('x_hist').checked,
  gpu:$('x_gpu').checked,keywords:kw,sub_style:{preset:st.sp,size:st.ss,pos:st.spos,words:+st.sw,caps:$('scaps').checked,font:$('sfont').value}}}
function plan(){if(!P)return;const i=P.info,o=opts(),live=['live','live_vod'].includes(i.kind);
  const dur=live?(o.live_minutes||120)*60+(i.duration||0):(i.duration||0),avg=(o.min_len+o.max_len)/2;let n,L=[],mb=0,min=0;const sp={base:.06,small:.13,medium:.35}[o.model]||.13;
  if(live&&i.kind==='live'){L.push(t('pl_live'));mb+=(o.live_minutes||120)/60*3000}
  if(o.mode==='edit'&&st.layout==='blur'){st.layout='full';mark('layout','layout')}
  if(o.mode==='edit'){const len=EM?Math.min(o.edit_sec,EM.dur-(o.music_start||0)):o.edit_sec;n=Math.round(len/0.55);
    if(['twitch'].includes(i.platform)){L.push(t('pl_audio'));mb+=dur/3600*70}else if(i.platform!=='file'){L.push(t('pl_video'));mb+=dur/3600*1800}
    L.push(t(i.has_chat?'pl_chat':'pl_loud'));L.push(t('pl_edit',n));if(o.edit_textmode!=='none')L.push(t(o.edit_textmode==='lyrics'?'pl_edit_lyrics':'pl_edit_custom'));if(o.edit_behind)L.push(t('pl_behind'));
    min+=dur/60*.02+20*1.5*65/60*sp+len*(o.edit_behind?.25:.12)+(o.edit_textmode==='lyrics'?len*sp*.5/60:0);mb+=40+len*2;
    $('plan').innerHTML=L.map(x=>`<li>${ic('check')}<span>${x}</span></li>`).join('');
    $('est').innerHTML=`<div><div class="l">${t('est_len')}</div><div class="f">≈ ${Math.round(len)} ${t('sec')}</div></div><div><div class="l">${t('est_time')}</div><div class="f">≈ ${human(min)}</div></div><div><div class="l">${t('est_disk')}</div><div class="f">≈ ${gb(mb*1e6)}</div></div>`;return}
  if(o.mode==='teaser'){const len=Math.min(o.teaser_sec,dur*.2);n=Math.max(3,Math.round(len/2.6));
    if(['twitch'].includes(i.platform)){L.push(t('pl_audio'));mb+=dur/3600*70}else if(i.platform!=='file'){L.push(t('pl_video'));mb+=dur/3600*1800}
    L.push(t(i.has_chat?'pl_chat':'pl_loud'));L.push(t('pl_teaser',n));L.push(t(o.subs?'pl_subs':'pl_nosubs'));L.push(t('l_'+o.layout)+', 1080×1920');
    min+=dur/60*.02+n*1.5*65/60*sp+n*.15;mb+=n*15;
    $('plan').innerHTML=L.map(x=>`<li>${ic('check')}<span>${x}</span></li>`).join('');
    $('est').innerHTML=`<div><div class="l">${t('est_len')}</div><div class="f">≈ ${Math.round(len)} ${t('sec')}</div></div><div><div class="l">${t('est_time')}</div><div class="f">≈ ${human(min)}</div></div><div><div class="l">${t('est_disk')}</div><div class="f">≈ ${gb(mb*1e6)}</div></div>`;return}
  if(o.mode==='compile'){const len=Math.min(o.compile_min*60,dur*.6);n=Math.max(2,Math.ceil(len/avg));
    if(['twitch'].includes(i.platform)){L.push(t('pl_audio'));mb+=dur/3600*70}else if(i.platform!=='file'){L.push(t('pl_video'));mb+=dur/3600*1800}
    L.push(t(i.has_chat?'pl_chat':'pl_loud'));L.push(t('pl_compile',n,o.min_len,o.max_len));min+=dur/60*.02+n*2*(avg+45)/60*sp*(o.subs?1:.6)+len*.02;mb+=len/60*90;
    L.push(t(o.subs?'pl_subs':'pl_nosubs'));
    $('plan').innerHTML=L.map(x=>`<li>${ic('check')}<span>${x}</span></li>`).join('');
    $('est').innerHTML=`<div><div class="l">${t('est_len')}</div><div class="f">≤ ${human(len/60)}</div></div><div><div class="l">${t('est_time')}</div><div class="f">≈ ${human(min)}</div></div><div><div class="l">${t('est_disk')}</div><div class="f">≈ ${gb(mb*1e6)}</div></div>`;return}
  if(o.mode==='best'){n=Math.min(o.max_clips,Math.max(3,Math.round(dur/420)));
    if(['twitch'].includes(i.platform)&&i.kind!=='live'){L.push(t('pl_audio'));mb+=dur/3600*70}else if(i.platform!=='file'&&i.kind!=='live'){L.push(t('pl_video'));mb+=dur/3600*1800}
    L.push(t(i.has_chat?'pl_chat':'pl_loud'));L.push(t('pl_keep',n,o.min_len,o.max_len));min+=dur/60*.02+n*2*(avg+45)/60*sp*(o.subs?1:.6)}
  else{const part=o.seq_limit?Math.min(dur,o.seq_limit*avg):dur;n=Math.max(1,Math.round(part/avg));if(i.platform!=='file'){L.push(t('pl_video'));mb+=dur/3600*1800}
    L.push(t('pl_seq',o.seq_limit?t('pl_first',hms(part)):t('pl_all'),o.min_len,o.max_len));min+=part/60*sp*(o.subs?1:0)}
  L.push(t(o.subs?'pl_subs':'pl_nosubs'));L.push(t('l_'+o.layout)+', 1080×1920');
  min+=n*avg*.015;mb+=n*avg/60*45;
  $('plan').innerHTML=L.map(x=>`<li>${ic('check')}<span>${x}</span></li>`).join('');
  $('est').innerHTML=`<div><div class="l">${t('est_clips')}</div><div class="f">≈ ${n}</div></div><div><div class="l">${t('est_time')}</div><div class="f">≈ ${live&&!o.live_minutes?'∞':human(min)}</div></div><div><div class="l">${t('est_disk')}</div><div class="f">≈ ${gb(mb*1e6)}</div></div>`}
function hintF(){if($('fhint'))$('fhint').textContent=t(st.layout==='cam'?'drag_hint':'frame_from')}
async function loadFrame(first){if(!P)return;const d=P.info.duration||0,tt=first?Math.round(d*.35):Math.round(d*(.1+Math.random()*.8));
  $('fhint').textContent=t('loading_frame');
  try{const r=await api('/api/frame',{pid:P.pid,t:tt});const im=new Image();im.onload=()=>{img=im;draw();hintF();if(THFAIL){$('th').src=im.src;$('th').style.visibility='visible'}};im.src=fileUrl(r.path)}
  catch(e){$('fhint').textContent=t('frame_fail')+e.message}}
async function autoCam(){$('acbtn').disabled=true;$('fhint').textContent=t('finding');
  try{const r=await api('/api/detect_cam',{pid:P.pid});if(r.cam){cam=r.cam;st.layout='cam';mark('layout','layout');draw();hintF()}else $('fhint').textContent=t('cam_none')}catch(e){$('fhint').textContent=e.message}
  $('acbtn').disabled=false}
const fc=$('fc'),fx=fc.getContext('2d'),pc=$('pc'),px=pc.getContext('2d');
function draw(){fx.fillStyle='#0f0f10';fx.fillRect(0,0,fc.width,fc.height);px.fillStyle='#0f0f10';px.fillRect(0,0,pc.width,pc.height);if(!img)return;
  fc.height=Math.round(fc.width*img.height/img.width);fx.drawImage(img,0,0,fc.width,fc.height);
  if(st.layout==='cam'){const [x,y,w,h]=cam,W=fc.width,H=fc.height;fx.fillStyle='rgba(15,15,16,.55)';fx.fillRect(0,0,W,H);
    fx.drawImage(img,x*img.width,y*img.height,w*img.width,h*img.height,x*W,y*H,w*W,h*H);fx.strokeStyle='#ffe500';fx.lineWidth=2;fx.strokeRect(x*W+1,y*H+1,w*W-2,h*H-2);fx.fillStyle='#ffe500';fx.fillRect((x+w)*W-12,(y+h)*H-12,12,12)}
  const iw=img.width,ih=img.height;const cover=(sx,sy,sw,sh,dx,dy,dw,dh)=>{const r=Math.max(dw/sw,dh/sh),cw=dw/r,ch=dh/r;px.drawImage(img,sx+(sw-cw)/2,sy+(sh-ch)/2,cw,ch,dx,dy,dw,dh)};
  if(pc.width===960){cover(0,0,iw,ih,0,0,960,540);if($('subs').value!=='1')return;
    const sz={s:26,m:32,l:38}[st.ss];px.font=`800 ${sz}px Montserrat,"Arial Black",sans-serif`;let W=({uk:['ось','цей','момент'],ru:['вот','этот','момент'],en:['this','is','it']}[LANG]||['ось','цей','момент']).slice(0,+st.sw);
    if($('scaps').checked)W=W.map(w=>w.toUpperCase());const gap=10,ws=W.map(w=>px.measureText(w).width),tot=ws.reduce((a,b)=>a+b,0)+gap*(W.length-1);let xx=480-tot/2,y=st.spos==='top'?60:st.spos==='middle'?280:500;
    const cols={classic:'#ffe500',green:'#4cff66',minimal:'#ffffff',box:'#ffe500'};if(st.sp==='box'){px.fillStyle='rgba(0,0,0,.6)';px.fillRect(xx-10,y-sz,tot+20,sz*1.35)}
    px.lineJoin='round';W.forEach((w,k)=>{if(st.sp!=='box'){px.lineWidth=st.sp==='minimal'?3:6;px.strokeStyle='#000';px.strokeText(w,xx,y)}px.fillStyle=k===Math.min(1,W.length-1)?cols[st.sp]:'#fff';px.fillText(w,xx,y);xx+=ws[k]+gap});return}
  let capY=960*(1-560/1920);
  if(st.layout==='blur'){px.save();px.filter='blur(14px) brightness(.75)';cover(0,0,iw,ih,-20,-20,580,1000);px.restore();const h=540*ih/iw;px.drawImage(img,0,(960-h)/2,540,h)}
  else if(st.layout==='full'){cover(0,0,iw,ih,0,0,540,960);capY=960*(1-420/1920)}
  else{const [x,y,w,h]=cam;cover(x*iw,y*ih,w*iw,h*ih,0,0,540,320);const cw=ih*27/32;px.drawImage(img,(iw-cw)/2,0,cw,ih,0,320,540,640);capY=960*(1-1180/1920)}
  if(st.spos==='middle')capY=500;if(st.spos==='top')capY=st.layout==='cam'?360:160;
  if($('subs').value!=='1')return;
  const sz={s:33,m:41,l:49}[st.ss],fam=$('sfont').value.startsWith('Montserrat')?'Montserrat':$('sfont').value;px.font=`800 ${sz}px ${fam},"Arial Black",sans-serif`;
  const cols={classic:'#ffe500',green:'#4cff66',minimal:'#ffffff',box:'#ffe500'};
  let W=({uk:['ось','цей','момент'],ru:['вот','этот','момент'],en:['this','is','it']}[LANG]||['ось','цей','момент']).slice(0,+st.sw);if($('scaps').checked)W=W.map(w=>w.toUpperCase());
  const gap=14,ws=W.map(w=>px.measureText(w).width),tot=ws.reduce((a,b)=>a+b,0)+gap*(W.length-1);let xx=270-tot/2;
  if(st.sp==='box'){px.fillStyle='rgba(0,0,0,.6)';px.fillRect(xx-14,capY-sz,tot+28,sz*1.35)}
  px.lineJoin='round';W.forEach((w,k)=>{px.textAlign='left';if(st.sp!=='box'&&st.sp!=='minimal'){px.lineWidth=8;px.strokeStyle='#000';px.strokeText(w,xx,capY)}
    if(st.sp==='minimal'){px.lineWidth=4;px.strokeStyle='rgba(0,0,0,.6)';px.strokeText(w,xx,capY)}
    px.fillStyle=k===Math.min(1,W.length-1)?cols[st.sp]:(st.sp==='minimal'?'rgba(255,255,255,.7)':'#fff');px.fillText(w,xx,capY);xx+=ws[k]+gap})}
let drag=null;
fc.addEventListener('pointerdown',e=>{if(st.layout!=='cam'||!img)return;const r=fc.getBoundingClientRect(),X=(e.clientX-r.left)/r.width,Y=(e.clientY-r.top)/r.height,[x,y,w,h]=cam;
  if(Math.abs(X-(x+w))<.03&&Math.abs(Y-(y+h))<.05)drag={k:'r'};else if(X>x&&X<x+w&&Y>y&&Y<y+h)drag={k:'m',dx:X-x,dy:Y-y};else{cam=[X,Y,.05,.05];drag={k:'r'}}fc.setPointerCapture(e.pointerId)});
fc.addEventListener('pointermove',e=>{if(!drag)return;const r=fc.getBoundingClientRect(),X=Math.min(1,Math.max(0,(e.clientX-r.left)/r.width)),Y=Math.min(1,Math.max(0,(e.clientY-r.top)/r.height));
  if(drag.k==='m'){cam[0]=Math.min(1-cam[2],Math.max(0,X-drag.dx));cam[1]=Math.min(1-cam[3],Math.max(0,Y-drag.dy))}else{cam[2]=Math.max(.04,X-cam[0]);cam[3]=Math.max(.04,Y-cam[1])}draw()});
fc.addEventListener('pointerup',()=>drag=null);
async function saveProfile(){await api('/api/profile',{name:P.info.uploader,opts:opts()});$('tn').textContent=t('prof_saved');$('tn').classList.remove('hide')}

// ---------------- робота
async function start(){if(st.mode==='edit'&&!EM){alert(t('need_music'));return}$('go').disabled=true;
  try{const r=await api('/api/start',{pid:P.pid,url:P.info.url,opts:opts()});cur={job:r.job,src:P.info.duration};
    $('s3').classList.remove('hide');['s4','srv','after','compbox'].forEach(x=>$(x).classList.add('hide'));$('compv').removeAttribute('src');delete $('compv').dataset.p;$('rc').textContent='';$('res').innerHTML='';$('tl').innerHTML='';$('jerr').textContent='';$('cbtn').disabled=false;
    $('s3').scrollIntoView({behavior:'smooth',block:'start'});track(r.job)}catch(e){alert(e.message)}
  $('go').disabled=false}
function logLine(l){return `${l.t}  ${t(l.k==='parts'?'parts_log':l.k,...l.a)}`}
function track(id){clearInterval(poll);cur.job=id;poll=setInterval(async()=>{try{const j=await api('/api/job/'+id);cur.dir=j.dir;
  $('stg').textContent=t(j.stage,...j.args);$('sub').textContent=j.sub||'';$('pbar').style.width=j.pct+'%';$('elp').textContent=hms(j.elapsed);
  {const lt=j.logs.map(logLine).join('\n');if($('log').textContent!==lt){$('log').textContent=lt;$('log').scrollTop=1e9}}$('stopbtn').classList.toggle('hide',!(j.live&&j.state==='running'));
  if(j.state==='review'&&j.candidates&&!$('srv').dataset.job){showReview(j)}
  if(j.results.length){$('s4').classList.remove('hide');const rk=j.id+':'+j.results.length+':'+j.state;if(cur.rk!==rk){cur.rk=rk;renderRes(j.results,j.dir,j.src_dur||cur.src)}}
  if(j.extra&&j.extra.compilation){showComp(j.extra.compilation,j.extra.compilation_dur)}
  if(j.extra&&j.extra.teaser){showComp(j.extra.teaser,j.extra.teaser_dur,true)}
  if(j.extra&&j.extra.edit){showComp(j.extra.edit,j.extra.edit_dur,true,'edit');if(j.extra.edit_info&&j.state==='done')showEditPanel(j.extra.edit_info,j.extra.edit_project,j.opts)}
  if(!['running','review'].includes(j.state)){clearInterval(poll);$('cbtn').disabled=true;$('stopbtn').classList.add('hide');if(j.err)$('jerr').textContent=t(j.err)===j.err?j.err:t(j.err);
    if(j.state==='done'){document.title='✓ StreamClipper';showAfter(j);notifyB(t('n_done'),t('done',j.results.length))}}}catch(e){}},1500)}
async function cancel(){if(cur.job&&confirm(t('confirm_cancel')))await api('/api/cancel',{job:cur.job})}
async function stopLive(){await api('/api/stop',{job:cur.job})}
function notifyB(title,body){try{if(window.Notification&&Notification.permission==='granted'&&document.hidden)new Notification(title,{body})}catch(e){}}

function showReview(j){const box=$('rvl'),src=j.extra.review_src;$('srv').dataset.job=j.id;$('srv').classList.remove('hide');
  box.innerHTML=j.candidates.map(c=>`<div class="rv" data-i="${c.i}"><label class="tg"><input type="checkbox" checked><i></i></label>
    <div><div class="tc">${hms(c.start)} – ${hms(c.end)} · ${Math.round(c.end-c.start)} s · ${c.score??''} ${c.chat?'· '+esc(c.chat):''}</div><div class="tx">${esc(c.text)||'—'}</div></div>
    <div class="ctl">${src?`<button class="ghost ic" data-play>▶</button>`:''}<input type="number" step="0.5" data-s value="${c.start}"><input type="number" step="0.5" data-e value="${c.end}"><input type="text" data-tt placeholder="${t('title_ph')}"></div></div>`).join('');
  box.querySelectorAll('.rv').forEach(r=>{const cb=r.querySelector('input[type=checkbox]');cb.onchange=()=>r.classList.toggle('off',!cb.checked);
    const pb=r.querySelector('[data-play]');if(pb)pb.onclick=()=>{const a=$('rvaudio'),s=+r.querySelector('[data-s]').value-src.off,e=+r.querySelector('[data-e]').value-src.off;
      a.src=fileUrl(src.path)+'#t='+s.toFixed(1)+','+e.toFixed(1);a.play()}});
  $('srv').scrollIntoView({behavior:'smooth',block:'start'})}
async function sendReview(){const items=[...document.querySelectorAll('#rvl .rv')].map(r=>({i:+r.dataset.i,keep:r.querySelector('input[type=checkbox]').checked,
  start:+r.querySelector('[data-s]').value,end:+r.querySelector('[data-e]').value,title:r.querySelector('[data-tt]').value}));
  await api('/api/review',{job:$('srv').dataset.job,items});$('rvaudio').pause();$('srv').classList.add('hide');delete $('srv').dataset.job}

function timeline(rs,total){const T=Math.max(total||0,...rs.map(r=>(r.s??sec(r.start))+r.dur));if(!T){$('tl').innerHTML='';return}
  $('tl').innerHTML='<div class="track"></div>'+rs.map(r=>`<div class="mk" title="#${r.n} · ${r.start}" style="left:${(r.s??sec(r.start))/T*100}%;width:${Math.max(.25,r.dur/T*100)}%"></div>`).join('')+
  `<span class="a" style="left:0">0:00:00</span><span class="a" style="right:0">${hms(T)}</span>`}
function renderRes(rs,dir,total){$('rc').textContent='· '+rs.length;timeline(rs,total);const box=$('res');
  rs.forEach(r=>{let el=box.querySelector(`[data-n="${r.n}"]`);const key=r.file+r.title+(r.rated||'');if(el&&el.dataset.k===key)return;
    const html=`<video controls preload="none" poster="${fileUrl(r.preview)}&v=${Date.now()}" src="${fileUrl(r.file)}"></video><div class="m">
      <div class="h"><span>#${r.n}</span><span class="tc">${r.start} · ${r.dur} s</span></div><div class="tx">${esc(r.text)}</div>
      <input type="text" placeholder="${t('title_ph')}" value="${esc(r.title)}"><button data-ap>${t('apply_title')}</button>
      <div class="h"><div class="row" style="gap:4px"><button class="ghost ic ${r.rated===1?'on':''}" data-up>${ic('up')}</button><button class="ghost ic ${r.rated===-1?'on':''}" data-down>${ic('down')}</button></div>
      <div class="row" style="gap:4px">${r.cover?`<a class="btn ghost ic" title="${t('cover')}" href="${fileUrl(r.cover,1)}">${ic('square')}</a>`:''}<a class="btn ghost ic" title="${t('download')}" href="${fileUrl(r.file,1)}">${ic('dl')}</a></div></div></div>`;
    if(!el){el=document.createElement('div');el.className='clip';el.dataset.n=r.n;box.appendChild(el)}
    el.dataset.k=key;el.innerHTML=html;const b=el.querySelector('[data-ap]'),inp=el.querySelector('input');
    el.querySelector('[data-up]').onclick=()=>rate(dir,r,true,rs,total);el.querySelector('[data-down]').onclick=()=>rate(dir,r,false,rs,total);
    b.onclick=async()=>{b.disabled=true;b.textContent=t('rebuild');try{const q=await api('/api/rerender',{dir,n:r.n,title:inp.value});
      const tm=setInterval(async()=>{const j=await api('/api/job/'+q.job);if(j.state!=='running'){clearInterval(tm);if(j.err)alert(t(j.err));else{Object.assign(r,j.results[0]);renderRes(rs,dir,total)}}},1500)}catch(e){alert(e.message);b.disabled=false}}})}
$('enick').addEventListener('change',()=>saveSet({nick:$('enick').value.trim()}).catch(()=>{}));
$('emusic').addEventListener('change',async e=>{const f=e.target.files[0];if(!f)return;$('emname').textContent=t('uploading');
  try{const r=await fetch('/api/upload?name='+encodeURIComponent(f.name),{method:'POST',body:f});
    if(r.status===404)throw new Error(t('restart_need'));const j=await r.json().catch(()=>({error:'bad_audio'}));if(!r.ok||j.error)throw new Error(t(j.error)||j.error);
    EM=j;$('emname').textContent=`${j.name} · ${hms(j.dur)}`;$('emstart').max=Math.max(0,Math.floor(j.dur-5));plan();loadWave()}catch(err){$('emname').textContent=err.message;EM=null}});
async function reEdit(count,extra,same){if(!cur.dir)return;const r=await api('/api/reedit',{dir:cur.dir,count:count||1,opts:extra||null,same:!!same});$('epanel').classList.add('hide');$('s3').classList.remove('hide');$('jerr').textContent='';track(r.job)}
function showComp(p,d,vert,kind){$('s4').classList.remove('hide');$('compbox').classList.remove('hide');$('compbox').classList.toggle('vert',!!vert);$('compn').textContent=t(kind==='edit'?'m_edit':vert?'m_teaser':'m_compile');$('compmore').classList.toggle('hide',kind!=='edit');$('compv').onloadedmetadata=()=>{const v=$('compv');$('compbox').classList.toggle('vert',v.videoHeight>v.videoWidth);v.style.aspectRatio=v.videoWidth+'/'+v.videoHeight};if($('compv').dataset.p!==p){$('compv').dataset.p=p;$('compv').src=fileUrl(p)}
  $('compd').textContent=d?hms(d):'';$('compdl').href=fileUrl(p,1)}
async function rate(dir,r,good,rs,total){await api('/api/rate',{dir,n:r.n,good});r.rated=good?1:-1;renderRes(rs,dir,total)}
function showAfter(j){const b=j.extra.sources_bytes||0;$('after').classList.remove('hide');$('cleanlbl').textContent=t('clean',gb(b));$('cleanbtn').disabled=!b;$('morebtn').disabled=!b}
async function cleanup(dir){const r=await api('/api/cleanup',{dir});$('cleanlbl').textContent=t('clean_done',gb(r.freed));$('cleanbtn').disabled=true;$('morebtn').disabled=true;loadLib()}
async function more(){const r=await api('/api/more',{dir:cur.dir,n:+$('moren').value});$('after').classList.add('hide');$('s3').classList.remove('hide');track(r.job)}

// ---------------- канали
async function loadWatch(){const r=await api('/api/watch');WLIST=r.list;renderW(r.status)}
function renderW(status){status=status||{};$('wadd').classList.toggle('hide',!WEDIT);$('weditl').textContent=t(WEDIT?'done_edit':'edit');
  $('wl').innerHTML=WLIST.length?WLIST.map((w,i)=>{const s=status[w.platform+':'+w.name.toLowerCase()]||{};return `<div class="w"><div class="top">${w.avatar?`<img class="av" src="${thumbUrl(w.avatar)}" alt="">`:`<span class="av ini">${esc((w.display||w.name||'?')[0].toUpperCase())}</span>`}
    <div style="flex:1;min-width:0"><div class="nm ti">${esc(w.display||w.name)}</div><div class="st"><span class="dot ${s.live?'live':''}"></span>${s.live?t('w_live'):t('w_off')} · ${w.platform}</div></div>
    ${WEDIT?`<button class="ghost ic" data-del="${i}" title="${t('remove')}">${ic('x')}</button>`:`<label class="tg"><input type="checkbox" data-en="${i}" ${w.enabled?'checked':''}><i></i></label>`}</div>
    ${WEDIT?`<input type="text" data-url="${i}" value="${esc(w.url||(w.platform==='kick'?'https://kick.com/':'https://www.twitch.tv/')+w.name)}">`:''}</div>`}).join(''):`<div class="empty">${t('w_empty')}</div>`;
  $('wl').querySelectorAll('[data-en]').forEach(c=>c.onchange=()=>{WLIST[+c.dataset.en].enabled=c.checked;saveW()});
  $('wl').querySelectorAll('[data-del]').forEach(b=>b.onclick=()=>{WLIST.splice(+b.dataset.del,1);renderW(status)});
  $('wl').querySelectorAll('[data-url]').forEach(inp=>inp.onchange=()=>{WLIST[+inp.dataset.url].url=inp.value.trim()})}
async function saveW(){const r=await api('/api/watch/save',{list:WLIST});WLIST=r.list}
async function toggleWEdit(){if(WEDIT){await saveW();WEDIT=false;loadWatch()}else{WEDIT=true;renderW()}}
function wAdd(){const u=$('waddurl').value.trim();if(!u)return;WLIST.push({url:u,enabled:true,name:u.split('/').filter(Boolean).pop()});$('waddurl').value='';renderW()}

// ---------------- черга
async function loadQueue(){const L=await api('/api/queue');$('ql').innerHTML=L.length?L.map(q=>`<div class="it"><span class="t tc">${esc(q.url)}</span><span class="tc">${t('q_'+q.state)}</span>
  ${q.job?`<button class="ghost" onclick="tab('cut');document.getElementById('s3').classList.remove('hide');track('${q.job}')">${ic('right')}</button>`:''}${q.state==='wait'?`<button class="ghost ic" onclick="qRm('${q.id}')">${ic('x')}</button>`:''}</div>`).join(''):`<div class="empty">${t('q_empty')}</div>`}
async function qAdd(){const urls=$('qurls').value.split('\n').map(s=>s.trim()).filter(Boolean);if(!urls.length)return;
  await api('/api/queue/add',{urls,opts:P?opts():{}});$('qurls').value='';loadQueue()}
async function qRm(id){await api('/api/queue/remove',{id});loadQueue()}

// ---------------- бібліотека
async function loadLib(){try{const L=await api('/api/library');if(!L.length){$('lib').innerHTML=`<span class="hint">${t('empty')}</span>`;return}$('lib').innerHTML='';
  L.forEach(x=>{const d=document.createElement('div');d.className='it click';
    d.innerHTML=`<span class="t">${esc((x.uploader?x.uploader+' · ':'')+x.title)}</span><span class="tc">${!x.results.length?t(x.edit?'m_edit':x.teaser?'m_teaser':'m_compile'):t('clips_n',x.results.length)}</span>
      ${x.sources_bytes?`<button class="ghost" data-c>${ic('trash')} ${gb(x.sources_bytes)}</button>`:''}${ic('right')}`;
    d.onclick=e=>{if(e.target.closest('[data-c]'))return;tab('cut');cur={dir:x.dir};$('s4').classList.remove('hide');$('compbox').classList.add('hide');$('res').innerHTML='';$('tl').innerHTML='';$('rc').textContent='';
      if(x.results.length)renderRes(x.results,x.dir,x.src_dur);if(x.compilation)showComp(x.compilation,x.compilation_dur);if(x.teaser)showComp(x.teaser,x.teaser_dur,true);if(x.edit){showComp(x.edit,x.edit_dur,true,'edit');showEditPanel(x.edit_info||{},x.edit_project,x.opts)}
      if(x.sources_bytes)showAfter({extra:{sources_bytes:x.sources_bytes}});$('s4').scrollIntoView({behavior:'smooth',block:'start'})};
    const c=d.querySelector('[data-c]');if(c)c.onclick=async()=>{await api('/api/cleanup',{dir:x.dir});loadLib()};$('lib').appendChild(d)})}catch(e){}}

// ---------------- налаштування
async function saveSet(o){SET=await api('/api/settings',o);$('supd').textContent=t('s_saved');return SET}
async function loadSet(){const h=await api('/api/health');SET=h.settings;const hw=await api('/api/hardware');
  $('smodel').value=SET.model||hw.model;$('sgpu').checked=!!SET.gpu;$('sgpun').textContent=hw.gpu||t('s_gpu_none');$('sgpu').disabled=!hw.gpu;$('gpuname').textContent=hw.gpu||t('s_gpu_none');
  st.sspeed=SET.speed||'auto';mark('sspeed','sspeed');
  $('sspeedd').textContent=t('s_speed_d',t('sp_'+hw.speed_auto),hw.perf?t('sp_'+hw.perf.tier):'',hw.perf?t('s_speed_x',hw.perf.loud,hw.perf.asr,hw.perf.render,hw.perf.prefetch):'');
  $('squiet').checked=!!SET.quiet;$('sdisk').value=SET.disk_limit_gb||hw.disk_suggest;$('sdiskd').textContent=t('s_disk_d',hw.disk_suggest);$('shist').checked=!!SET.history;
  $('sphone').checked=!!SET.lan;$('shw').textContent=`${hw.cores} CPU · ${hw.ram} GB RAM · ${hw.gpu||'CPU'} · ${hw.free_gb} GB free`;
  $('snotif').textContent=t(window.Notification&&Notification.permission==='granted'?'s_notif_ok':'s_notif_btn');lan()}
async function lan(){const r=await api('/api/lan');const box=$('qr');$('sphr').classList.toggle('hide',r.on===r.active);
  box.classList.toggle('hide',!(r.on&&r.active&&r.svg));if(!(r.on&&r.active&&r.svg))return;
  box.innerHTML=`<div class="qrimg">${r.svg}</div><div class="qrt"><div class="tc">${esc(r.url)}</div>
    ${r.alt.length?`<div class="hint">${t('s_phone_alt')}</div>`+r.alt.map(u=>`<div class="tc">${esc(u)}</div>`).join(''):''}
    ${r.fw===false?`<div class="hint warn">${t('s_fw_off')}</div><button class="ghost" onclick="fwAllow(this)">${t('s_fw_btn')}</button>`:''}
    <div class="hint">${t('s_phone_help')}</div></div>`}
async function fwAllow(b){b.disabled=true;b.textContent=t('s_fw_wait');await api('/api/firewall',{});lan()}
async function phone(on){$('supd').textContent=t('s_restarting');try{await saveSet({lan:on,restart:true})}catch(e){}
  $('supd').textContent=t('s_restarting');setTimeout(()=>location.reload(),7000)}
async function autoTune(){const hw=await api('/api/hardware');await saveSet({model:hw.model,gpu:!!hw.gpu,disk_limit_gb:hw.disk_suggest});loadSet()}
async function updCheck(){try{const u=await api('/api/update');$('supd').textContent=u.newer?t('s_upd_new',u.latest):t('s_upd_none');$('supdgo').classList.toggle('hide',!u.newer)}catch(e){$('supd').textContent=e.message}}
async function updGo(){$('supd').textContent=t('s_upd_wait');try{await api('/api/update',{});setTimeout(()=>location.reload(),15000)}catch(e){$('supd').textContent=e.message}}

// ---------------- активні задачі (лайв зі слідкування тощо)
async function watchJobs(){try{const L=await api('/api/jobs');for(const j of L){if(j.live&&!NOTIFIED[j.id]){NOTIFIED[j.id]=1;notifyB('StreamClipper',(j.uploader||'')+' '+t('n_live'))}}}catch(e){}}
setInterval(watchJobs,30000);
document.fonts.load('800 40px Montserrat').then(draw);
health().then(()=>{tab((location.hash||'#cut').slice(1)||'cut');setTimeout(async()=>{try{const u=await api('/api/update');if(!$('updb').onclick&&!$('updb').classList.contains('hide'))return;$('updb').classList.toggle('hide',!u.newer)}catch(e){}},2000)});

// ---------------- 2.17: хвиля треку, референс, другі джерела, панель едіту (заміна кадрів, текст пісні, 3 варіанти, проєкт)
let WV=null,WDRAG=null,EINFO=null,EOPTS=null;
function wLen(){return st.emode==='drop'?9:Math.min(60,Math.max(5,+$('cnt').value||15))}
function wStart(){const v=$('emstart').value;return v===''?(WV?WV.best:0):+v}
async function loadWave(){if(!EM)return;$('wavef').classList.remove('hide');try{WV=await api('/api/wave',{path:EM.path,len:wLen()});if($('emstart').value==='')$('emstart').value=WV.best;drawWave()}catch(e){$('wavef').classList.add('hide')}}
function drawWave(){const c=$('wavec');if(!WV||!c)return;const dpr=devicePixelRatio||1;c.width=c.clientWidth*dpr;c.height=72*dpr;const g=c.getContext('2d'),W=c.width,H=c.height,n=WV.peaks.length;
  const s0=wStart(),L=wLen(),x0=s0/WV.dur*W,x1=Math.min(W,(s0+L)/WV.dur*W);g.clearRect(0,0,W,H);
  g.fillStyle='rgba(255,214,10,.13)';g.fillRect(x0,0,x1-x0,H);
  for(let i=0;i<n;i++){const x=i/n*W,h=Math.max(1,WV.peaks[i]*H*.9),inS=x>=x0&&x<=x1;g.fillStyle=inS?'#ffd60a':'rgba(237,237,237,.28)';g.fillRect(x,(H-h)/2,Math.max(1,W/n-.5*dpr),h)}
  const mk=(t,col)=>{if(!t)return;const x=t/WV.dur*W;g.fillStyle=col;g.fillRect(x-dpr,0,2*dpr,H)};mk(WV.drop,'#ff4d4d');
  const a=$('waud');if(!a.paused){mk(a.currentTime,'#fff')}
  g.strokeStyle='#ffd60a';g.lineWidth=2*dpr;g.strokeRect(x0+dpr,dpr,x1-x0-2*dpr,H-2*dpr);
  $('wpos').textContent=`${hms(s0)} – ${hms(Math.min(WV.dur,s0+L))}`}
(function(){const c=$('wavec');if(!c)return;const tAt=e=>{const r=c.getBoundingClientRect();return Math.max(0,Math.min(1,(e.clientX-r.left)/r.width))*WV.dur};
  c.addEventListener('pointerdown',e=>{if(!WV)return;c.setPointerCapture(e.pointerId);const t0=tAt(e),s0=wStart(),L=wLen();WDRAG=(t0>=s0&&t0<=s0+L)?{off:t0-s0}:{off:L/2};move(e)});
  const move=e=>{if(!WDRAG||!WV)return;const s=Math.max(0,Math.min(WV.dur-wLen(),tAt(e)-WDRAG.off));$('emstart').value=Math.round(s*10)/10;drawWave()};
  c.addEventListener('pointermove',move);c.addEventListener('pointerup',()=>{WDRAG=null;const a=$('waud');if(!a.paused){a.currentTime=wStart()}plan()});
  $('wplay').onclick=()=>{const a=$('waud');if(!a.paused){a.pause();$('wplayl').textContent=t('e_listen');return}if(a.dataset.p!==EM.path){a.src=fileUrl(EM.path);a.dataset.p=EM.path}a.currentTime=wStart();a.play();$('wplayl').textContent=t('e_stop')};
  $('waud').addEventListener('timeupdate',()=>{const a=$('waud');if(a.currentTime>=wStart()+wLen()){a.pause();$('wplayl').textContent=t('e_listen')}drawWave()});
  $('wbest').onclick=()=>{if(WV){$('emstart').value=WV.best;drawWave();plan()}};
  $('wdrop').onclick=()=>{if(WV&&WV.drop){$('emstart').value=Math.max(0,Math.round((WV.drop-(st.emode==='drop'?3.5:wLen()*0.35))*10)/10);drawWave();plan()}};
  ['cnt','emstart'].forEach(i=>$(i).addEventListener('input',drawWave));window.addEventListener('resize',drawWave);$('emode').addEventListener('click',drawWave)})();
$('eref').addEventListener('change',async e=>{const f=e.target.files[0];if(!f)return;$('erefn').textContent=t('uploading');
  try{const r=await fetch('/api/upload?name='+encodeURIComponent(f.name),{method:'POST',body:f});const j=await r.json();if(!r.ok||j.error)throw new Error(t(j.error)||j.error);
    $('erefn').textContent=t('e_ref_an');const R=await api('/api/refstyle',{path:j.path});st.eref=R;
    $('erefn').innerHTML=`${esc(t('e_ref_ok',R.shot_len,t('es_'+R.style),t('g_'+R.grade)))} <a href="#" id="erefx">✕</a>`;$('erefx').onclick=ev=>{ev.preventDefault();st.eref=null;$('erefn').textContent='';$('eref').value=''}}
  catch(err){$('erefn').textContent=err.message;st.eref=null}});
async function loadSources(){try{const L=await api('/api/edit_sources',{});$('eextra').innerHTML=L.length?L.map(x=>{const m=(x.dir.split(/[\\/]/).pop()||'').match(/^(\d{4})(\d\d)(\d\d)_(\d\d)(\d\d)/);return `<label><input type="checkbox" value="${esc(x.dir)}"> ${esc(x.title)}${m?` <span class="tc">· ${m[3]}.${m[2]} ${m[4]}:${m[5]}</span>`:''}</label>`}).join(''):`<span class="hint">${t('e_extra_none')}</span>`}catch(e){}}
loadSources();
let ESEL=new Set();
function showEditPanel(info,proj,o){EINFO=info||{};EOPTS=o||null;ESEL=new Set();$('epanel').classList.remove('hide');
  const th=EINFO.thumbs||[];$('ethumbs').innerHTML=th.map((p,k)=>p?`<div class="th" data-k="${k}"><img loading="lazy" src="${fileUrl(p)}"><span>${k+1}</span></div>`:'').join('');
  $('ethumbs').querySelectorAll('.th').forEach(d=>d.onclick=()=>{const k=+d.dataset.k;ESEL.has(k)?ESEL.delete(k):ESEL.add(k);d.classList.toggle('sel');$('erep').disabled=!ESEL.size});
  $('erep').disabled=true;$('elyr').value=EINFO.lyrics||'';$('elyrw').classList.toggle('hide',!EINFO.lyrics);
  $('eproj').classList.toggle('hide',!proj);$('eproj').onclick=()=>api('/api/open',{path:proj})}
function replaceShots(){if(!EINFO||!EINFO.shots||!ESEL.size)return;const ban=[...ESEL].map(k=>EINFO.shots[k]);
  reEdit(1,{edit_pin:{shots:EINFO.shots,replace:[...ESEL]},edit_ban:ban},true)}
function fixLyrics(){if(!EINFO)return;reEdit(1,{lyrics_fix:$('elyr').value,edit_pin:EINFO.shots?{shots:EINFO.shots,replace:[]}:null},true)}
function reuseOpts(){const o=EOPTS;if(!o)return;st.mode='edit';mark('mode','mode');
  const m={edit_style:'estyle',edit_grade:'egrade',edit_ar:'ear',edit_mode:'emode',edit_textmode:'etm'};for(const k in m)if(o[k]){st[m[k]]=o[k];mark(m[k],m[k])}
  if(o.edit_sec)CNT.edit=o.edit_sec;$('cnt').value=o.edit_sec||15;PREVM='edit';$('enick').value=o.nick||'';$('etext').value=o.edit_text||'';$('etext').classList.toggle('hide',st.etm!=='custom');
  ['edit_behind:ebehind','edit_delogo:edelogo','edit_mute:emute','edit_loop:eloop','edit_vocal_ramp:evramp','edit_export:eexport'].forEach(x=>{const [k,id]=x.split(':');if(o[k]!==undefined)$(id).checked=!!o[k]});
  if(o.music){EM={path:o.music,name:o.music.split(/[\\/]/).pop().replace(/^[0-9a-f]{6}_/,''),dur:0};$('emname').textContent=EM.name;$('emstart').value=o._music_start??o.music_start??'';loadWave()}
  $('eurls').value=(o.edit_extra_urls||[]).join('\n');st.eref=o.edit_ref||null;$('erefn').textContent=st.eref?t('e_ref_ok',st.eref.shot_len,t('es_'+st.eref.style),t('g_'+st.eref.grade)):'';
  modeUI();plan();tab('cut');window.scrollTo({top:0,behavior:'smooth'})}

// ---------------- 2.19: синхронізація ПК ↔ телефон: панель активності, сповіщення, підхоплення налаштувань
const SY={seen:{},init:false,draftTs:+(localStorage.getItem('sc_draft_seen')||0),applying:false,saveT:0,lastDraft:0};
const devName=d=>t(d==='phone'?'sy_phone':d==='pc'?'sy_pc':'sy_queue');
const modeName=m=>t({edit:'m_edit',best:'m_best',seq:'m_seq',compile:'m_compile',teaser:'m_teaser',reedit:'sy_reedit',rerender:'sy_rerender'}[m]||'m_best');
function toast(html,acts,ms){const box=$('toasts');const d=document.createElement('div');d.className='toast';d.innerHTML=`<div class="tx">${html}</div>`;
  const row=document.createElement('div');row.className='ta';(acts||[]).forEach(([lbl,fn])=>{const b=document.createElement('button');b.className='ghost';b.textContent=lbl;b.onclick=()=>{fn();d.remove()};row.appendChild(b)});
  const x=document.createElement('button');x.className='ghost ic';x.innerHTML=ic('x');x.onclick=()=>d.remove();row.appendChild(x);d.appendChild(row);box.appendChild(d);
  if(DEV==='phone'&&navigator.vibrate)try{navigator.vibrate(60)}catch(e){}
  setTimeout(()=>d.classList.add('gone'),(ms||9000));setTimeout(()=>d.remove(),(ms||9000)+400)}
function follow(id){tab('cut');$('s3').classList.remove('hide');['s4','srv','after','compbox'].forEach(x=>$(x).classList.add('hide'));$('res').innerHTML='';$('jerr').textContent='';track(id);$('s3').scrollIntoView({behavior:'smooth',block:'start'})}
async function syncPoll(){let r;try{r=await api('/api/active')}catch(e){return}
  const run=r.jobs.filter(j=>['running','review'].includes(j.state));
  $('act').classList.toggle('hide',!run.length);
  const ah=run.map(j=>`<button class="ap" onclick="follow('${j.id}')"><i class="spin"></i><b>${esc((j.title||modeName(j.mode)).slice(0,28))}</b><span>${Math.round(j.pct)}%</span>${j.device&&j.device!==DEV?`<em>${esc(devName(j.device))}</em>`:''}</button>`).join('');
  if(ah!==SY.ah){SY.ah=ah;$('act').innerHTML=ah}  // без перемальовування, якщо нічого не змінилось (на iPhone не «тремтить»)
  for(const j of r.jobs){const was=SY.seen[j.id];SY.seen[j.id]=j.state;if(!SY.init)continue;const mine=j.id===cur.job,nm=esc(j.title||modeName(j.mode));
    if(!was&&['running','review'].includes(j.state)&&!mine&&j.device!==DEV){
      const busyHere=cur.job&&['running','review'].includes(SY.seen[cur.job]);
      if(!busyHere){follow(j.id);toast(t('sy_watch',devName(j.device),modeName(j.mode),nm),[],5000)}  // сам показує процес з іншого пристрою
      else toast(t('sy_started',devName(j.device),modeName(j.mode),nm),[[t('sy_follow'),()=>follow(j.id)]])}
    if(j.state==='done'&&((was&&['running','review'].includes(was))||(!was&&j.ended&&r.now-j.ended<30&&j.device!==DEV))){notifyB('StreamClipper',t('sy_done',nm));if(!mine||document.hidden)toast(t('sy_done',nm),[[t('sy_open'),()=>follow(j.id)]],14000)}
    if(was&&was!=='error'&&j.state==='error'&&!mine)toast(t('sy_err',nm),[[t('sy_open'),()=>follow(j.id)]])}
  if(!SY.init){SY.draftTs=0;const lr=run.filter(j=>j.state==='running'||j.state==='review').sort((a,b)=>a.elapsed-b.elapsed)[0];
    if(lr&&!cur.job){follow(lr.id)}}  // відкрив сайт, а там уже щось робиться — одразу показуємо процес
  SY.init=true;if(r.draft&&r.draft>SY.lastDraft){SY.lastDraft=r.draft;checkDraft()}}
function draftState(){const vals={};document.querySelectorAll('#p-cut input[id],#p-cut select[id],#p-cut textarea[id]').forEach(e=>{if(e.type==='file')return;vals[e.id]=e.type==='checkbox'?e.checked:e.value});
  const s={...st};delete s.eref;return{url:$('url').value.trim(),purl:P&&P.info?P.info.url:null,st:s,eref:st.eref||null,vals,EM:EM||null,cnt:typeof CNT!=='undefined'?CNT:null}}
function saveDraft(){if(SY.applying)return;SY.localEdit=Date.now();clearTimeout(SY.saveT);SY.saveT=setTimeout(async()=>{try{const r=await api('/api/draft',{state:draftState()});SY.lastDraft=r.ts;SY.draftTs=r.ts;localStorage.setItem('sc_draft_seen',r.ts)}catch(e){}},900)}
document.addEventListener('input',e=>{if(e.target.closest('#p-cut'))saveDraft()});document.addEventListener('change',e=>{if(e.target.closest('#p-cut'))saveDraft()});
document.addEventListener('click',e=>{if(e.target.closest('#p-cut .seg button'))saveDraft()});
async function checkDraft(){let d;try{d=await api('/api/draft')}catch(e){return}if(!d||!d.ts||d.ts<=SY.draftTs||d.device===DEV||Date.now()/1000-d.ts>6*3600)return;
  const s=d.state||{};if(!s.url&&!s.EM)return;
  if(Date.now()-(SY.localEdit||0)<4000){SY.lastDraft=0;return}  // щойно щось міняв тут — не перебиваємо, підхопимо на наступному опитуванні
  SY.draftTs=d.ts;localStorage.setItem('sc_draft_seen',d.ts);
  const newSrc=s.purl&&(!P||!P.info||P.info.url!==s.purl);
  await applyDraft(s,newSrc);
  if(newSrc)toast(t('sy_opened',devName(d.device))+`<div class="tc">${esc((s.url||'').slice(0,60))}</div>`,[],5000)}
async function applyDraft(s,newSrc){SY.applying=true;try{
  if(s.url)$('url').value=s.url;
  if(s.purl&&(!P||!P.info||P.info.url!==s.purl))await doProbe(s.url)
  Object.assign(st,s.st||{});st.eref=s.eref||null;document.querySelectorAll('.seg[id]').forEach(e=>{if(e.id in st)mark(e.id,e.id)});
  for(const [id,v] of Object.entries(s.vals||{})){const e=$(id);if(!e||e.type==='file')continue;if(e.type==='checkbox')e.checked=!!v;else e.value=v}
  if(s.cnt&&typeof CNT!=='undefined')Object.assign(CNT,s.cnt);
  if(s.EM){EM=s.EM;$('emname').textContent=`${EM.name}${EM.dur?' · '+hms(EM.dur):''}`;loadWave()}
  $('etext').classList.toggle('hide',st.etm!=='custom');if(st.eref)$('erefn').textContent=t('e_ref_ok',st.eref.shot_len,t('es_'+st.eref.style),t('g_'+st.eref.grade));
  modeUI();plan();if(newSrc&&!(cur.job&&SY.seen[cur.job]==='running'))tab('cut')}catch(e){console.log(e)}finally{SY.applying=false}}
setInterval(()=>{if(!document.hidden)syncPoll()},2500);syncPoll();
document.addEventListener('visibilitychange',()=>{if(!document.hidden){syncPoll();checkDraft()}});
