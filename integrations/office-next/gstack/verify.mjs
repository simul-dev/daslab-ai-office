/** Integration checks: real isolated helper I/O; no AI/model/network calls. */
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { CONFIG, SKILLS, PIN } from './prepare.mjs';

const root = path.resolve(process.argv[2]);
const bash = process.argv[3] || 'C:/Program Files/Git/bin/bash.exe';
const manifest = JSON.parse(fs.readFileSync(path.join(root,'gstack-install.json'),'utf8'));
assert.equal(manifest.source_commit,PIN);
assert.deepEqual(manifest.config,CONFIG);
const runtime=path.join(root,'gstack-runtime');
const installedFiles=[];
const expectedUnavailable=new Set(['browse/dist','design/dist']);
for(const name of SKILLS) {
  const file=path.join(root,'skills',`gstack-${name}`,'SKILL.md');
  const data=fs.readFileSync(file,'utf8');
  assert.match(data,/AI Office deployment policy/);
  assert.match(data,/env\.sh/);
  assert.doesNotMatch(data,/GSTACK_ROOT="\$HOME\/\.codex/);
  assert.doesNotMatch(data,/\{\{[A-Z_]+(?::[^}]*)?\}\}/);
  for(const match of data.matchAll(/\$GSTACK_(ROOT|BIN)\/([A-Za-z0-9_.\/-]+)/g)) {
    const relative=(match[1]==='BIN'?'bin/':'')+match[2].replace(/[.]$/,'');
    if(!expectedUnavailable.has(relative)) assert.ok(fs.existsSync(path.join(runtime,relative)),`Missing reference: ${relative}`);
  }
  installedFiles.push(file);
}
fs.mkdirSync(path.join(root,'verification'),{recursive:true});
const project=fs.mkdtempSync(path.join(root,'verification','gstack-'));
let result=spawnSync('git',['init','--quiet',project],{encoding:'utf8'});
assert.equal(result.status,0,result.stderr);
const envFile=path.join(runtime,'env.sh').replaceAll('\\','/');
const script=`set -e
source "${envFile}"
"$GSTACK_BIN/gstack-skill-start" --skill plan-eng-review --model gpt --parent-pid "$PPID"
"$GSTACK_BIN/gstack-decision-log" '{"decision":"Office integration verification decision","rationale":"Check local durable recall without a model call","scope":"repo","source":"agent"}'
echo DECISIONS_BEGIN
"$GSTACK_BIN/gstack-decision-search" --query 'Office integration verification decision' --json
echo DECISIONS_END
"$GSTACK_BIN/gstack-skill-end" --skill plan-eng-review --outcome success
`;
const scriptFile=path.join(project,'verify.sh');
fs.writeFileSync(scriptFile,script,'utf8');
result=spawnSync(bash,['--noprofile','--norc',scriptFile],{cwd:project,encoding:'utf8',timeout:60000});
assert.equal(result.status,0,result.stderr || result.stdout);
for(const expected of ['SKILL_START_PROTO: 1','SESSION_KIND: spawned','TELEMETRY: off','UPDATE_CHECK: false','ARTIFACTS_SYNC: off','SKILL_END: recorded']) assert.ok(result.stdout.includes(expected),`Missing runtime status: ${expected}`);
const decisions=JSON.parse(result.stdout.split('DECISIONS_BEGIN\n')[1].split('\nDECISIONS_END')[0]);
assert.equal(decisions.length,1);
assert.equal(decisions[0].decision,'Office integration verification decision');
for(const helper of ['gstack-claude-code','gstack-office-hours-review','gstack-global-discover']) {
  const blocked=spawnSync(bash,['--noprofile','--norc',path.join(runtime,'bin',helper)],{encoding:'utf8'});
  assert.equal(blocked.status,78,`${helper} must not launch an outside model/discovery`);
}
const resultFile=path.join(root,'verification','gstack-check.json');
fs.writeFileSync(resultFile,JSON.stringify({checked_at:new Date().toISOString(),source_commit:PIN,selected_skills:SKILLS,source_references:'passed excluding documented uninstalled browser/design binaries',runtime_preamble:'passed',decision_write_read:'passed',external_helpers:'blocked',model_calls:0,scope:'installation and local helpers; actual employee usage separately verified',project},null,2)+'\n');
console.log(JSON.stringify({passed:true,skills:SKILLS.length,decision_write_read:true,external_helpers:'blocked',evidence:resultFile}));
