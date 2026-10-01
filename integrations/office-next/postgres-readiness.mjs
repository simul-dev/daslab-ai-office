import {readFile,realpath} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';

// A connection test only: never print configuration, SQL errors or credentials.
const timer=setTimeout(()=>process.exit(1),2500);
let sql;
try {
  const [configPath,runtimePath]=process.argv.slice(2);
  if(!path.isAbsolute(configPath ?? '') || !path.isAbsolute(runtimePath ?? ''))throw new Error();
  const config=JSON.parse(await readFile(configPath,'utf8'));
  const connection=new URL(config.database.connectionString);
  if(config.database.mode!=='postgres' || !['postgres:','postgresql:'].includes(connection.protocol) ||
      connection.hostname!=='127.0.0.1' || connection.port!=='54329')throw new Error();
  const packagePath=await realpath(path.join(runtimePath,'node_modules','paperclipai','package.json'));
  const postgres=createRequire(packagePath)('postgres');
  sql=postgres(connection.href,{max:1,connect_timeout:1,idle_timeout:1,onnotice:()=>{}});
  const rows=await sql`SELECT 1 AS ready`;
  if(rows[0]?.ready!==1)throw new Error();
  process.exitCode=0;
} catch {
  process.exitCode=1;
} finally {
  if(sql)await sql.end({timeout:1}).catch(()=>{});
  clearTimeout(timer);
}
