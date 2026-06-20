#!/usr/bin/env node
/**
 * CLI entry point for the WhatsApp outreach worker.
 *
 * Commands:
 *   start           Connect to WhatsApp and start sending from the queue
 *   dry-run         Show what would be sent without actually sending
 *   import-leads    Import UK phone leads from leads_master_enriched.csv → SQLite
 *   build-queue     Fill the queue from eligible leads (also runs automatically on start)
 *   stats           Print queue + send stats
 *   templates       List all message templates
 */

import { getDb } from './db.js';
import { log } from './logger.js';
import { importLeads } from './importer.js';
import { buildQueue, queueStats, pendingCount } from './queue.js';
import { startWorker, stop } from './sender.js';
import { getSocket } from './auth.js';
import { getStats } from './ratelimit.js';
import { recentSends } from './logger.js';

const cmd = process.argv[2] ?? 'help';

async function main() {
  switch (cmd) {

    // ── START ──────────────────────────────────────────────────────────
    case 'start': {
      // Auto-import latest CSV and rebuild queue before starting
      log.info('Importing leads from CSV…');
      await importLeads();

      log.info('Building queue…');
      const added = buildQueue();
      log.info({ added, total: pendingCount() }, 'Queue ready');

      // Connect WhatsApp (shows QR on first run)
      log.info('Connecting to WhatsApp…');
      await getSocket();

      // Handle SIGINT / SIGTERM gracefully
      process.on('SIGINT',  () => { stop(); });
      process.on('SIGTERM', () => { stop(); });

      await startWorker(false);
      break;
    }

    // ── DRY RUN ────────────────────────────────────────────────────────
    case 'dry-run': {
      await importLeads();
      const added = buildQueue();
      log.info({ added, total: pendingCount() }, 'Queue built (dry-run)');
      await startWorker(true);
      break;
    }

    // ── IMPORT ─────────────────────────────────────────────────────────
    case 'import-leads': {
      const result = await importLeads();
      printTable([result]);
      break;
    }

    // ── BUILD QUEUE ────────────────────────────────────────────────────
    case 'build-queue': {
      await importLeads();
      const added = buildQueue();
      log.info({ added, pending: pendingCount() }, 'Queue updated');
      break;
    }

    // ── STATS ──────────────────────────────────────────────────────────
    case 'stats': {
      const db = getDb();

      const qs = queueStats();
      console.log('\n── Queue ────────────────────────');
      printTable(qs.map(r => ({ status: r.status, count: r.n })));

      const rs = getStats();
      console.log('\n── Rate Limit ───────────────────');
      printTable([{
        messages_today: rs.messages_today,
        date: rs.date,
        paused_until: rs.paused_until ?? '—',
        last_sent: rs.last_sent_at ?? '—',
      }]);

      const total = db.prepare(
        "SELECT COUNT(*) as n FROM sends WHERE channel='whatsapp'"
      ).get().n;
      const today = db.prepare(`
        SELECT COUNT(*) as n FROM sends
        WHERE channel='whatsapp' AND sent_at >= date('now')
      `).get().n;
      const failed = db.prepare(`
        SELECT COUNT(*) as n FROM sends
        WHERE channel='whatsapp' AND status='failed'
      `).get().n;
      const dnc = db.prepare(
        'SELECT COUNT(*) as n FROM leads WHERE do_not_contact=1'
      ).get().n;

      console.log('\n── Sends ────────────────────────');
      printTable([{ total_sent: total, sent_today: today, failed, opted_out: dnc }]);

      console.log('\n── Recent Sends ─────────────────');
      printTable(recentSends(10).map(s => ({
        name: s.name, city: s.city, phone: s.phone,
        sent_at: s.sent_at, status: s.status,
      })));

      break;
    }

    // ── TEMPLATES ──────────────────────────────────────────────────────
    case 'templates': {
      const db = getDb();
      const tpls = db.prepare('SELECT * FROM templates').all();
      for (const t of tpls) {
        console.log(`\n── Template ${t.variant} (${t.active ? 'active' : 'inactive'}) ──`);
        console.log(t.body);
      }
      break;
    }

    default:
      console.log(`
Usage: node src/index.js <command>

Commands:
  start          Connect WhatsApp, import leads, build queue, start sending
  dry-run        Show what would be sent (no actual messages)
  import-leads   Import from leads_master_enriched.csv into SQLite
  build-queue    Rebuild the send queue from eligible leads
  stats          Print queue, rate-limit and send stats
  templates      Show all message templates
`);
  }
}

function printTable(rows) {
  if (!rows?.length) { console.log('(empty)'); return; }
  const keys = Object.keys(rows[0]);
  const widths = keys.map(k => Math.max(k.length, ...rows.map(r => String(r[k] ?? '').length)));
  const fmt = row => keys.map((k,i) => String(row[k] ?? '').padEnd(widths[i])).join('  ');
  console.log(fmt(Object.fromEntries(keys.map(k => [k, k]))));
  console.log(widths.map(w => '─'.repeat(w)).join('  '));
  rows.forEach(r => console.log(fmt(r)));
}

main().catch(err => {
  log.error(err);
  process.exit(1);
});
