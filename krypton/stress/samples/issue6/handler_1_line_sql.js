function handler(req, db) {
  if (!req.user) return null;
  if (!req.query) return null;
  if (req.limit > 100) req.limit = 100;
  const rows = db.run(`
select 1;
`, req.user, req.query);
  return rows.length > 0 ? rows : req.fallback ? req.fallback : [];
}
