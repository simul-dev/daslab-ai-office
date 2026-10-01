import test from 'node:test';import assert from 'node:assert/strict';
import {redactStream} from './redact-stream.mjs';
async function run(parts,secrets){const stream=redactStream(secrets);let result='';stream.on('data',d=>result+=d);const end=new Promise(resolve=>stream.on('end',resolve));for(const p of parts)stream.write(p);stream.end();await end;return result;}
test('redacts a runtime credential split at every possible boundary',async()=>{const secret='synthetic-test-token-123';for(let i=1;i<secret.length;i++)assert.equal(await run(['before '+secret.slice(0,i),secret.slice(i)+' after'],[secret]),'before [REDACTED] after');});
test('preserves unicode and final output while replacing repeated secrets',async()=>{const bytes=Buffer.from('직원: token 보고 token 끝');assert.equal(await run([...bytes].map(x=>Buffer.from([x])),['token']),'직원: [REDACTED] 보고 [REDACTED] 끝');});
