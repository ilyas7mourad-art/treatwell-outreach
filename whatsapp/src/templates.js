import { getDb } from './db.js';

// Pick a random active template, cycling through variants so no lead
// in the same batch gets the same wording.
export function pickTemplate() {
  const db = getDb();
  const templates = db.prepare(
    'SELECT * FROM templates WHERE active = 1 ORDER BY id'
  ).all();
  if (!templates.length) throw new Error('No active templates in DB');
  return templates[Math.floor(Math.random() * templates.length)];
}

// Avoid repeating a template used recently for the same lead.
export function pickTemplateFor(leadId) {
  const db = getDb();
  const usedIds = db.prepare(`
    SELECT DISTINCT template_id FROM sends
    WHERE lead_id = ? AND channel = 'whatsapp'
  `).all(leadId).map(r => r.template_id);

  const templates = db.prepare(
    'SELECT * FROM templates WHERE active = 1 ORDER BY id'
  ).all();

  const unused = templates.filter(t => !usedIds.includes(t.id));
  const pool = unused.length ? unused : templates;
  return pool[Math.floor(Math.random() * pool.length)];
}

export function render(template, lead) {
  const businessName = lead.name || 'your barbershop';
  const firstName = businessName.split(/[\s']/)[0]; // first word
  return template.body
    .replace(/\{business_name\}/g, businessName)
    .replace(/\{first_name\}/g, firstName);
}
