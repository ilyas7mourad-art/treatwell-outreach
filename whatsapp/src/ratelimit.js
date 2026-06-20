import { getDb } from './db.js';

export const MAX_PER_DAY  = 80;
export const MIN_DELAY_MS = 45_000;   // 45 s
export const MAX_DELAY_MS = 90_000;   // 90 s
export const PAUSE_HOURS  = 24;

export function randomDelayMs() {
  return MIN_DELAY_MS + Math.random() * (MAX_DELAY_MS - MIN_DELAY_MS);
}

function getState(db) {
  const today = new Date().toISOString().slice(0, 10);
  let s = db.prepare('SELECT * FROM rate_state WHERE id = 1').get();

  if (!s) {
    db.prepare(
      'INSERT INTO rate_state (id, messages_today, date) VALUES (1, 0, ?)'
    ).run(today);
    return { id: 1, messages_today: 0, date: today, paused_until: null, last_sent_at: null };
  }

  // Reset daily counter on new day
  if (s.date !== today) {
    db.prepare(
      'UPDATE rate_state SET messages_today = 0, date = ? WHERE id = 1'
    ).run(today);
    s = { ...s, messages_today: 0, date: today };
  }

  return s;
}

export function checkLimit() {
  const db = getDb();
  const s = getState(db);

  if (s.paused_until) {
    const resumeAt = new Date(s.paused_until);
    if (resumeAt > new Date()) {
      return {
        allowed: false,
        reason: 'paused',
        resumeAt: s.paused_until,
      };
    }
    // Pause expired — clear it
    db.prepare('UPDATE rate_state SET paused_until = NULL WHERE id = 1').run();
  }

  if (s.messages_today >= MAX_PER_DAY) {
    return { allowed: false, reason: 'daily_cap', count: s.messages_today };
  }

  return { allowed: true, count: s.messages_today };
}

export function recordSent() {
  const db = getDb();
  const today = new Date().toISOString().slice(0, 10);
  db.prepare(`
    INSERT INTO rate_state (id, messages_today, last_sent_at, date)
    VALUES (1, 1, datetime('now'), ?)
    ON CONFLICT(id) DO UPDATE SET
      messages_today = CASE WHEN date = excluded.date
                            THEN messages_today + 1 ELSE 1 END,
      last_sent_at   = excluded.last_sent_at,
      date           = excluded.date
  `).run(today);
}

export function triggerPause(reason) {
  const db = getDb();
  const resumeAt = new Date(Date.now() + PAUSE_HOURS * 3_600_000).toISOString();
  db.prepare(
    'UPDATE rate_state SET paused_until = ? WHERE id = 1'
  ).run(resumeAt);
  return resumeAt;
}

export function getStats() {
  const db = getDb();
  return getState(db);
}
