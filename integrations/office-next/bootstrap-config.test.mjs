import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import {bootstrapWorkspace,mergeBootstrapConfig} from './bootstrap-config.mjs';

const defaultWorkspace=path.resolve('default-trial-workspace');
const savedWorkspace=path.resolve('previously-selected-workspace');

test('first setup uses the default workspace and keeps actions disabled',()=>{
  const workspace=bootstrapWorkspace(undefined,defaultWorkspace);
  const result=mergeBootstrapConfig(undefined,{paperclipUrl:'http://127.0.0.1:3101',companyId:'company-1',agentIds:{pm:'pm-1'},workspace});
  assert.equal(result.workspace,defaultWorkspace);
  assert.equal(result.actionsEnabled,false);
});

test('repeated setup preserves the workspace, action decision, artifact and future local settings',()=>{
  const saved={schemaVersion:1,paperclipUrl:'http://127.0.0.1:3101',companyId:'company-1',agentIds:{pm:'pm-1'},workspace:savedWorkspace,actionsEnabled:true,demoArtifactPath:'demo/index.html',localPreference:{layout:'office'}};
  const ids={...saved.agentIds,qa:'qa-1'};
  const workspace=bootstrapWorkspace(saved,defaultWorkspace);
  const result=mergeBootstrapConfig(saved,{paperclipUrl:saved.paperclipUrl,companyId:saved.companyId,agentIds:ids,workspace});
  assert.equal(result.workspace,savedWorkspace);
  assert.equal(result.actionsEnabled,true);
  assert.equal(result.demoArtifactPath,saved.demoArtifactPath);
  assert.deepEqual(result.localPreference,saved.localPreference);
  assert.deepEqual(result.agentIds,{pm:'pm-1',qa:'qa-1'});
  assert.deepEqual(saved.agentIds,{pm:'pm-1'});
  ids.developer='developer-1';
  assert.equal(result.agentIds.developer,undefined);
});

test('a saved disabled state is preserved and a corrupt workspace cannot silently relocate the trial',()=>{
  const result=mergeBootstrapConfig({actionsEnabled:false},{paperclipUrl:'http://127.0.0.1:3101',companyId:'company-1',agentIds:{},workspace:savedWorkspace});
  assert.equal(result.actionsEnabled,false);
  for(const workspace of ['', 'relative/workspace', 123]) {
    assert.throws(()=>bootstrapWorkspace({workspace},defaultWorkspace),/absolute path/);
  }
});
