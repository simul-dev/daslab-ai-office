import {createProfile,readProfile,signalQuality,assessSegment,verifyEmbeddings,verificationWindows,WakeGate,riskHint} from './policy.mjs';
import {Microphone,Utterances,resample,join} from './audio.mjs';
import {VoiceEngine} from './rpc.mjs';

const $=selector=>document.querySelector(selector);
const STORAGE_KEY='daslab.owner-voice.v1';
const phrases=[
  'GPT야, 다스랩의 오늘 업무를 정리하고 중요한 일부터 알려줘.',
  '나는 다스랩 대표입니다. 미션의 진행 상황과 남은 일을 간단히 보고해줘.',
  '연구개발과 마케팅 담당자가 다음에 해야 할 일을 확인해줘.',
];
const rejection={overlap:'겹친 목소리가 감지되어 버렸습니다.',speaker_change:'화자 교대가 감지되어 버렸습니다.',
  short_speech:'목소리를 확인하기에 너무 짧습니다. 호출어와 요청을 한 문장으로 말해주세요.',
  uncertain_speech:'목소리가 불명확해 버렸습니다. 조용한 곳에서 다시 말해주세요.',quiet_audio:'소리가 너무 작습니다.',
  clipped_audio:'소리가 일그러져 버렸습니다. 마이크에서 조금 떨어져 말해주세요.',different_voice:'등록된 목소리와 일치하지 않아 버렸습니다.',
  unknown_speech:'발화를 확인할 수 없어 버렸습니다.',unknown_voice:'목소리를 확인할 수 없어 버렸습니다.'};
let profile=null,engine=null,ready=false,listening=false,starting=false,processing=false,speaking=false,epoch=0;
let enrollment=[],captureCancel=null,recording=false,submitting=false,draft=null,assetsReady=false;
const gate=new WakeGate(),mic=new Microphone();
try{profile=readProfile(JSON.parse(localStorage.getItem(STORAGE_KEY)||'null'));}catch{}
const utterances=new Utterances(handleAudio,level=>{$('#level-fill').style.width=level*100+'%';},()=>{
  gate.reset();state('조금 짧게 말씀해 주세요.','12초보다 긴 발화는 저장하지 않고 버렸습니다.');
});

