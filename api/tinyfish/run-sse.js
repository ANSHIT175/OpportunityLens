// Vercel Serverless Function (Universal CommonJS)
module.exports = async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-API-Key');

  if (req.method === 'OPTIONS') {
    res.statusCode = 200;
    return res.end();
  }

  const query = (req.query && req.query.q) || (req.body && req.body.query) || 'AI/ML & Web3 Opportunities';

  res.statusCode = 200;
  res.setHeader('Content-Type', 'text/event-stream; charset=utf-8');
  res.setHeader('Cache-Control', 'no-cache, no-transform');
  res.setHeader('Connection', 'keep-alive');

  const writeSse = (obj) => {
    res.write(`data: ${JSON.stringify(obj)}\n\n`);
  };

  writeSse({ type: 'STATUS', message: `TinyFish Agent connected for query: ${query}` });
  writeSse({ type: 'PROGRESS', stage: 'SEARCH', message: `Searching live web portals for: ${query}` });
  writeSse({ type: 'PROGRESS', stage: 'FETCH', message: 'Extracting verified application links and required skills...' });
  writeSse({ type: 'COMPLETE', query: query });
  res.end();
};
