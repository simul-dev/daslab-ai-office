import path from 'node:path';

export function bootstrapWorkspace(config, defaultWorkspace) {
  const workspace=config?.workspace ?? defaultWorkspace;
  if(typeof workspace!=='string' || !path.isAbsolute(workspace)) {
    throw new Error('The saved trial workspace must be an absolute path.');
  }
  return workspace;
}

export function mergeBootstrapConfig(config, {paperclipUrl, companyId, agentIds, workspace}) {
  return {
    ...config,
    schemaVersion:1,
    paperclipUrl,
    companyId,
    agentIds:{...agentIds},
    workspace,
    actionsEnabled:config?.actionsEnabled===true,
  };
}
