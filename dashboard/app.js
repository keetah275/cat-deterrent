const $=id=>document.getElementById(id);
let offset=0,total=0,decisions=[],feedbackToken="",savingFeedback=false;
const feedbackDrafts=new Map();
const text=(tag,value,cls)=>{const e=document.createElement(tag);e.textContent=value;if(cls)e.className=cls;return e;};
async function get(url){const r=await fetch(url,{cache:'no-store'});if(!r.ok)throw Error('Request failed');return r.json();}
function imageLink(url,label){const a=document.createElement('a');a.href=url;a.target='_blank';a.rel='noopener';a.textContent=label;return a;}
function photo(url){const img=document.createElement('img');img.src=url;img.loading='lazy';img.alt='Garden evidence frame';return img;}
function captureTime(name){const m=name.match(/motion_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})/);return m?`${m[3]}/${m[2]}/${m[1]} ${m[4]}:${m[5]}:${m[6]}`:name;}
async function loadCaptures(){const d=await get(`/api/captures?kind=${$('kind').value}&offset=${offset}`);total=d.total;$('gallery').replaceChildren();for(const item of d.items){const card=text('article','','card');const a=imageLink(item.url,'');a.append(photo(item.url));card.append(a,text('p',captureTime(item.name)),text('p',item.folder+' / '+item.name));$('gallery').append(card);}if(!d.items.length)$('gallery').append(text('p','No captures in this view.'));$('captureCount').textContent=`${total} images in this view`;$('page').textContent=`Page ${Math.floor(offset/24)+1}`;$('previous').disabled=offset===0;$('next').disabled=offset+24>=total;}
function feedbackPanel(d){
 const validId=typeof d.decision_id==='string'&&/^[a-f0-9]{64}$/.test(d.decision_id);
 // Older running servers may omit decision_id. Never share their drafts under undefined.
 const draftKey=validId?d.decision_id:`event:${d.event_id}`;
 const panel=text('section','','feedback');
 const status=text('p',d.feedback?`You ${d.feedback.verdict==='agree'?'agreed':'disagreed'} · saved ${new Date(d.feedback.saved_at).toLocaleString()}`:'Not reviewed yet');
 status.setAttribute('role','status');
 const label=text('label','Optional review note');
 const note=document.createElement('textarea');note.rows=2;note.maxLength=2000;
 note.value=feedbackDrafts.get(draftKey)??d.feedback?.note??'';
 note.name=`review-note-${draftKey}`;note.autocomplete='off';
 note.placeholder='What did you see in the evidence?';
 note.oninput=()=>{feedbackDrafts.set(draftKey,note.value);};
 label.append(note);
 const buttons=text('div','','feedback-buttons');
 const agree=text('button','Agree with decision'),disagree=text('button','Disagree with decision');
 for(const [button,verdict] of [[agree,'agree'],[disagree,'disagree']]){
  button.disabled=!validId;
  button.setAttribute('aria-pressed',String(d.feedback?.verdict===verdict));
  button.onclick=async()=>{
   if(!validId)return;
   savingFeedback=true;agree.disabled=disagree.disabled=true;status.textContent='Saving feedback…';
   try{
    const response=await fetch('/api/feedback',{method:'POST',headers:{'Content-Type':'application/json','X-Feedback-Token':feedbackToken},body:JSON.stringify({decision_id:d.decision_id,verdict,note:note.value})});
    if(!response.ok){const detail=await response.json().catch(()=>({}));throw Error(detail.error||`Feedback was not saved (HTTP ${response.status}). Refresh and try again.`);}
    d.feedback=await response.json();feedbackDrafts.delete(draftKey);
    status.textContent=`Saved: you ${verdict==='agree'?'agree':'disagree'} with this decision.`;
    agree.setAttribute('aria-pressed',String(verdict==='agree'));disagree.setAttribute('aria-pressed',String(verdict==='disagree'));
    $('reviewStatus').textContent='Feedback saved.';
    renderDecisions();
   }catch(e){status.textContent=e.message+' Your draft note is retained.';$('reviewStatus').textContent=status.textContent;}
   finally{savingFeedback=false;agree.disabled=disagree.disabled=false;}
  };
 }
 if(!validId)status.textContent='Dashboard update needed: restart garden-dashboard on the Pi, then reload this page. Feedback is disabled until decision IDs are available.';
 buttons.append(agree,disagree);panel.append(text('h4','Your assessment'),status,label,buttons,text('small','A vote applies to this decision and policy. Click a verdict again to save an edited note. Feedback does not change detection settings.'));
 return panel;
}
function renderDecisions(){$('decisionList').replaceChildren();const chosen=decisions.filter(d=>($('verdict').value==='all'||d.would_spray===($('verdict').value==='yes'))&&($('reviewFilter').value==='all'||($('reviewFilter').value==='unreviewed'&&!d.feedback)||($('reviewFilter').value==='disagree'&&d.feedback?.verdict==='disagree')));for(const d of chosen){const card=text('article','','decision');card.append(text('span',d.would_spray?'WOULD SPRAY':'NO SPRAY','badge'+(d.would_spray?' yes':'')),text('h3',d.event_id),text('p',d.reason.replaceAll('_',' ')));if(d.policy)card.append(text('p',`Policy: ${d.policy.min_animal_frames} animal frames; animal score ≥ ${d.policy.animal_confidence}; person veto ≥ ${d.policy.person_confidence}.`));const evidence=text('div','','evidence');for(const frame of d.evidence){const tile=text('div','');const url=frame.annotated||frame.original;if(url){const a=imageLink(url,'');a.append(photo(url));tile.append(a);}tile.append(text('p',`Frame ${frame.index}`));if(frame.original)tile.append(imageLink(frame.original,'Original'));if(frame.annotated){tile.append(document.createTextNode(' · '),imageLink(frame.annotated,'Labels'));}tile.append(text('p',frame.detections.map(x=>`${x.label} ${Number(x.confidence).toFixed(2)}`).join(', ')||'No detections'));evidence.append(tile);}card.append(evidence,feedbackPanel(d));$('decisionList').append(card);}if(!chosen.length)$('decisionList').append(text('p',decisions.length?'No decisions match these filters. Choose All to revisit reviewed decisions.':'No decisions yet. Run spray_decisions.py on a YOLO results batch to add a review.'));}
async function refresh(){if(savingFeedback||document.activeElement?.tagName==="TEXTAREA")return;try{const [s,d]=await Promise.all([get('/api/status'),get('/api/decisions')]);$('service').textContent=`${s.service.ActiveState||'unknown'} · ${s.service.SubState||'unknown'}`;$('restarts').textContent=`Automatic restarts: ${s.service.NRestarts||'unknown'}`;$('latest').textContent=s.latest_capture?captureTime(s.latest_capture.name):'No captures yet';feedbackToken=d.feedback_token;decisions=d.items;$('decisionRun').textContent=d.run?`Latest analysed batch: ${d.run}. ${d.items.length} decisions shown (up to 500).`:'No decision batch yet.';renderDecisions();await loadCaptures();$('connection').textContent=`Updated ${new Date().toLocaleTimeString()} · Saved events, not a live video feed`;}catch(e){$('connection').textContent='Could not refresh. Check your connection to the Pi.';}}
$('refresh').onclick=refresh;$('kind').onchange=()=>{offset=0;refresh();};$('verdict').onchange=renderDecisions;$('reviewFilter').onchange=renderDecisions;$('previous').onclick=()=>{offset=Math.max(0,offset-24);refresh();};$('next').onclick=()=>{offset+=24;refresh();};
for(const [tab,other,section,hidden] of [['capturesTab','decisionsTab','captures','decisions'],['decisionsTab','capturesTab','decisions','captures']])$(tab).onclick=()=>{$(tab).setAttribute('aria-pressed','true');$(other).setAttribute('aria-pressed','false');$(section).hidden=false;$(hidden).hidden=true;};
refresh();setInterval(refresh,30000);
