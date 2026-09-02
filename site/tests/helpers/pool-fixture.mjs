import { DatabaseSync } from 'node:sqlite';
import { readFileSync, readdirSync } from 'node:fs';

export function createPool() {
  const sqlite = new DatabaseSync(':memory:');
  let clock = 1788307200;
  sqlite.function('unixepoch', () => BigInt(clock));
  const migrationRoot = new URL('../../drizzle/', import.meta.url);
  for (const name of readdirSync(migrationRoot).filter(n => n.endsWith('.sql')).sort()) sqlite.exec(readFileSync(new URL(name, migrationRoot), 'utf8'));
  return {
    prepare(sql) {
      let args = [];
      const stmt = { bind(...values) { args = values; return stmt; }, first: async () => null };
      // SQLite numbered placeholders are bound by name, unlike D1's positional array.
      stmt.first = async () => {
        const statement = sqlite.prepare(sql);
        const bindings = Object.fromEntries(args.map((v, i) => [String(i + 1), v]));
        return (args.length ? statement.get(bindings) : statement.get()) ?? null;
      };
      return stmt;
    },
    advance(seconds) { clock += seconds; },
    row() { return sqlite.prepare('SELECT * FROM shared_pool').get(); },
    count() { return sqlite.prepare('SELECT count(*) n FROM shared_pool').get().n; },
    close() { sqlite.close(); },
  };
}
export function fixtureEnvironment(DB) {
  return { DB, WUWATERM_SHARED_POOL_ENABLED: 'true', WUWATERM_TRANSLATION_ENABLED: 'true', WUWATERM_API_ALLOWED_HOST: 'api.wuwaterm-test.net', WUWATERM_API_BASE_URL: 'https://api.wuwaterm-test.net/wuwaterm-api/', WUWATERM_SITE_DEVICE_TOKEN: 'SYNTHETIC_PRODUCT_TOKEN_61E8' };
}
