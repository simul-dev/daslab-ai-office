import os from 'node:os';
import path from 'node:path';
export const vendorRoot = path.resolve(process.env.PIXEL_AGENTS_VENDOR || path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), '.local', 'share'), 'DASLab', 'ai-office-next', 'vendor', 'pixel-agents'));
