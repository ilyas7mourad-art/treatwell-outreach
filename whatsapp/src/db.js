import Database from 'better-sqlite3';
import { mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dir = dirname(fileURLToPath(import.meta.url));
export const DB_PATH = join(__dir, '..', '..', 'output', 'leads.db');

mkdirSync(dirname(DB_PATH), { recursive: true });

let _db;
export function getDb() {
  if (_db) return _db;
  _db = new Database(DB_PATH);
  _db.pragma('journal_mode = WAL');
  _db.pragma('foreign_keys = ON');
  migrate(_db);
  return _db;
}

function migrate(db) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS leads (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      booking_url     TEXT UNIQUE,
      name            TEXT,
      city            TEXT,
      country         TEXT DEFAULT 'UK',
      email           TEXT,
      phone           TEXT,
      address         TEXT,
      rating          TEXT,
      review_count    TEXT,
      site            TEXT,
      -- email sequence state (mirrors CSV)
      email_sent      INTEGER DEFAULT 0,
      email_sent_at   TEXT,
      sms_sent        INTEGER DEFAULT 0,
      sms_sent_at     TEXT,
      replied         INTEGER DEFAULT 0,
      replied_at      TEXT,
      -- shared opt-out
      do_not_contact  INTEGER DEFAULT 0,
      dnc_reason      TEXT,
      dnc_at          TEXT,
      imported_at     TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS templates (
      id      INTEGER PRIMARY KEY AUTOINCREMENT,
      variant TEXT NOT NULL UNIQUE,
      body    TEXT NOT NULL,
      active  INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS sends (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      lead_id     INTEGER NOT NULL REFERENCES leads(id),
      channel     TEXT NOT NULL CHECK(channel IN ('whatsapp','email','sms')),
      template_id INTEGER REFERENCES templates(id),
      phone       TEXT,
      sent_at     TEXT DEFAULT (datetime('now')),
      status      TEXT DEFAULT 'sent' CHECK(status IN ('sent','failed','delivered')),
      error       TEXT
    );

    CREATE TABLE IF NOT EXISTS wa_queue (
      id           INTEGER PRIMARY KEY AUTOINCREMENT,
      lead_id      INTEGER NOT NULL REFERENCES leads(id),
      status       TEXT DEFAULT 'pending'
                   CHECK(status IN ('pending','processing','done','failed','skipped')),
      scheduled_at TEXT DEFAULT (datetime('now')),
      attempts     INTEGER DEFAULT 0,
      last_error   TEXT,
      created_at   TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS rate_state (
      id             INTEGER PRIMARY KEY CHECK(id = 1),
      messages_today INTEGER DEFAULT 0,
      last_sent_at   TEXT,
      paused_until   TEXT,
      date           TEXT DEFAULT (date('now'))
    );

    CREATE INDEX IF NOT EXISTS idx_leads_phone   ON leads(phone);
    CREATE INDEX IF NOT EXISTS idx_leads_url     ON leads(booking_url);
    CREATE INDEX IF NOT EXISTS idx_leads_country ON leads(country, do_not_contact);
    CREATE INDEX IF NOT EXISTS idx_sends_lead    ON sends(lead_id, channel);
    CREATE INDEX IF NOT EXISTS idx_queue_status  ON wa_queue(status, scheduled_at);
  `);

  // Seed templates on first run
  const n = db.prepare('SELECT COUNT(*) as n FROM templates').get().n;
  if (n === 0) seedTemplates(db);
}

function seedTemplates(db) {
  const insert = db.prepare(
    'INSERT OR IGNORE INTO templates (variant, body) VALUES (?, ?)'
  );
  const templates = [
    ['A', `Hey {first_name} 👋

Spotted {business_name} on Treatwell — looks great.

Quick one: Treatwell takes a commission on every booking they send you. I build barbers their own booking site — your domain, zero commission, one payment, you own it forever.

Worth a quick chat? 🙏

Ilyas
bookbarber.design`],

    ['B', `Hi {first_name},

Came across {business_name} on Treatwell. Your reviews are solid.

Wanted to reach out — every booking Treatwell sends you costs you a cut. I help barbers launch their own commission-free site. Yours to keep forever, no monthly fees.

Fancy a chat?

Ilyas — bookbarber.design`],

    ['C', `Hey {first_name} — noticed {business_name} on Treatwell.

Every booking they send you, they take a percentage. I build custom booking sites for barbers — your brand, your clients, zero commission.

One payment, yours forever. Happy to show you what it looks like 👇
bookbarber.design

— Ilyas`],

    ['D', `Hi {first_name},

Found {business_name} on Treatwell — nice setup.

Honest question: how much are you giving Treatwell each month? Most barbers are surprised when they work it out.

I build booking sites that cut them out completely. No commission ever.

Worth a look? bookbarber.design

Ilyas`],

    ['E', `Hey {first_name} 👋

{business_name} looks great on Treatwell — but you're paying for every client they send you.

I do one thing: build barbers their own booking site. You own it, no monthly fees, no commission. Could save you hundreds a month.

Fancy a quick chat?

Ilyas
bookbarber.design`],
  ];
  for (const [variant, body] of templates) insert.run(variant, body);
}
