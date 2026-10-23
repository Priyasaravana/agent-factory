import { createHash, randomBytes, randomUUID, scrypt, timingSafeEqual } from "node:crypto";

import { AuthError, MIN_PASSWORD_LENGTH, ROLES, type Auth, type Role, type User } from "../port";

// Local accounts (AUTH=local): email and password, scrypt hashes, sessions in Postgres. The
// session token is random; only its SHA-256 is stored, so a database dump signs nobody in.

type Queryable = {
  query(text: string, values?: unknown[]): Promise<{ rows: Record<string, unknown>[] }>;
};

export const AUTH_SCHEMA = `
create table if not exists app_users (
  id text primary key,
  email text not null unique,
  password_hash text not null,
  role text not null,
  created_at bigint not null
);
create table if not exists app_sessions (
  token_hash text primary key,
  user_id text not null,
  expires_at bigint not null
);
`;

export const SESSION_MS = 7 * 24 * 60 * 60 * 1000;
const SCRYPT = { N: 16384, r: 8, p: 1, keylen: 64 };

function derive(password: string, salt: Buffer): Promise<Buffer> {
  return new Promise((resolve, reject) =>
    scrypt(password, salt, SCRYPT.keylen, { N: SCRYPT.N, r: SCRYPT.r, p: SCRYPT.p }, (err, key) =>
      err ? reject(err) : resolve(key),
    ),
  );
}

export async function hashPassword(password: string): Promise<string> {
  const salt = randomBytes(16);
  const key = await derive(password, salt);
  return `scrypt$${salt.toString("base64url")}$${key.toString("base64url")}`;
}

export async function verifyPassword(password: string, stored: string): Promise<boolean> {
  const [scheme, salt, key] = stored.split("$");
  if (scheme !== "scrypt" || !salt || !key) return false;
  const expected = Buffer.from(key, "base64url");
  const actual = await derive(password, Buffer.from(salt, "base64url"));
  return actual.length === expected.length && timingSafeEqual(actual, expected);
}

const tokenHash = (token: string): string => createHash("sha256").update(token).digest("hex");
const normalise = (email: string): string => email.trim().toLowerCase();

export interface LocalOptions {
  db: () => Promise<Queryable>;
  /** The first account (from the chart's Secret): created when the app has no users yet. */
  admin?: { email: string; password: string };
  now?: () => number;
}

export function localAuth({ db, admin, now = Date.now }: LocalOptions): Auth {
  // a hash to compare against when the email is unknown, so both answers take as long
  const decoy = hashPassword(randomBytes(16).toString("hex"));
  let prepared: Promise<Queryable> | undefined;

  async function prepare(): Promise<Queryable> {
    const conn = await db();
    // once per database: pg-mem (tests, `make preview`) can't run `if not exists` on a table it has
    const { rows: found } = await conn.query(
      "select count(*) as n from information_schema.tables where table_name = 'app_users'",
    );
    if (Number(found[0]?.n ?? 0) === 0) await conn.query(AUTH_SCHEMA);
    if (admin) {
      const { rows } = await conn.query("select count(*) as n from app_users");
      if (Number(rows[0]?.n ?? 0) === 0)
        await insertUser(conn, admin.email, admin.password, "admin");
    }
    return conn;
  }

  function ready(): Promise<Queryable> {
    prepared ??= prepare().catch((err: unknown) => {
      prepared = undefined; // the database may come up later: try again on the next request
      throw err;
    });
    return prepared;
  }

  async function insertUser(
    conn: Queryable,
    email: string,
    password: string,
    role: Role,
  ): Promise<User> {
    const address = normalise(email);
    if (!/^[^\s@]+@[^\s@]+$/.test(address)) throw new AuthError("enter a valid email address");
    if (password.length < MIN_PASSWORD_LENGTH) {
      throw new AuthError(`a password needs at least ${MIN_PASSWORD_LENGTH} characters`);
    }
    if (!ROLES.includes(role)) throw new AuthError(`unknown role ${role}`);
    const { rows } = await conn.query("select id from app_users where email = $1", [address]);
    if (rows.length) throw new AuthError("an account with that email already exists");
    const user: User = { id: randomUUID(), email: address, role };
    await conn.query(
      "insert into app_users (id, email, password_hash, role, created_at) values ($1, $2, $3, $4, $5)",
      [user.id, user.email, await hashPassword(password), role, now()],
    );
    return user;
  }

  return {
    enabled: true,
    async signIn(email, password) {
      const conn = await ready();
      const { rows } = await conn.query(
        "select id, email, role, password_hash from app_users where email = $1",
        [normalise(email)],
      );
      const row = rows[0];
      const ok = await verifyPassword(password, row ? String(row.password_hash) : await decoy);
      if (!row || !ok) return null;
      const token = randomBytes(32).toString("base64url");
      const expiresAt = now() + SESSION_MS;
      await conn.query(
        "insert into app_sessions (token_hash, user_id, expires_at) values ($1, $2, $3)",
        [tokenHash(token), row.id, expiresAt],
      );
      return {
        token,
        expiresAt,
        user: { id: String(row.id), email: String(row.email), role: row.role as Role },
      };
    },
    async session(token) {
      if (!token) return null;
      const conn = await ready();
      const { rows } = await conn.query(
        "select u.id, u.email, u.role from app_sessions s join app_users u on u.id = s.user_id " +
          "where s.token_hash = $1 and s.expires_at > $2",
        [tokenHash(token), now()],
      );
      const row = rows[0];
      return row ? { id: String(row.id), email: String(row.email), role: row.role as Role } : null;
    },
    async signOut(token) {
      if (!token) return;
      const conn = await ready();
      await conn.query("delete from app_sessions where token_hash = $1", [tokenHash(token)]);
    },
    async createUser(email, password, role) {
      return insertUser(await ready(), email, password, role);
    },
  };
}
