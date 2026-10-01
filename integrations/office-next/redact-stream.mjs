import {Transform} from 'node:stream';
import {StringDecoder} from 'node:string_decoder';
// Hold enough suffix to catch a credential split across arbitrary stream chunks.
export function redactStream(secrets) {
  const values=[...new Set(secrets.filter(v=>typeof v==='string'&&v.length>0))];
  const hold=Math.max(0,...values.map(v=>v.length-1));
  const decoder=new StringDecoder('utf8');let pending='';
  const redact=text=>values.reduce((out,value)=>out.split(value).join('[REDACTED]'),text);
  return new Transform({
    transform(chunk,encoding,callback){pending=redact(pending+decoder.write(chunk));const length=Math.max(0,pending.length-hold);this.push(pending.slice(0,length));pending=pending.slice(length);callback();},
    flush(callback){this.push(redact(pending+decoder.end()));callback();}
  });
}
