export async function resample(input, sampleRate) {
  if (sampleRate === 16000) return input.slice();
  // Web Audio applies an anti-aliasing resampler, unlike dropping PCM samples.
  const context = new OfflineAudioContext(1, Math.ceil(input.length * 16000 / sampleRate), 16000);
  const buffer = context.createBuffer(1, input.length, sampleRate);
  buffer.copyToChannel(input, 0);
  const source = context.createBufferSource(); source.buffer = buffer; source.connect(context.destination); source.start();
  const result = await context.startRendering();
  return result.getChannelData(0).slice();
}
export function join(chunks) {
  const result = new Float32Array(chunks.reduce((sum, a) => sum + a.length, 0));
  let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.length; }
  return result;
}
export class Microphone {
  async start(onChunk) {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw new Error('마이크는 localhost 또는 HTTPS에서 사용할 수 있습니다.');
    const generation=this.generation=(this.generation||0)+1;
    let acquired;
    try {
      acquired = await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:false},video:false});
      if(generation!==this.generation){acquired.getTracks().forEach(track=>track.stop());return;}
      this.stream=acquired;
      this.context = new AudioContext();
      await this.context.audioWorklet.addModule('/voice/capture-worklet.js');
      if(generation!==this.generation)return;
      this.source = this.context.createMediaStreamSource(this.stream);
      this.node = new AudioWorkletNode(this.context, 'das-local-capture', {numberOfInputs:1,numberOfOutputs:1,channelCount:1});
      this.mute = this.context.createGain(); this.mute.gain.value = 0;
      this.source.connect(this.node); this.node.connect(this.mute); this.mute.connect(this.context.destination);
      this.node.port.onmessage = event => onChunk(event.data, this.context.sampleRate);
      await this.context.resume();
    } catch (error) { acquired?.getTracks().forEach(track=>track.stop());if(generation===this.generation)await this.stop();throw error; }
  }
  async stop() {
    this.generation=(this.generation||0)+1;
    if (this.node) this.node.port.onmessage = null;
    this.stream?.getTracks().forEach(track => track.stop());
    this.source?.disconnect(); this.node?.disconnect(); this.mute?.disconnect();
    if (this.context && this.context.state !== 'closed') await this.context.close();
    this.stream = this.context = this.source = this.node = this.mute = null;
  }
}
export class Utterances {
  constructor(onUtterance, onLevel, onTooLong) { this.done=onUtterance; this.level=onLevel; this.tooLong=onTooLong; this.reset(); }
  reset() { this.chunks=[]; this.prefix=[]; this.active=false; this.silence=0; this.seconds=0; this.voiced=0; this.suppress=false; }
  push(chunk, rate) {
    const duration=chunk.length/rate, rms=Math.sqrt(chunk.reduce((sum,v)=>sum+v*v,0)/chunk.length);
    this.level(Math.min(1,rms*12));
    const speech=rms>.008;
    if (this.suppress) { if (!speech) this.silence+=duration; else this.silence=0; if(this.silence>.7)this.reset(); return; }
    if (!this.active) {
      this.prefix.push(chunk); if(this.prefix.length>4)this.prefix.shift();
      if(!speech)return;
      this.active=true; this.chunks=this.prefix; this.prefix=[];
      this.seconds=this.chunks.reduce((n,c)=>n+c.length/rate,0);
    } else { this.chunks.push(chunk); this.seconds+=duration; }
    if(speech){this.voiced+=duration;this.silence=0;}else this.silence+=duration;
    if(this.seconds>=11.5){this.reset();this.suppress=true;this.tooLong();return;}
    if(this.silence>=.65){
      const audio=join(this.chunks), voiced=this.voiced;this.reset();
      if(voiced>=.35)this.done(audio,rate);else audio.fill(0);
    }
  }
}
