import { getDb } from './db.js';

export function pickTemplateFor(leadId, followUpNum = 0) {
  const db = getDb();

  if (followUpNum > 0) {
    const t = db.prepare(
      "SELECT * FROM templates WHERE variant = ? AND active = 1"
    ).get(`FU${followUpNum}`);
    if (!t) throw new Error(`No template for FU${followUpNum}`);
    return t;
  }

  // Initial: random A-E, avoid repeating what was already sent to this lead
  const usedIds = db.prepare(`
    SELECT DISTINCT template_id FROM sends
    WHERE lead_id = ? AND channel = 'whatsapp' AND follow_up_num = 0
  `).all(leadId).map(r => r.template_id);

  const templates = db.prepare(
    "SELECT * FROM templates WHERE active = 1 AND variant IN ('A','B','C','D','E') ORDER BY id"
  ).all();

  if (!templates.length) throw new Error('No initial templates in database');
  const unused = templates.filter(t => !usedIds.includes(t.id));
  const pool   = unused.length ? unused : templates;
  return pool[Math.floor(Math.random() * pool.length)];
}

export function render(template, lead) {
  const businessName = lead.name || 'your barbershop';
  const firstName    = businessName.split(/[\s']/)[0];
  return template.body
    .replace(/\{business_name\}/g, businessName)
    .replace(/\{first_name\}/g, firstName);
}
