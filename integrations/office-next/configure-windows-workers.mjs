import {readFile,copyFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const here=path.dirname(fileURLToPath(import.meta.url));
const cfg=JSON.parse(await readFile(path.join(process.env.LOCALAPPDATA,'DASLab','ai-office-next','office.json'),'utf8'));
const codex=process.argv[2];if(!codex||!path.isAbsolute(codex))throw new Error('Existing absolute Codex CLI path required.');
await copyFile(path.join(here,'worker-api.mjs'),path.join(cfg.workspace,'worker-api.mjs'));
for(const [slug,id] of Object.entries(cfg.agentIds)){
 const r=await fetch(`${cfg.paperclipUrl}/api/agents/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({adapterType:'process',adapterConfig:{command:process.execPath,args:[path.join(here,'codex-worker.mjs'),slug,codex],cwd:cfg.workspace,timeoutSec:600,graceSec:15},runtimeConfig:{heartbeat:{enabled:false,intervalSec:0,wakeOnDemand:true,maxConcurrentRuns:1}}})});
 if(!r.ok)throw new Error(`Worker config ${slug}: ${r.status}: ${(await r.text()).slice(0,500)}`);
 console.log(`${slug}: configured subscription CLI process in isolated workspace`);
}
