/** Pinned, selected Codex-rendered gstack methods. No upstream setup/login. */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

export const PIN = '96764e80a641e28141ec8297223768029f5bf483';
export const SKILLS = ['office-hours', 'plan-ceo-review', 'plan-eng-review', 'review', 'retro'];
export const CONFIG = {
  telemetry: 'off', auto_upgrade: 'false', update_check: 'false',
  artifacts_sync_mode: 'off', artifacts_sync_mode_prompted: 'true',
  codex_reviews: 'disabled', design_detector: 'off', gstack_contributor: 'false',
  proactive: 'true', skill_prefix: 'true', routing_declined: 'true',
  plan_tune_hooks: 'no', timeline_stop_hook: 'no', memorable_recall: 'off', explain_level: 'terse',
};
const posix = value => value.replaceAll('\\', '/');
const msys = value => posix(value).replace(/^([A-Za-z]):\//, (_, drive) => `/${drive.toLowerCase()}/`);
const sha = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const text = file => fs.readFileSync(file, 'utf8').replaceAll('\r\n', '\n');
const write = (file, value) => { fs.mkdirSync(path.dirname(file), {recursive: true}); fs.writeFileSync(file, value, 'utf8'); };

const POLICY = `

## AI Office deployment policy (local integration)

This is a Codex-rendered methodology skill, pinned and isolated for AI Office.
The owner's current request, company permissions, and role instructions take
precedence. Use existing company records before asking questions. A delegated
worker escalates missing decisions to its PM in the existing task; it does not
interrogate the owner or turn assumptions into facts. Ordinary reversible
implementation choices stay with the assigned worker/PM.

Use only the existing Codex subscription execution supplied by the office.
Outside Claude/API reviews, nested model CLI launches, automatic upgrades,
telemetry, artifact sync and global setup are disabled. Do not run these even
if an upstream optional section suggests them. Record outside-review coverage
as disabled, and use the separately assigned QA agent for independent review.
Global retrospective session discovery is also disabled; examine only the
assigned company's task records and the explicitly scoped project history.
Browser/CSO/shipping skills and their binary runtimes are not installed here;
use the office's available verification tools or report the exact missing tool.
No role/skill grants publication, customer contact, spending or new credentials.

Before each shell invocation of a gstack helper, source the adjacent runtime
env.sh with Git Bash. Do not change HOME or CODEX_HOME. The full upstream text
below is retained for traceability; load only the phase needed for this task.
`;

export function adapt(value, runtime, state) {
  value = value.replace(
    /_ROOT=\$\(git rev-parse --show-toplevel 2>\/dev\/null\)\nGSTACK_ROOT="\$HOME\/\.codex\/skills\/gstack"\n\[ -n "\$_ROOT" \].*?\n/g,
    `. "${posix(path.join(runtime, 'env.sh'))}"\n`,
  );
  value = value.replaceAll('GSTACK_ROOT="${CODEX_HOME:-$HOME/.codex}/skills/gstack"', `GSTACK_ROOT="${posix(runtime)}"`);
  value = value.replaceAll('"$HOME/.gstack', `"${posix(state)}`);
  value = value.replaceAll('~/.gstack', posix(state));
  const marker = value.indexOf('\n---', 4) + '\n---'.length;
  return value.slice(0, marker) + POLICY + value.slice(marker);
}

function walk(dir) {
  return fs.readdirSync(dir, {withFileTypes:true}).flatMap(entry => {
    const target = path.join(dir, entry.name);
    if (entry.isSymbolicLink()) throw Error(`Unexpected symlink: ${target}`);
    return entry.isDirectory() ? walk(target) : [target];
  });
}

export function prepare({source, root, bun}) {
  [source, root, bun] = [source, root, bun].map(value => path.resolve(value));
  if (source !== path.join(root, 'vendor', 'gstack')) throw Error('Source must be root/vendor/gstack');
  const actual = execFileSync('git', ['-C',source,'rev-parse','HEAD'], {encoding:'utf8'}).trim();
  if (actual !== PIN) throw Error(`Unreviewed gstack revision: ${actual}`);
  const originals = path.join(root, 'gstack-source-skills');
  for (const name of SKILLS) {
    if (text(path.join(originals,name,'SKILL.md')) !== text(path.join(source,name,'SKILL.md'))) {
      throw Error(`Skill-installer source mismatch: ${name}`);
    }
  }
  const render = path.join(root,'gstack-render');
  // Explicit exported entry point avoids upstream import.meta.main doing
  // nothing with Bun 1.4.2 on this Windows host.
  const script = 'import {main} from "./scripts/gen-skill-docs.ts"; process.exitCode = await main(["--host","codex","--out-dir",process.argv[1]]);';
  execFileSync(bun,['-e',script,render], {cwd:source,encoding:'utf8',stdio:['ignore','pipe','pipe']});
  const generated = path.join(render,'.agents','skills');
  if (!fs.existsSync(path.join(generated,'gstack-office-hours','SKILL.md'))) throw Error('Generator produced no skill');
  const runtime = path.join(root,'gstack-runtime'), state = path.join(root,'state','gstack'), skills = path.join(root,'skills');
  fs.mkdirSync(runtime,{recursive:true});
  for (const name of new Set(['bin','lib','docs','scripts','review',...SKILLS])) {
    fs.cpSync(path.join(source,name),path.join(runtime,name),{recursive:true});
  }
  for (const name of ['ETHOS.md','LICENSE','VERSION']) {
    if (fs.existsSync(path.join(source,name))) fs.copyFileSync(path.join(source,name),path.join(runtime,name));
  }
  fs.mkdirSync(path.join(runtime,'browse','bin'),{recursive:true});
  fs.copyFileSync(path.join(source,'browse','bin','remote-slug'),path.join(runtime,'browse','bin','remote-slug'));
  // Git's checkout can be CRLF; executable shell scripts must retain LF.
  for (const file of walk(runtime)) {
    if (file.startsWith(path.join(runtime,'bin') + path.sep) || /\.(md|ts|sh)$/.test(file)) write(file,text(file));
  }
  write(path.join(runtime,'browse','bin','remote-slug'),text(path.join(runtime,'browse','bin','remote-slug')));
  // Disable unrelated global-profile discovery in the preamble. This does not
  // read/copy those profiles or alter the immutable upstream checkout.
  const preamble = path.join(runtime,'bin','gstack-skill-start');
  write(preamble,text(preamble)
    .replaceAll('$HOME/.claude.json','${GSTACK_HOME}/disabled-claude.json')
    .replaceAll('$HOME/.gbrain/config.json','${GSTACK_HOME}/disabled-gbrain.json')
    .replaceAll('$HOME/.gstack-artifacts-remote.txt','${GSTACK_HOME}/disabled-artifacts-remote.txt')
    .replaceAll('$HOME/.gstack-brain-remote.txt','${GSTACK_HOME}/disabled-brain-remote.txt'));
  write(path.join(state,'config.yaml'),Object.entries(CONFIG).map(([key,value])=>`${key}: ${value}\n`).join(''));
  write(path.join(runtime,'env.sh'),[
    `export GSTACK_ROOT="${msys(runtime)}"`, `export GSTACK_HOME="${msys(state)}"`,
    'export GSTACK_STATE_ROOT="$GSTACK_HOME"', 'export GSTACK_STATE_DIR="$GSTACK_HOME"',
    'export GSTACK_BIN="$GSTACK_ROOT/bin"', 'export GSTACK_SESSION_KIND=spawned',
    `export PATH="${msys(path.dirname(bun))}:$GSTACK_BIN:$PATH"`, '',
  ].join('\n'));
  // Defense in depth for selected workflows. Immutable vendor files remain.
  for (const name of ['gstack-claude-code','gstack-office-hours-review','gstack-codex-probe','gstack-global-discover']) {
    write(path.join(runtime,'bin',name),'#!/usr/bin/env bash\nprintf "%s\\n" "AI Office: outside model execution disabled; use assigned QA." >&2\nexit 78\n');
  }
  const manifestSkills = [];
  for (const name of SKILLS) {
    const src = path.join(generated,`gstack-${name}`), dst = path.join(skills,`gstack-${name}`);
    // Assets/templates first, generated host files second.
    fs.cpSync(path.join(originals,name),dst,{recursive:true});
    fs.cpSync(src,dst,{recursive:true});
    for (const file of walk(src).filter(file => file.endsWith('.md'))) {
      write(path.join(dst,path.relative(src,file)),adapt(text(file),runtime,state));
    }
    fs.copyFileSync(path.join(dst,'SKILL.md'),path.join(runtime,name,'SKILL.md'));
    // Codex upstream inlines these sections. A few fallback references still
    // point to source section files; redirect them to the complete Codex host
    // document rather than shipping Claude-only executable section prose.
    const sections = path.join(runtime,name,'sections');
    if (fs.existsSync(sections)) {
      for (const file of walk(sections).filter(file => file.endsWith('.md'))) {
        const title = text(file).split('\n').find(line => /^#{1,3} /.test(line)) || path.basename(file);
        const pointer = `# Codex section reference\n\nUpstream Codex host inlines this methodology. Read the section beginning \`${title}\` in \`${posix(path.join(dst,'SKILL.md'))}\`. The immutable upstream source and templates remain in vendor/gstack. The AI Office deployment policy in that skill takes precedence.\n`;
        write(file,pointer);
        write(path.join(dst,'sections',path.relative(sections,file)),pointer);
      }
    }
    manifestSkills.push({name,path:path.join(dst,'SKILL.md'),source_sha256:sha(path.join(originals,name,'SKILL.md')),installed_sha256:sha(path.join(dst,'SKILL.md'))});
  }
  const manifest = {schema_version:1,source_url:'https://github.com/garrytan/gstack',source_commit:PIN,license:'MIT',host:'codex',skills:manifestSkills,runtime,state,config:CONFIG,
    capability:'selected methodologies and local bookkeeping helpers',excluded:['global install','external model reviews','browser binary','CSO','shipping','telemetry','auto-update'],authentication_touched:false};
  write(path.join(root,'gstack-install.json'),JSON.stringify(manifest,null,2)+'\n');
  return manifest;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const options = {};
  for (let i=2;i<process.argv.length;i+=2) options[process.argv[i].replace(/^--/,'')] = process.argv[i+1];
  if (!options.source || !options.root || !options.bun) throw Error('Required: --source --root --bun');
  const result = prepare(options);
  console.log(JSON.stringify({installed:SKILLS,root:options.root,source_commit:result.source_commit}));
}
