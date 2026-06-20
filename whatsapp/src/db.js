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
      id             INTEGER PRIMARY KEY AUTOINCREMENT,
      lead_id        INTEGER NOT NULL REFERENCES leads(id),
      channel        TEXT NOT NULL CHECK(channel IN ('whatsapp','email','sms')),
      template_id    INTEGER REFERENCES templates(id),
      phone          TEXT,
      sent_at        TEXT DEFAULT (datetime('now')),
      status         TEXT DEFAULT 'sent' CHECK(status IN ('sent','failed','delivered')),
      error          TEXT,
      follow_up_num  INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS wa_queue (
      id             INTEGER PRIMARY KEY AUTOINCREMENT,
      lead_id        INTEGER NOT NULL REFERENCES leads(id),
      follow_up_num  INTEGER DEFAULT 0,
      status         TEXT DEFAULT 'pending'
                     CHECK(status IN ('pending','processing','done','failed','skipped')),
      scheduled_at   TEXT DEFAULT (datetime('now')),
      attempts       INTEGER DEFAULT 0,
      last_error     TEXT,
      created_at     TEXT DEFAULT (datetime('now'))
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

  // Migrate existing DBs — add columns added after initial deploy
  for (const sql of [
    'ALTER TABLE sends    ADD COLUMN follow_up_num INTEGER DEFAULT 0',
    'ALTER TABLE wa_queue ADD COLUMN follow_up_num INTEGER DEFAULT 0',
  ]) {
    try { db.exec(sql); } catch {}
  }

  // Seed templates on first run
  const n = db.prepare('SELECT COUNT(*) as n FROM templates').get().n;
  if (n === 0) seedTemplates(db);

  // Seed follow-up templates if missing
  seedFollowUpTemplates(db);
}

function seedTemplates(db) {
  const insert = db.prepare(
    'INSERT OR IGNORE INTO templates (variant, body) VALUES (?, ?)'
  );
  const templates = [
    ['A', `hey saw {business_name} on treatwell just wanted to reach out real quick treatwell takes a cut from every booking you get through them i build barbers their own booking site your domain zero commission one payment you own it forever worth a quick chat

ilyas`],

    ['B', `hey just came across {business_name} on treatwell noticed you got some good reviews on there quick thing though treatwell are taking a percentage of every booking they send you i help barbers cut them out completely own booking site your brand no commission ever just one payment

up for a chat

ilyas`],

    ['C', `hey saw {business_name} on treatwell wanted to drop you a message real quick how much are you giving treatwell every month most barbers i speak to are surprised when they work it out i build booking sites for barbers your own domain zero fees you own it outright

ilyas`],

    ['D', `hey found {business_name} on treatwell just a quick one treatwell charge you every time someone books through them i build barbers a proper booking site on your own domain no monthly fees no commission you just pay once and its yours

worth a chat

ilyas`],

    ['E', `hey just spotted {business_name} on treatwell wanted to reach out i do one thing i build barbers their own booking site so you stop paying treatwell their cut your domain your clients you own it no ongoing fees

ilyas`],
  ];
  for (const [variant, body] of templates) insert.run(variant, body);
}

function seedFollowUpTemplates(db) {
  const insert = db.prepare('INSERT OR IGNORE INTO templates (variant, body) VALUES (?, ?)');
  const followUps = [
    ['FU1', `hey just checking if you saw my last message about {business_name} i know its a lot of messages but genuinely think this could save you a decent amount every month worth 5 mins of your time

ilyas`],
    ['FU2', `hey me again just wanted to leave this here in case it got buried treatwell are taking money from every single booking you get through them i can build you a site so that stops happening one payment thats it

ilyas`],
    ['FU3', `last one i promise just wanted to reach out one more time if youre ever thinking about cutting out treatwell im your guy no pressure

ilyas`],
  ];
  for (const [variant, body] of followUps) insert.run(variant, body);
}
