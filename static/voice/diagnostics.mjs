import {VoiceEngine} from './rpc.mjs';
import {cosine,assessSegment} from './policy.mjs';
const output=document.querySelector('#diagnostic-output'),state=document.querySelector('#diagnostic-state');
async function readWav(name){
  const response=await fetch('/voice/fixtures/'+name);if(!response.ok)throw new Error('공개 시험 샘플 설치가 필요합니다: python scripts/setup_voice_test_samples.py');
  const buffer=await response.arrayBuffer();
  const context=new OfflineAudioContext(1,16000,16000);
  const decoded=await context.decodeAudioData(buffer);
  return decoded.getChannelData(0).slice(0,16000*10);
}
document.querySelector('#run-diagnostics').addEventListener('click',async event=>{
  const button=event.currentTarget;button.disabled=true;output.textContent='';const engine=new VoiceEngine();let samples=[];
  const started=performance.now();
  try{
    state.textContent='실제 모델을 불러오는 중…';await engine.request('init');
    const names=['sv_speaker-1_1.wav','sv_speaker-1_2.wav','sv_speaker-2_1.wav','sv_speaker-2_2.wav'];
    samples=await Promise.all(names.map(readWav));const embeddings=[],segments=[];
    for(let i=0;i<samples.length;i++){
      state.textContent=`샘플 ${i+1}/4의 화자 특징과 겹침 검사 중…`;
      embeddings.push((await engine.request('embed',samples[i])).embedding);
      segments.push(await engine.request('segment',samples[i]));
    }
    const same=[cosine(embeddings[0],embeddings[1]),cosine(embeddings[2],embeddings[3])];
    const different=[cosine(embeddings[0],embeddings[2]),cosine(embeddings[0],embeddings[3]),cosine(embeddings[1],embeddings[2]),cosine(embeddings[1],embeddings[3])];
    state.textContent='두 화자의 동시 발화 혼합 샘플 검사 중…';
    const mixed=new Float32Array(Math.min(samples[0].length,samples[2].length));
    for(let i=0;i<mixed.length;i++)mixed[i]=(samples[0][i]+samples[2][i])*.5;
    const overlap=await engine.request('segment',mixed);mixed.fill(0);
    state.textContent='한국어 설정의 전사 엔진 실행 확인 중…';
    const transcript=await engine.request('transcribe',samples[0]);
    const result={kind:'real_public_sample_inference',sameSpeakerCosine:same,differentSpeakerCosine:different,
      sampleSeparation:Math.min(...same)>Math.max(...different),segments:segments.map(s=>({...s,policy:assessSegment(s)})),
      mixedVoice:{...overlap,policy:assessSegment(overlap)},transcription:transcript.text,
      elapsedSeconds:Math.round((performance.now()-started)/100)/10,
      limitations:'4 public recordings only. No owner enrollment, Korean wake-word validation, FAR/FRR estimate, mobile test, or replay defense validation.'};
    output.textContent=JSON.stringify(result,null,2);state.textContent='실제 모델 점검 완료 · 결과는 아래와 같습니다.';
  }catch(error){state.textContent='점검 실패';output.textContent=error.message;}
  finally{samples.forEach(s=>s.fill(0));engine.close();button.disabled=false;}
});
