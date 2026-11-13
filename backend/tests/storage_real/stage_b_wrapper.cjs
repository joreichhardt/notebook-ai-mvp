'use strict';
// Exact pinned /app Node entrypoint; no raw startup/driver output.
// Own readonly service mount supplies path stability, NOT protection from a malicious host.
const fs = require('node:fs');
const { spawn } = require('node:child_process');
const directory = '/storage-secrets';
const databaseURL = 'postgresql://supabase_storage_admin@db:5432/postgres';
const profile = Object.freeze({
  DB_INSTALL_ROLES: 'false', DATABASE_MAX_CONNECTIONS: '4',
  DATABASE_CONNECTION_TIMEOUT: '3000', DATABASE_STATEMENT_TIMEOUT: '3000',
  STORAGE_BACKEND: 'file', FILE_STORAGE_BACKEND_PATH: '/var/lib/storage',
  GLOBAL_S3_BUCKET: 'a5-synthetic', REGION: 'a5', TENANT_ID: 'a5',
  FILE_SIZE_LIMIT: '26214400', IMAGE_TRANSFORMATION_ENABLED: 'false',
  PG_QUEUE_ENABLE: 'false', PG_QUEUE_WORKERS_ENABLE: 'false',
  S3_PROTOCOL_ENABLED: 'false', LOG_LEVEL: 'silent', SERVER_PORT: '5000',
  MULTI_TENANT: 'false', EXPOSE_DOCS: 'false'
});
const invalid = () => { throw new Error('invalid_storage_bootstrap'); };
try {
  // Reject inherited presence, even empty: pgpass suppresses lookup for empty PGPASSWORD.
  // These checks cannot undo a malicious preload of this initial Node process.
  for (const key of Object.keys(process.env)) {
    if (Object.hasOwn(profile, key)) {
      if (process.env[key] !== profile[key]) invalid();
    } else if (
      (key.startsWith('NODE_') && key !== 'NODE_VERSION') ||
      /^(PG|DATABASE_|DB_|MULTI_|IS_MULTITENANT$|TENANT_|VECTOR_|QUEUE_|TLS_|SSL_|AUTH_|STORAGE_|S3_|GLOBAL_S3_|IMAGE_|ENABLE_|WEBHOOK_|OTEL_|LOG_|LD_|DYLD_|AWS_)/.test(key) ||
      /(_URL|_URI|_ENDPOINT)$/.test(key)
    ) invalid();
  }
  for (const key of Object.keys(profile)) {
    if (!Object.hasOwn(process.env, key) || process.env[key] !== profile[key]) invalid();
  }
  const uid = process.geteuid();
  const parent = fs.lstatSync(directory);
  if (!parent.isDirectory() || parent.isSymbolicLink() || parent.uid !== uid || (parent.mode & 0o7777) !== 0o700) invalid();
  const validFile = stat => stat.isFile() && !stat.isSymbolicLink() && stat.uid === uid &&
    stat.nlink === 1 && (stat.mode & 0o7777) === 0o600;
  const readPrivate = (name, limit) => {
    const file = directory + '/' + name;
    const before = fs.lstatSync(file);
    if (!validFile(before) || before.size < 1 || before.size > limit || !Number.isInteger(fs.constants.O_NOFOLLOW)) invalid();
    const fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
    try {
      const opened = fs.fstatSync(fd);
      if (!validFile(opened) || opened.dev !== before.dev || opened.ino !== before.ino || opened.size !== before.size) invalid();
      const buffer = Buffer.alloc(limit + 1);
      const count = fs.readSync(fd, buffer, 0, buffer.length, 0);
      const after = fs.fstatSync(fd);
      if (count !== opened.size || count > limit || !validFile(after) || after.dev !== opened.dev || after.ino !== opened.ino || after.size !== opened.size || after.mtimeMs !== opened.mtimeMs) invalid();
      return new TextDecoder('utf-8', { fatal: true }).decode(buffer.subarray(0, count));
    } finally { fs.closeSync(fd); }
  };
  const data = JSON.parse(readPrivate('environment.json', 16384));
  const names = ['DATABASE_URL', 'AUTH_JWT_SECRET', 'ANON_KEY', 'SERVICE_KEY'];
  if (data === null || typeof data !== 'object' || Array.isArray(data) || Object.keys(data).length !== names.length ||
      !names.every(key => Object.hasOwn(data, key) && typeof data[key] === 'string' && data[key].length > 0 && data[key].length < 4096) ||
      data.DATABASE_URL !== databaseURL) invalid();
  const pass = readPrivate('database.pgpass', 256);
  const prefix = 'db:5432:postgres:supabase_storage_admin:';
  if (!pass.startsWith(prefix) || pass.length !== prefix.length + 65 || !pass.endsWith('\n') || !/^[0-9a-f]{64}$/.test(pass.slice(prefix.length, -1))) invalid();
  // No arbitrary parent environment, alternate endpoints, DB password or loader overrides.
  const environment = { ...profile, ...data, PGPASSFILE: directory + '/database.pgpass',
    PATH: '/usr/local/bin:/usr/bin:/bin', HOME: '/tmp' };
  const child = spawn(process.execPath, ['dist/start/server.js'], {
    cwd: '/app', env: environment, stdio: 'ignore'
  });
  child.on('error', () => process.exit(70));
  child.on('exit', code => process.exit(Number.isInteger(code) ? code : 71));
  for (const sig of ['SIGTERM', 'SIGINT']) process.on(sig, () => child.kill(sig));
} catch {
  process.exit(70);
}
