/** Move no live state: copy the selected methods into an accessible workspace.
 * The source installation, database, authentication, ACLs and sandbox policy
 * are untouched. Only declared method assets plus portable Bun are copied.
 */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { PIN, SKILLS, CONFIG } from './prepare.mjs';

const posix = value => value.replaceAll('\\','/');
const msys = value => posix(value).replace(/^([A-Za-z]):\//,(_,drive)=>`/${drive.toLowerCase()}/`);
const hash = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const within = (file,dir) => { const relative=path.relative(dir,file);return relative==='' || (!relative.startsWith('..'+path.sep) && relative!=='..' && !path.isAbsolute(relative)); };
const write = (file,data) => {fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,data);};
export function prepareRoleCopies({sourceRoot,workspace,rolesRoot=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..','roles')}) {
  const methods=path.join(path.resolve(workspace),'.office-methods');
  const destination=path.join(path.resolve(workspace),'roles');
  const pairs=[[posix(path.resolve(sourceRoot)),posix(methods)],[path.resolve(sourceRoot),methods]];
  const files=fs.readdirSync(rolesRoot).filter(name=>name.endsWith('.md')||name==='roles.json');
  for(const name of files) {
    let content=fs.readFileSync(path.join(rolesRoot,name),'utf8');
    for(const [from,to] of pairs) content=content.split(from).join(to);
    content=content.replace('회사 지식은 활성 작업본의','회사 지식은 현재 격리된 작업공간의');
    write(path.join(destination,name),content);
  }
  return {destination,files};
}
function filesUnder(dir) {
  return fs.readdirSync(dir,{withFileTypes:true}).flatMap(entry=>{
    const file=path.join(dir,entry.name);
    if(entry.isSymbolicLink()) throw Error(`Unexpected method asset symlink: ${file}`);
    return entry.isDirectory()?filesUnder(file):[file];
  });
}

export function relocateGstack({sourceRoot,workspace}) {
  if(!sourceRoot || !workspace) throw Error('sourceRoot and workspace are required');
  sourceRoot=path.resolve(sourceRoot);workspace=path.resolve(workspace);
  const destination=path.join(workspace,'.office-methods');
  if(within(destination,sourceRoot) || within(sourceRoot,destination)) throw Error('Method source and destination must be disjoint');
  fs.mkdirSync(workspace,{recursive:true});
  // Refuse junctions/aliases at the destination: it must be the normal user
  // directory that the approved Windows sandbox can traverse.
  if(fs.realpathSync(workspace).toLowerCase() !== workspace.toLowerCase()) throw Error('Use the physical isolated workspace path');
  if(fs.existsSync(destination) && fs.lstatSync(destination).isSymbolicLink()) throw Error('Method destination may not be a symlink');
  const sourceManifest=JSON.parse(fs.readFileSync(path.join(sourceRoot,'gstack-install.json'),'utf8'));
  if(sourceManifest.source_commit!==PIN) throw Error('Unreviewed gstack installation revision');
  if(sourceManifest.host!=='codex') throw Error('Only the prepared Codex installation can be relocated');
  if(fs.existsSync(destination) && fs.readdirSync(destination).length) {
    const marker=path.join(destination,'gstack-relocation.json');
    if(!fs.existsSync(marker)) throw Error('Refusing to overwrite an unrelated method directory');
    const prior=JSON.parse(fs.readFileSync(marker,'utf8'));
    if(prior.kind!=='ai-office-gstack-relocation' || prior.source_commit!==PIN) throw Error('Existing method destination has different provenance');
  }
  // Copy only this allowlist; source state, logs, credentials, other runtimes and
  // the vendor checkout are not imported into the worker workspace.
  const copyRoots=[['gstack-runtime','gstack-runtime'],...SKILLS.map(name=>[`skills/gstack-${name}`,`skills/gstack-${name}`])];
  const sourceFiles=[];
  for(const [from,to] of copyRoots) {
    const src=path.join(sourceRoot,from), dst=path.join(destination,to);
    if(!fs.existsSync(src)) throw Error(`Required method asset missing: ${from}`);
    sourceFiles.push(...filesUnder(src).map(file=>({file,sha256:hash(file)})));
    fs.cpSync(src,dst,{recursive:true});
  }
  const bunRelative='tools/bun-v1.4.2/bun-windows-x64/bun.exe';
  const sourceBun=path.join(sourceRoot,bunRelative), targetBun=path.join(destination,bunRelative);
  if(!fs.existsSync(sourceBun)) throw Error('Verified portable Bun binary is missing');
  write(targetBun,fs.readFileSync(sourceBun));
  const pairs=[
    [sourceRoot,destination], [posix(sourceRoot),posix(destination)], [msys(sourceRoot),msys(destination)],
    // JSON can contain escaped Windows separators inside generated examples.
    [sourceRoot.replaceAll('\\','\\\\'),destination.replaceAll('\\','\\\\')],
  ].sort((a,b)=>b[0].length-a[0].length);
  let rewrittenFiles=0;
  for(const file of filesUnder(destination)) {
    if(file.endsWith('.exe')) continue;
    const buffer=fs.readFileSync(file);
    if(buffer.includes(0)) continue;
    const original=buffer.toString('utf8');
    let changed=original;
    for(const [from,to] of pairs) changed=changed.split(from).join(to);
    if(changed!==original){write(file,changed);rewrittenFiles++;}
  }
  // State starts empty apart from the explicit no-external-services config.
  // A re-run preserves any decisions created in this destination afterwards.
  const state=path.join(destination,'state','gstack');
  const configPath=path.join(state,'config.yaml');
  if(!fs.existsSync(configPath)) write(configPath,Object.entries(CONFIG).map(([key,value])=>`${key}: ${value}\n`).join(''));
  const installation={...sourceManifest,runtime:path.join(destination,'gstack-runtime'),state,
    skills:sourceManifest.skills.map(skill=>({...skill,path:path.join(destination,'skills',`gstack-${skill.name}`,'SKILL.md'),installed_sha256:hash(path.join(destination,'skills',`gstack-${skill.name}`,'SKILL.md'))})),
    relocation:{source_installation:sourceRoot,destination,credentialFilesCopied:false,sourceStateCopied:false}};
  write(path.join(destination,'gstack-install.json'),JSON.stringify(installation,null,2)+'\n');
  // Verify exact source preservation before announcing success.
  for(const record of sourceFiles) if(hash(record.file)!==record.sha256) throw Error(`Source asset changed during relocation: ${record.file}`);
  const manifest={schema_version:1,kind:'ai-office-gstack-relocation',captured_at:new Date().toISOString(),source_commit:PIN,
    source_installation:sourceRoot,workspace,destination,selected_skills:SKILLS,
    portable_bun:{version:'1.4.2',relative_path:bunRelative,source_sha256:hash(sourceBun),installed_sha256:hash(targetBun)},
    copied_source_files:sourceFiles.length,rewritten_files:rewrittenFiles,source_assets_unchanged:true,
    copied_private_state:false,copied_credentials:false,changed_acl:false,changed_sandbox:false};
  manifest.role_copies=prepareRoleCopies({sourceRoot,workspace});
  write(path.join(destination,'gstack-relocation.json'),JSON.stringify(manifest,null,2)+'\n');
  return manifest;
}

if(process.argv[1] && path.resolve(process.argv[1])===fileURLToPath(import.meta.url)) {
  const args={};
  for(let index=2;index<process.argv.length;index+=2){
    const flag=process.argv[index];
    if(!['--source-root','--workspace'].includes(flag) || !process.argv[index+1]) throw Error('Usage: relocate-gstack.mjs --source-root <installed nextRoot> --workspace <normal user workspace>');
    args[flag==='--source-root'?'sourceRoot':'workspace']=process.argv[index+1];
  }
  const result=relocateGstack(args);
  console.log(JSON.stringify({destination:result.destination,skills:result.selected_skills,source_unchanged:result.source_assets_unchanged,rewritten_files:result.rewritten_files}));
}
