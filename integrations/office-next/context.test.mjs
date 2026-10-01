import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { prepareKnowledge } from './context.mjs';

const hash = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'office-context-'));
  const sourceRoot = path.join(root,'source');
  fs.mkdirSync(path.join(sourceRoot,'knowledge'),{recursive:true});
  fs.writeFileSync(path.join(sourceRoot,'knowledge','company-charter.md'),'# 회사 지침\r\n이전 결정을 보존한다.\r\n');
  fs.writeFileSync(path.join(sourceRoot,'knowledge','daslab-team.md'),'# 직원 책임\n실제 결과를 확인한다.\n');
  return {sourceRoot,workspace:path.join(root,'isolated')};
}

test('copies exact bytes with provenance, keeps source bytes and timestamps unchanged',()=>{
  const options=fixture();
  const names=['company-charter.md','daslab-team.md'];
  const before=names.map(name=>{const file=path.join(options.sourceRoot,'knowledge',name);return {hash:hash(file),mtime:fs.statSync(file).mtimeMs};});
  const result=prepareKnowledge(options);
  assert.deepEqual(result.files.map(file=>file.name),names);
  assert.deepEqual(result.missing_optional,['quality-bar.md']);
  for(let i=0;i<names.length;i++){
    const source=path.join(options.sourceRoot,'knowledge',names[i]);
    assert.equal(hash(source),before[i].hash);
    assert.equal(fs.statSync(source).mtimeMs,before[i].mtime);
    assert.equal(hash(path.join(options.workspace,'knowledge',names[i])),before[i].hash);
    assert.equal(result.files[i].source_sha256,before[i].hash);
  }
  assert.ok(!Number.isNaN(Date.parse(result.captured_at)));
});

test('refreshes owned snapshots and preserves unrelated isolated files',()=>{
  const options=fixture(); prepareKnowledge(options);
  const extra=path.join(options.workspace,'knowledge','employee-notes.md');
  fs.writeFileSync(extra,'keep employee context');
  fs.appendFileSync(path.join(options.sourceRoot,'knowledge','company-charter.md'),'\nNew owner decision');
  fs.writeFileSync(path.join(options.sourceRoot,'knowledge','quality-bar.md'),'# Quality\nEvidence first');
  const result=prepareKnowledge(options);
  assert.equal(result.files.length,3);
  assert.equal(fs.readFileSync(extra,'utf8'),'keep employee context');
  assert.match(fs.readFileSync(path.join(options.workspace,'knowledge','company-charter.md'),'utf8'),/New owner decision/);
});

test('refuses to overwrite modified snapshots and leaves source unchanged',()=>{
  const options=fixture(); prepareKnowledge(options);
  const original=path.join(options.sourceRoot,'knowledge','company-charter.md');
  const before=hash(original);
  const target=path.join(options.workspace,'knowledge','company-charter.md');
  fs.writeFileSync(target,'new independent knowledge');
  assert.throws(()=>prepareKnowledge(options),/edited; preserving/);
  assert.equal(fs.readFileSync(target,'utf8'),'new independent knowledge');
  assert.equal(hash(original),before);
});

test('requires core sources and forbids source/workspace overlap',()=>{
  const options=fixture();
  assert.throws(()=>prepareKnowledge({...options,workspace:options.sourceRoot}),/disjoint/);
  fs.unlinkSync(path.join(options.sourceRoot,'knowledge','daslab-team.md'));
  assert.throws(()=>prepareKnowledge(options),/Required company knowledge/);
  assert.equal(fs.existsSync(path.join(options.workspace,'knowledge')),false);
});
