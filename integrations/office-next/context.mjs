/** Read-only source export of selected company knowledge into the trial office. */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const FILES = [
  { name: 'company-charter.md', required: true },
  { name: 'daslab-team.md', required: true },
  { name: 'quality-bar.md', required: false },
];
const MANIFEST = 'snapshot-provenance.json';
const sha256 = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const isWithin = (candidate, parent) => {
  const rel = path.relative(parent, candidate);
  return rel === '' || (!rel.startsWith(`..${path.sep}`) && rel !== '..' && !path.isAbsolute(rel));
};

function rejectLink(target) {
  if (fs.existsSync(target) && fs.lstatSync(target).isSymbolicLink()) {
    throw Error(`Knowledge snapshot cannot use a symbolic link: ${target}`);
  }
}

function writeAtomic(target, bytes) {
  const temp = `${target}.${crypto.randomUUID()}.tmp`;
  fs.writeFileSync(temp, bytes, { flag: 'wx' });
  try { fs.renameSync(temp, target); }
  catch (error) { fs.unlinkSync(temp); throw error; }
}

export function prepareKnowledge({ sourceRoot, workspace }) {
  if (!sourceRoot || !workspace) throw Error('sourceRoot and workspace are required');
  sourceRoot = fs.realpathSync(path.resolve(sourceRoot));
  workspace = path.resolve(workspace);
  rejectLink(workspace);
  if (fs.existsSync(workspace)) workspace = fs.realpathSync(workspace);
  if (isWithin(workspace, sourceRoot) || isWithin(sourceRoot, workspace)) {
    throw Error('Source and isolated workspace must be disjoint');
  }
  const destination = path.join(workspace, 'knowledge');
  rejectLink(destination);
  const manifestPath = path.join(destination, MANIFEST);
  rejectLink(manifestPath);
  const previous = fs.existsSync(manifestPath)
    ? JSON.parse(fs.readFileSync(manifestPath, 'utf8')) : null;
  if (previous && (previous.schema_version !== 1 || previous.kind !== 'ai-office-knowledge-snapshot')) {
    throw Error('Existing knowledge manifest is not owned by this snapshot exporter');
  }
  const prepared = [];
  const missingOptional = [];
  // Read and validate everything before writing any snapshot.
  for (const item of FILES) {
    const original = path.join(sourceRoot, 'knowledge', item.name);
    const target = path.join(destination, item.name);
    rejectLink(original);
    rejectLink(target);
    if (!fs.existsSync(original)) {
      if (item.required) throw Error(`Required company knowledge is missing: ${item.name}`);
      if (fs.existsSync(target)) throw Error(`Source disappeared for existing snapshot: ${item.name}`);
      missingOptional.push(item.name);
      continue;
    }
    const bytes = fs.readFileSync(original);
    const hash = sha256(bytes);
    if (fs.existsSync(target)) {
      const currentHash = sha256(fs.readFileSync(target));
      const priorHash = previous?.files?.find(file => file.name === item.name)?.snapshot_sha256;
      if (currentHash !== hash && currentHash !== priorHash) {
        throw Error(`Isolated knowledge was edited; preserving it: ${item.name}`);
      }
    }
    prepared.push({ bytes, target, record: {
      name: item.name, source_path: original,
      source_mtime_utc: fs.statSync(original).mtime.toISOString(),
      source_sha256: hash, snapshot_sha256: hash,
      relative_path: `knowledge/${item.name}`,
    }});
  }
  fs.mkdirSync(destination, { recursive: true });
  // Validate the created directory's real location before writing any file.
  const actualDestination = fs.realpathSync(destination);
  if (isWithin(actualDestination, sourceRoot) || !isWithin(actualDestination, fs.realpathSync(workspace))) {
    throw Error('Knowledge destination resolved outside the isolated workspace');
  }
  const capturedAt = new Date().toISOString();
  for (const item of prepared) writeAtomic(item.target, item.bytes);
  const manifest = {
    schema_version: 1, kind: 'ai-office-knowledge-snapshot', captured_at: capturedAt,
    source_root: sourceRoot, workspace, source_mutated: false,
    note: 'Point-in-time context only. It does not import operational DBs, credentials, schedules or prove employee execution.',
    files: prepared.map(item => item.record), missing_optional: missingOptional,
  };
  writeAtomic(manifestPath, JSON.stringify(manifest, null, 2) + '\n');
  return { ...manifest, manifest_path: manifestPath };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const options = {};
  for (let index = 2; index < process.argv.length; index += 2) {
    const name = process.argv[index];
    if (!['--source-root', '--workspace'].includes(name) || !process.argv[index + 1]) {
      throw Error('Usage: context.mjs --source-root <existing checkout> --workspace <isolated workspace>');
    }
    options[name === '--source-root' ? 'sourceRoot' : 'workspace'] = process.argv[index + 1];
  }
  const result = prepareKnowledge(options);
  console.log(JSON.stringify({ copied: result.files.map(file => file.name), manifest: result.manifest_path, source_mutated: false }));
}
