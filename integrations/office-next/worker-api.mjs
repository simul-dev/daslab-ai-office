// Read runtime credentials only in memory; never include them in output or argv.
import {readFile} from 'node:fs/promises';
const [method,route,bodyFile]=process.argv.slice(2);
if(!process.env.PAPERCLIP_API_KEY || !process.env.PAPERCLIP_RUN_ID)throw new Error('A run-scoped Paperclip identity is required.');
if(!['GET','POST','PATCH'].includes(method)||!route?.startsWith('/api/')||route.startsWith('//'))throw new Error('Use GET, POST or PATCH with an /api/ path.');
const base=new URL(process.env.PAPERCLIP_API_URL);
if(!['localhost','127.0.0.1'].includes(base.hostname))throw new Error('Local Paperclip is required.');
const body=bodyFile?await readFile(bodyFile,'utf8'):undefined;
if(body)JSON.parse(body);
const r=await fetch(new URL(route,base),{method,headers:{'Content-Type':'application/json',Authorization:`Bearer ${process.env.PAPERCLIP_API_KEY}`,'X-Paperclip-Run-Id':process.env.PAPERCLIP_RUN_ID},body,signal:AbortSignal.timeout(15000)});
const result=await r.text();
if(!r.ok){console.error(`Paperclip ${r.status}: ${result.slice(0,2000)}`);process.exitCode=1;}else console.log(result);
