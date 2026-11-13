'use strict';
// Local noDB test harness: evaluate the UNCHANGED executable wrapper body.
// Only absolute /storage-secrets paths map to real private fixture files.
// This VM is not evidence about hostile initial Node startup/preload behavior.
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const source = fs.readFileSync(process.argv[2], 'utf8');
let starts = 0;
let boundary = false;
let status = 0;
const mapped = value => {
  if (value === '/storage-secrets') return input.root;
  if (value === '/storage-secrets/environment.json') return path.join(input.root, 'environment.json');
  if (value === '/storage-secrets/database.pgpass') return path.join(input.root, 'database.pgpass');
  throw new Error('unexpected_path');
};
const mappedFs = new Proxy(fs, {
  get(target, key) {
    if (['lstatSync', 'statSync', 'openSync', 'readFileSync'].includes(key)) {
      return (file, ...args) => target[key](mapped(file), ...args);
    }
    return typeof target[key] === 'function' ? target[key].bind(target) : target[key];
  }
});
class Exit { constructor(code) { this.code = code; } }
const fakeProcess = {
  env: input.environment,
  execPath: process.execPath,
  getuid: () => process.getuid() + (input.wrong_uid ? 1 : 0),
  geteuid: () => process.geteuid() + (input.wrong_uid ? 1 : 0),
  exit(code) { throw new Exit(code); },
  on() {}
};
const spawn = (executable, args, options) => {
  starts++;
  const env = options.env;
  const expected = new Set([...Object.keys(input.profile), 'DATABASE_URL', 'AUTH_JWT_SECRET', 'ANON_KEY', 'SERVICE_KEY', 'PGPASSFILE', 'PATH', 'HOME']);
  const row = fs.readFileSync(path.join(input.root, 'database.pgpass'), 'utf8');
  const password = row.trimEnd().split(':').at(-1);
  boundary = executable === process.execPath && JSON.stringify(args) === JSON.stringify(['dist/start/server.js']) &&
    options.cwd === '/app' && options.stdio === 'ignore' &&
    env.DATABASE_URL === 'postgresql://supabase_storage_admin@db:5432/postgres' &&
    env.PGPASSFILE === '/storage-secrets/database.pgpass' &&
    !Object.hasOwn(env, 'PGPASSWORD') && Object.keys(env).length === expected.size &&
    Object.keys(env).every(key => expected.has(key)) &&
    Object.entries(input.profile).every(([key, value]) => env[key] === value) &&
    Object.values(env).every(value => typeof value === 'string' && !value.includes(password));
  return { on() { return this; }, kill() {} };
};
try {
  vm.runInNewContext(source, {
    process: fakeProcess, Buffer, TextDecoder,
    require(name) {
      if (name === 'node:fs') return mappedFs;
      if (name === 'node:child_process') return { spawn };
      throw new Error('unexpected_module');
    }
  }, { timeout: 1000 });
} catch (error) {
  status = error instanceof Exit && Number.isInteger(error.code) ? error.code : 99;
}
process.stdout.write(JSON.stringify({ status, starts, boundary }));
