export class VoiceEngine {
  constructor() {
    this.worker = new Worker('/voice/inference-worker.js', {type:'module'});
    this.pending = new Map(); this.sequence = 0;
    this.worker.onmessage = ({data}) => {
      const task = this.pending.get(data.id); if (!task) return;
      clearTimeout(task.timer); this.pending.delete(data.id);
      if(data.ok)task.resolve(data);else task.reject(new Error(data.error || '음성 처리에 실패했습니다.'));
    };
    this.worker.onerror = () => this.close('음성 엔진을 실행하지 못했습니다. 로컬 모델 설치를 확인해 주세요.');
  }
  request(type, audio) {
    if(!this.worker)return Promise.reject(new Error('음성 엔진 연결이 종료되었습니다. 페이지를 다시 열어주세요.'));
    return new Promise((resolve,reject)=>{
      const id=++this.sequence;
      const timer=setTimeout(()=>{this.close('음성 처리가 제한 시간을 넘었습니다. 듣기를 중지하고 다시 준비해 주세요.');},180000);
      this.pending.set(id,{resolve,reject,timer});
      const message={id,type};
      if(audio){message.audio=audio.slice();this.worker.postMessage(message,[message.audio.buffer]);}
      else this.worker.postMessage(message);
    });
  }
  close(reason='음성 엔진을 중지했습니다.') {
    this.worker?.terminate();this.worker=null;
    for(const task of this.pending.values()){clearTimeout(task.timer);task.reject(new Error(reason));}
    this.pending.clear();
  }
}
