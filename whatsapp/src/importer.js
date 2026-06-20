/**
 * Import UK leads from leads_master_enriched.csv into the SQLite DB.
 * Only imports records with a phone number.
 * Deduplicates by booking_url — safe to re-run.
 */

import { createReadStream } from 'node:fs';
import { createInterface } from 'node:readline';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { getDb } from './db.js';
import { log } from './logger.js';

const __dir = dirname(fileURLToPath(import.meta.url));
const CSV_PATH = join(__dir, '..', '..', 'output', 'leads_master_enriched.csv');

function parseCSVLine(line) {
  const fields = [];
  let cur = '', inQuote = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') { inQuote = !inQuote; continue; }
    if (ch === ',' && !inQuote) { fields.push(cur); cur = ''; continue; }
    cur += ch;
  }
  fields.push(cur);
  return fields;
}

export async function importLeads() {
  const db = getDb();

  const insert = db.prepare(`
    INSERT INTO leads (
      booking_url, name, city, country, email, phone, address,
      rating, review_count, site, email_sent, email_sent_at,
      sms_sent, sms_sent_at, replied, replied_at
    ) VALUES (
      @booking_url, @name, @city, @country, @email, @phone, @address,
      @rating, @review_count, @site, @email_sent, @email_sent_at,
      @sms_sent, @sms_sent_at, @replied, @replied_at
    )
    ON CONFLICT(booking_url) DO UPDATE SET
      email        = excluded.email,
      phone        = excluded.phone,
      address      = excluded.address,
      email_sent   = excluded.email_sent,
      email_sent_at= excluded.email_sent_at,
      sms_sent     = excluded.sms_sent,
      sms_sent_at  = excluded.sms_sent_at,
      replied      = excluded.replied,
      replied_at   = excluded.replied_at
  `);

  const importBatch = db.transaction(rows => {
    for (const row of rows) insert.run(row);
  });

  const rl = createInterface({
    input: createReadStream(CSV_PATH),
    crlfDelay: Infinity,
  });

  let headers = null;
  let batch = [];
  let imported = 0, skipped = 0;

  for await (const line of rl) {
    if (!headers) {
      headers = parseCSVLine(line);
      continue;
    }

    const vals = parseCSVLine(line);
    const row = Object.fromEntries(headers.map((h, i) => [h, vals[i] ?? '']));

    // Only UK leads with a phone number
    if (row.country?.toUpperCase() !== 'UK') { skipped++; continue; }
    if (!row.phone?.trim()) { skipped++; continue; }

    batch.push({
      booking_url:   row.booking_url  || null,
      name:          row.name         || null,
      city:          row.city         || null,
      country:       'UK',
      email:         row.email        || null,
      phone:         row.phone        || null,
      address:       row.address      || null,
      rating:        row.rating       || null,
      review_count:  row.review_count || null,
      site:          row.site         || null,
      email_sent:    row.sent === 'true' ? 1 : 0,
      email_sent_at: row.sent_at      || null,
      sms_sent:      row.sms_sent === 'true' ? 1 : 0,
      sms_sent_at:   row.sms_sent_at  || null,
      replied:       row.replied === 'true' ? 1 : 0,
      replied_at:    row.replied_at   || null,
    });

    if (batch.length >= 500) {
      importBatch(batch);
      imported += batch.length;
      batch = [];
    }
  }

  if (batch.length) {
    importBatch(batch);
    imported += batch.length;
  }

  log.info({ imported, skipped }, 'Import complete');
  return { imported, skipped };
}
