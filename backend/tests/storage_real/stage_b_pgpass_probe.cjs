'use strict';
// Optional local repeat against the independent integrity-checked research cache.
// Actual pg8.23.0 Client._getPassword, NOT a copied matching algorithm; never connect.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
let attempts = 0;
const forbidden = () => { attempts++; throw new Error('network_or_child_forbidden'); };
const net = require('node:net');
net.Socket.prototype.connect = forbidden;
net.connect = net.createConnection = forbidden;
require('node:tls').connect = forbidden;
const dns = require('node:dns');
dns.lookup = dns.resolve = forbidden;
const cp = require('node:child_process');
for (const name of ['spawn', 'spawnSync', 'exec', 'execSync', 'execFile', 'execFileSync', 'fork']) cp[name] = forbidden;
async function main() {
  const modules = path.join(input.cache, 'node_modules');
  assert.equal(require(path.join(modules, 'pg/package.json')).version, '8.23.0');
  assert.equal(require(path.join(modules, 'pgpass/package.json')).version, '1.0.5');
  require(path.join(modules, 'pgpass')).warnTo(null);
  const { Client } = require(path.join(modules, 'pg'));
  const passfile = path.join(input.root, 'database.pgpass');
  const original = fs.readFileSync(passfile, 'utf8');
  const prefix = 'db:5432:postgres:supabase_storage_admin:';
  assert.equal(original.length, prefix.length + 65);
  assert.ok(original.startsWith(prefix) && original.endsWith('\n'));
  const secret = original.slice(prefix.length, -1);
  const endpoint = 'postgresql://supabase_storage_admin@db:5432/postgres';
  let checks = 0;
  async function lookup(url) {
    const client = new Client({ connectionString: url, connectionTimeoutMillis: 3000, ssl: false });
    let timer;
    try {
      await Promise.race([
        new Promise((resolve, reject) => client._getPassword(error => error ? reject(error) : resolve())),
        new Promise((resolve, reject) => { timer = setTimeout(() => reject(new Error('bounded_lookup')), 1500); })
      ]);
      return client.password;
    } finally { clearTimeout(timer); }
  }
  process.env.PGPASSFILE = passfile;
  delete process.env.PGPASSWORD;
  try {
    assert.equal(await lookup(endpoint), secret); checks++;
    for (const [field, value] of [['hostname', 'other'], ['port', '5433'], ['pathname', '/other'], ['username', 'other']]) {
      const changed = new URL(endpoint); changed[field] = value;
      assert.equal(await lookup(changed.toString()), null); checks++;
    }
    process.env.PGPASSFILE = passfile + '.missing';
    assert.equal(await lookup(endpoint), null); checks++;
    process.env.PGPASSFILE = passfile;
    fs.chmodSync(passfile, 0o644);
    assert.equal(await lookup(endpoint), null); checks++;
    fs.chmodSync(passfile, 0o600);
    process.env.PGPASSWORD = '';
    assert.equal(await lookup(endpoint), null); checks++;
    process.env.PGPASSWORD = require('node:crypto').randomBytes(32).toString('hex');
    assert.equal(await lookup(endpoint), process.env.PGPASSWORD); checks++;
    delete process.env.PGPASSWORD;
    const link = passfile + '.link';
    fs.symlinkSync(passfile, link);
    try {
      process.env.PGPASSFILE = link;
      assert.equal(await lookup(endpoint), secret); checks++;
    } finally { fs.unlinkSync(link); }
    assert.equal(attempts, 0);
    assert.equal(fs.readFileSync(passfile, 'utf8'), original);
    process.stdout.write(JSON.stringify({ ok: true, checks, attempts, node: process.versions.node }));
  } finally {
    fs.chmodSync(passfile, 0o600);
    delete process.env.PGPASSWORD;
    delete process.env.PGPASSFILE;
  }
}
main().catch(() => { process.stdout.write(JSON.stringify({ ok: false, attempts })); process.exitCode = 1; });