function state(title,detail){$('#state-title').textContent=title;$('#state-detail').textContent=detail;}
function error(message){$('#error').textContent=message;$('#error').hidden=!message;}
function render(){
  $('#prepare').hidden=ready;$('#prepare').disabled=!assetsReady||processing;
  $('#listen').hidden=!ready||listening;$('#listen').disabled=!profile||processing||recording||starting;
  $('#stop').hidden=!listening&&!starting;$('#enroll').disabled=!ready||processing||recording||submitting||starting;
  $('#forget').hidden=!profile;$('#forget').disabled=submitting;
  $('#profile-status').textContent=profile?'등록됨 · 이 브라우저에만 저장':'다른 문장 3개 · 약 30초';
  $('#setup-count').textContent=profile?'목소리 등록됨':'등록 전';
  $('#enroll').textContent=profile?'목소리 다시 등록하기 →':'목소리 등록하기 →';
  $('#privacy-state').textContent=listening||recording?'기기에서만 듣는 중':'마이크 꺼짐';
  $('#orb').classList.toggle('listening',listening&&!processing);
  $('#orb').classList.toggle('busy',processing);
}
async function loadAssets(){
  try{
    const response=await fetch('/voice/assets-status.json');
    if(!response.ok)throw new Error('음성 모델 설치가 필요합니다. 프로젝트에서 npm run voice:assets를 실행해 주세요.');
    const assets=await response.json();assetsReady=assets.ready===true;
    if(!assetsReady)throw new Error('음성 모델이 모두 설치되지 않았습니다. 설치 상태를 확인해 주세요.');
    $('#engine-status').textContent='설치됨 · 브라우저 안에서 실행';
    state(profile?'대표님 목소리가 등록되어 있습니다.':'먼저, 대표님 목소리를 알려주세요.','음성 엔진을 준비하면 '+(profile?'로컬 듣기를 시작할 수 있습니다.':'3개의 문장으로 목소리를 등록할 수 있습니다.'));
  }catch(e){$('#engine-status').textContent='설치 확인 필요';error(e.message);state('음성 엔진을 준비해야 합니다.','미션 대시보드는 계속 사용할 수 있습니다.');}
  render();
}
async function prepare(){
  processing=true;error('');render();state('음성 엔진을 준비하고 있어요.','모델을 이 브라우저의 메모리로 불러옵니다. 마이크는 아직 꺼져 있습니다.');
  try{engine?.close();engine=new VoiceEngine();await engine.request('init');ready=true;state(profile?'듣기를 시작할 준비가 됐습니다.':'이제 목소리를 등록해 주세요.',profile?'로컬 듣기를 누른 뒤 호출어와 요청을 말해주세요.':'오른쪽 목소리 등록하기에서 세 문장을 읽어주세요.');}
  catch(e){ready=false;error(e.message);state('엔진을 준비하지 못했습니다.','원음이 외부로 전송되거나 다른 엔진으로 전환되지는 않습니다.');}
  finally{processing=false;render();}
}
async function stopListening(message=true){
  ++epoch;listening=false;gate.reset();utterances.reset();captureCancel?.();captureCancel=null;
  window.speechSynthesis?.cancel();speaking=false;await mic.stop();$('#level-fill').style.width='0';render();
  if(message)state('듣기를 중지했습니다.','마이크가 꺼졌습니다. 다시 시작하려면 로컬 듣기를 눌러주세요.');
}
async function startListening(){
  if(!profile||!ready||processing||recording||starting)return;
  if(document.hidden){error('이 페이지를 화면에 띄운 상태에서 시작해 주세요.');return;}
  error('');const generation=++epoch;starting=true;gate.reset();utterances.reset();render();
  try{
    await mic.start((chunk,rate)=>{
      if(!listening||processing||speaking){chunk.fill(0);return;}
      utterances.push(chunk,rate);
    });
    if(generation!==epoch||document.hidden||!profile){await mic.stop();return;}
    listening=true;state('대표님, 말씀하세요.','요청을 한 문장으로 말해주세요. 호출어 없이도 입력하며 요청을 인식하면 마이크를 끕니다.');render();
  }catch(e){await stopListening(false);error(e.name==='NotAllowedError'?'마이크 사용을 허용해야 목소리를 등록하고 들을 수 있습니다.':e.message);}
  finally{starting=false;render();}
}
function say(text){
  if(!('speechSynthesis' in window))return;
  const voice=speechSynthesis.getVoices().find(v=>v.localService&&v.lang.toLowerCase().startsWith('ko'));
  if(!voice)return; // Never use a network voice as an implicit fallback.
  speaking=true;utterances.reset();speechSynthesis.cancel();
  const utterance=new SpeechSynthesisUtterance(text);utterance.voice=voice;utterance.lang=voice.lang;
  const finish=()=>{speaking=false;utterances.reset();};utterance.onend=finish;utterance.onerror=finish;
  speechSynthesis.speak(utterance);
}
async function handleAudio(raw,rate){
  if(processing||!listening){raw.fill(0);return;}
  processing=true;const generation=epoch;let audio;
  render();state('목소리와 발화를 확인하고 있어요.','확인하는 동안 새로 들리는 소리는 저장하지 않습니다.');
  try{
    audio=await resample(raw,rate);raw.fill(0);
    const quality=signalQuality(audio);if(!quality.ok)throw new Error(rejection[quality.reason]||'발화를 확인할 수 없습니다.');
    const segmentation=await engine.request('segment',audio);
    if(generation!==epoch||!listening)return;
    const segmentCheck=assessSegment(segmentation);if(!segmentCheck.ok)throw new Error(rejection[segmentCheck.reason]);
    const embeddings=[];
    for(const window of verificationWindows(audio)){
      try{embeddings.push((await engine.request('embed',window)).embedding);}finally{window.fill(0);}
      if(generation!==epoch||!listening)return;
    }
    const verified=verifyEmbeddings(profile,embeddings);
    if(!verified.ok)throw new Error(rejection[verified.reason]||'등록된 목소리로 확인하지 못했습니다.');
    state('대표님의 요청을 정리하고 있어요.','한국어 인식도 이 브라우저 안에서 처리합니다. 처음에는 모델 준비에 시간이 걸릴 수 있습니다.');
    const transcript=await engine.request('transcribe',audio);
    if(generation!==epoch||!listening||document.hidden)return;
    const decision=gate.accept(transcript.text,true);
    // Listening is explicitly started by the owner. A wake word is optional.
    if(decision.type!=='wake'&&decision.type!=='command'&&transcript.text?.trim()){
      decision.type='command';decision.text=transcript.text.trim();
    }
    if(decision.type==='wake'){
      state('네, 어떤 일을 도와드릴까요?','15초 안에 요청을 말해주세요. 다음 발화의 목소리도 다시 확인합니다.');say('네, 말씀하세요.');
    }else if(decision.type==='command'){
      if(segmentation.speechSeconds<1.5)throw new Error(rejection.short_speech);
      showDraft(decision.text);await stopListening(false);
      state('음성 입력이 준비됐습니다.','아래 내용을 조직 화면으로 가져오세요. 마이크는 꺼졌습니다.');say('요청을 정리했습니다. 내용을 확인해 주세요.');
    }else state('호출어를 기다리고 있어요.','“GPT야”로 시작하지 않은 발화는 버렸습니다.');
  }catch(e){
    gate.reset();
    if(generation===epoch&&listening){
      if(!engine?.worker){ready=false;await stopListening(false);error(e.message);state('음성 엔진을 다시 준비해 주세요.','인식 결과를 넘기지 않고 마이크를 껐습니다.');}
      else state('이 발화는 넘기지 않았습니다.',e.message);
    }
  }finally{raw.fill(0);audio?.fill(0);processing=false;utterances.reset();render();}
}
function showDraft(text){
  draft={text};$('#command').value=text;$('#command-card').hidden=false;$('#submit-result').textContent='';$('#submit').disabled=false;$('#submit').textContent='조직 화면으로 가져오기 ↗';
  updateRisk();$('#command-card').scrollIntoView({behavior:'smooth',block:'nearest'});
}
function clearDraft(){draft=null;$('#command').value='';$('#command-card').hidden=true;$('#submit-result').textContent='';}
function updateRisk(){const hint=riskHint($('#command').value);$('#risk-note').textContent=hint;$('#risk-note').hidden=!hint;}
async function submit(){
  if(!draft||submitting||!$('#command').value.trim())return;
  submitting=true;$('#submit').disabled=true;$('#discard').disabled=true;$('#command').disabled=true;error('');render();
  try{
    const recipient=new URLSearchParams(location.search).get('employee');
    sessionStorage.setItem('daslab.office.voiceDraft',JSON.stringify({text:$('#command').value.trim(),employee_id:['assistant','das-pm','das-rd','das-mkt','das-sales'].includes(recipient)?recipient:'assistant',created_at:new Date().toISOString()}));
    draft=null;await stopListening(false);location.assign('/');
  }catch(e){
    $('#submit').disabled=false;
    error('입력 내용을 옮기지 못했습니다. 아래 텍스트를 복사해 조직 화면에서 입력해 주세요. AI 실행은 시작하지 않았습니다. '+e.message);
  }
  finally{submitting=false;$('#discard').disabled=false;$('#command').disabled=false;render();}
}
async function briefing(){
  try{
    const response=await fetch('/api/org');if(!response.ok)throw new Error();
    const {missions=[],metrics={}}=await response.json();
    $('#briefing').textContent=missions.length?`업무 ${missions.length}개 · ${metrics.active||0}개 실행 중 · 결과 보고 ${metrics.reports||0}개. 조직 화면에서 담당 직원과 남은 일을 확인할 수 있습니다.`:'아직 새 조직에 맡긴 업무가 없습니다. 화면에서 직접 입력하거나 음성으로 시작할 수 있습니다.';
  }catch{$('#briefing').textContent='미션 현황을 불러오지 못했습니다. 음성은 기기에서 계속 처리할 수 있습니다.';}
}
function setPhrase(){
  $('#enroll-step').textContent=`${enrollment.length+1} / 3`;
  $('#enroll-phrase').textContent=phrases[enrollment.length]||'';
  $('#record').textContent='8초 녹음하기';$('#record').disabled=false;$('#record').hidden=false;
  $('#restart-enroll').hidden=true;$('#enroll-status').textContent='버튼을 누른 뒤 자연스럽게 읽어주세요.';
}
async function openEnrollment(){
  await stopListening(false);enrollment=[];setPhrase();$('#enroll-dialog').showModal();
}
async function recordFixed(seconds,generation){
  const chunks=[];let rate=0,timer,ticker;
  try{
    await mic.start((chunk,sampleRate)=>{chunks.push(chunk);rate=sampleRate;});
    if(generation!==epoch)throw new Error('등록을 취소했습니다.');
    const start=Date.now();
    await new Promise((resolve,reject)=>{
      captureCancel=()=>reject(new Error('등록을 취소했습니다.'));
      timer=setTimeout(resolve,seconds*1000);
      ticker=setInterval(()=>{$('#enroll-status').textContent=`읽어주세요 · ${Math.max(0,Math.ceil(seconds-(Date.now()-start)/1000))}초 남음`;},200);
    });
    return await resample(join(chunks),rate);
  }finally{clearTimeout(timer);clearInterval(ticker);captureCancel=null;await mic.stop();chunks.forEach(c=>c.fill(0));}
}
async function recordEnrollment(){
  if(recording||processing)return;recording=true;processing=true;const generation=++epoch;let audio;
  $('#record').disabled=true;render();
  try{
    audio=await recordFixed(8,generation);if(generation!==epoch)return;
    $('#enroll-status').textContent='한 사람의 목소리인지 확인하고 있습니다.';
    const quality=signalQuality(audio);if(!quality.ok)throw new Error(rejection[quality.reason]||'녹음 품질을 확인해 주세요.');
    const segment=await engine.request('segment',audio);if(generation!==epoch)return;
    const segmentCheck=assessSegment(segment,2.5);if(!segmentCheck.ok)throw new Error(rejection[segmentCheck.reason]);
    const result=await engine.request('embed',audio);if(generation!==epoch)return;
    enrollment.push(result.embedding);
    if(enrollment.length===3){
      const next=createProfile(enrollment);localStorage.setItem(STORAGE_KEY,JSON.stringify(next));profile=next;
      clearDraft();enrollment=[];$('#enroll-dialog').close();state('대표님 목소리를 등록했습니다.','로컬 듣기를 시작해 직접 시험해 주세요. 실제 타인과의 구분 성능은 별도 확인이 필요합니다.');
    }else setPhrase();
  }catch(e){
    if(generation===epoch){
      if(!engine?.worker){ready=false;enrollment=[];$('#enroll-dialog').close();error(e.message);state('음성 엔진을 다시 준비해 주세요.','등록을 완료하지 않았습니다.');}
      $('#enroll-status').textContent=e.message;$('#record').disabled=false;
      if(enrollment.length===3){enrollment=[];$('#record').hidden=true;$('#restart-enroll').hidden=false;}}
  }finally{audio?.fill(0);recording=false;processing=false;render();}
}
async function closeEnrollment(){++epoch;captureCancel?.();captureCancel=null;await mic.stop();enrollment=[];$('#enroll-dialog').close();render();}
$('#prepare').addEventListener('click',prepare);$('#listen').addEventListener('click',startListening);$('#stop').addEventListener('click',()=>stopListening());
$('#enroll').addEventListener('click',openEnrollment);$('#record').addEventListener('click',recordEnrollment);$('#close-enroll').addEventListener('click',closeEnrollment);
$('#enroll-dialog').addEventListener('cancel',event=>{event.preventDefault();closeEnrollment();});$('#restart-enroll').addEventListener('click',()=>{enrollment=[];setPhrase();});
$('#forget').addEventListener('click',async()=>{await stopListening(false);localStorage.removeItem(STORAGE_KEY);profile=null;clearDraft();render();state('목소리 등록을 삭제했습니다.','이 브라우저에 저장된 음성 특징을 지웠습니다.');});
$('#command').addEventListener('input',updateRisk);$('#discard').addEventListener('click',clearDraft);$('#submit').addEventListener('click',submit);$('#brief-refresh').addEventListener('click',briefing);
document.addEventListener('visibilitychange',()=>{if(document.hidden){closeEnrollment();stopListening();}});
window.addEventListener('pagehide',()=>{++epoch;captureCancel?.();mic.stop();engine?.close();});
window.addEventListener('storage',event=>{if(event.key===STORAGE_KEY){stopListening(false);profile=null;try{profile=readProfile(JSON.parse(event.newValue));}catch{}clearDraft();render();}});
loadAssets();briefing();render();
