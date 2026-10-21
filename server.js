/**
 * OpportunityLens Node.js Backend Server (Alternative to server.py)
 * =================================================================
 * Runs with built-in Node.js 18+ APIs (http, fs, path, fetch) — zero npm install required.
 * Reads process.env.TINYFISH_API_KEY strictly on the server.
 */

const http = require('http');
const fs = require('fs');
const path = require('path');
const { URL } = require('url');

// Load local .env file if present
const envPath = path.join(__dirname, '.env');
if (fs.existsSync(envPath)) {
  const lines = fs.readFileSync(envPath, 'utf8').split(/\r?\n/);
  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#') || !line.includes('=')) continue;
    const idx = line.indexOf('=');
    const key = line.slice(0, idx).trim();
    const val = line.slice(idx + 1).trim().replace(/^['"]|['"]$/g, '');
    if (key && !process.env[key]) process.env[key] = val;
  }
}

const PORT = Number(process.env.PORT || 8000);
const TINYFISH_SEARCH_URL = process.env.TINYFISH_SEARCH_URL || 'https://api.search.tinyfish.ai';
const TINYFISH_FETCH_URL = process.env.TINYFISH_FETCH_URL || 'https://api.fetch.tinyfish.ai';
const TINYFISH_AGENT_SSE_URL = process.env.TINYFISH_AGENT_SSE_URL || 'https://agent.tinyfish.ai/v1/automation/run-sse';
const TINYFISH_AGENT_RUN_URL = process.env.TINYFISH_AGENT_RUN_URL || 'https://agent.tinyfish.ai/v1/automation/run';

const OPPORTUNITY_OUTPUT_SCHEMA = {
  type: 'object',
  properties: {
    opportunities: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          title: { type: 'string' },
          organization: { type: 'string' },
          category: { type: 'string' },
          eligibility: { type: 'string' },
          eligible_degrees: { type: 'array', items: { type: 'string' } },
          eligible_years: { type: 'array', items: { type: 'string' } },
          required_skills: { type: 'array', items: { type: 'string' } },
          preferred_skills: { type: 'array', items: { type: 'string' } },
          deadline: { type: 'string' },
          location: { type: 'string' },
          remote_status: { type: 'string' },
          stipend: { type: 'string' },
          summary: { type: 'string' },
          application_url: { type: 'string' }
        },
        required: ['title', 'organization', 'required_skills', 'application_url']
      }
    }
  },
  required: ['opportunities']
};

function getApiKey() {
  return (process.env.TINYFISH_API_KEY || '').trim();
}

function buildAgentGoal(query, profile = {}) {
  const degree = profile.degree || 'B.Tech';
  const gradYear = profile.gradYear || '2027';
  const skills = Array.isArray(profile.skills) && profile.skills.length ? profile.skills.slice(0, 8).join(', ') : 'Python, Machine Learning';
  const locationPref = profile.locationPref || 'Any';
  return (
    `Search and navigate live opportunity listings on this page for the query: "${query}". ` +
    `Target candidate profile: ${degree} student graduating in ${gradYear}, skills: [${skills}], preferred work mode: ${locationPref}. ` +
    `Extract up to 6 real, currently active opportunities (internships, research fellowships, scholarships, or hackathons). ` +
    `For each opportunity, extract: title, organization, category (Internship, Research, Scholarship, or Hackathon), ` +
    `eligibility, eligible_degrees, eligible_years, required_skills, preferred_skills, deadline, location, remote_status, stipend, summary, and application_url. Return valid JSON matching output_schema.`
  );
}

function normalizeOpportunity(raw, idx = 0, fallbackUrl = '', runId = '') {
  const title = String(raw.title || raw.role || 'Verified Opportunity').trim();
  const organization = String(raw.organization || raw.company || 'Verified Organization').trim();
  const appUrl = String(raw.application_url || raw.sourceUrl || raw.url || fallbackUrl || 'https://docs.tinyfish.ai').trim();

  let domain = 'verified-portal.org';
  try {
    const u = new URL(appUrl);
    domain = u.hostname.replace(/^www\./, '');
    const firstSeg = u.pathname.replace(/^\/+|\/+$/g, '').split('/')[0];
    if (firstSeg && firstSeg.length < 20) domain = `${domain}/${firstSeg}`;
  } catch (_) {}

  let rawCat = String(raw.category || 'Internship').trim();
  rawCat = rawCat.charAt(0).toUpperCase() + rawCat.slice(1).toLowerCase();
  if (!['Internship', 'Research', 'Scholarship', 'Hackathon'].includes(rawCat)) {
    const tLow = title.toLowerCase();
    if (tLow.includes('research') || tLow.includes('fellow')) rawCat = 'Research';
    else if (tLow.includes('scholar') || tLow.includes('grant')) rawCat = 'Scholarship';
    else if (tLow.includes('hack') || tLow.includes('challenge')) rawCat = 'Hackathon';
    else rawCat = 'Internship';
  }

  let reqSkills = raw.required_skills || raw.requiredSkills || [];
  if (typeof reqSkills === 'string') reqSkills = reqSkills.split(/[,;/|]+/).map((s) => s.trim()).filter(Boolean);
  if (!Array.isArray(reqSkills) || !reqSkills.length) reqSkills = ['Problem Solving'];

  let prefSkills = raw.preferred_skills || raw.preferredSkills || [];
  if (typeof prefSkills === 'string') prefSkills = prefSkills.split(/[,;/|]+/).map((s) => s.trim()).filter(Boolean);
  if (!Array.isArray(prefSkills)) prefSkills = [];

  const eligDegrees = Array.isArray(raw.eligible_degrees) && raw.eligible_degrees.length ? raw.eligible_degrees : ['B.Tech', 'M.Tech', 'B.Sc', 'PhD'];
  const eligYears = Array.isArray(raw.eligible_years) && raw.eligible_years.length ? raw.eligible_years : ['2026', '2027', '2028'];
  const deadline = String(raw.deadline || 'Open / Rolling').trim();
  const location = String(raw.location || raw.remote_status || 'Remote / Hybrid').trim();
  let remoteStatus = String(raw.remote_status || '').trim();
  if (!['Remote', 'India', 'Global'].includes(remoteStatus)) {
    remoteStatus = location.toLowerCase().includes('remote') ? 'Remote' : 'India';
  }

  const stipend = String(raw.stipend || 'Listed on Official Portal').trim();
  const eligText = String(raw.eligibility || '').trim();
  let summary = String(raw.summary || raw.description || `Live opportunity at ${organization} extracted via TinyFish Web Agent.`).trim();
  if (eligText && !summary.toLowerCase().includes(eligText.toLowerCase())) {
    summary = `${summary} (Eligibility: ${eligText})`;
  }

  const keywords = `${title} ${organization} ${rawCat} ${reqSkills.join(' ')} ${location}`
    .toLowerCase()
    .split(/\W+/)
    .filter((w) => w.length >= 2);

  return {
    id: raw.id || `tf-live-${Date.now()}-${idx}`,
    title,
    organization,
    sourceDomain: domain,
    sourceUrl: appUrl,
    category: rawCat,
    location,
    locationTag: remoteStatus,
    stipend,
    deadline,
    daysLeft: Number(raw.daysLeft || 14),
    eligibilityText: eligText || `Open to ${eligDegrees.join(', ')} (${eligYears.join(', ')})`,
    eligibleDegrees: eligDegrees,
    eligibleYears: eligYears,
    requiredSkills: reqSkills,
    preferredSkills: prefSkills,
    keywords: Array.from(new Set(keywords)),
    tinyfishRunId: runId || `tf_run_${Date.now()}`,
    fetchedAt: new Date().toLocaleString('en-IN') + ' (TTL: 0s Live)',
    summary,
    learningAdvice: `Review core requirements (${reqSkills.slice(0, 3).join(', ')}) and tailor your application on ${domain}.`
  };
}

function extractOpportunities(resultObj, fallbackUrl, runId) {
  if (!resultObj) return [];
  let candidates = [];
  if (Array.isArray(resultObj)) candidates = resultObj;
  else if (typeof resultObj === 'object') {
    if (Array.isArray(resultObj.opportunities)) candidates = resultObj.opportunities;
    else if (Array.isArray(resultObj.results)) candidates = resultObj.results;
    else if (Array.isArray(resultObj.result)) candidates = resultObj.result;
    else if (resultObj.result && typeof resultObj.result === 'object' && Array.isArray(resultObj.result.opportunities)) {
      candidates = resultObj.result.opportunities;
    } else if (resultObj.title || resultObj.role) {
      candidates = [resultObj];
    }
  }
  return candidates.filter((c) => c && typeof c === 'object').map((c, i) => normalizeOpportunity(c, i, fallbackUrl, runId));
}

function sendJson(res, status, data) {
  const body = JSON.stringify(data);
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Accept',
    'Cache-Control': 'no-store'
  });
  res.end(body);
}

function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    let raw = '';
    req.on('data', (chunk) => { raw += chunk; });
    req.on('end', () => {
      if (!raw.trim()) return resolve({});
      try { resolve(JSON.parse(raw)); } catch (e) { reject(e); }
    });
    req.on('error', reject);
  });
}

const server = http.createServer(async (req, res) => {
  const parsedUrl = new URL(req.url, `http://localhost:${PORT}`);
  const route = parsedUrl.pathname;

  if (req.method === 'OPTIONS') {
    res.writeHead(204, {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type, Accept'
    });
    return res.end();
  }

  if (req.method === 'GET' && route === '/api/health') {
    const key = getApiKey();
    return sendJson(res, 200, {
      status: 'ok',
      service: 'OpportunityLens TinyFish Backend (Node.js)',
      tinyfish_api_key_configured: Boolean(key),
      tinyfish_key_preview: key.length > 10 ? `${key.slice(0, 6)}...${key.slice(-4)}` : (key ? 'SET' : null),
      endpoints: {
        search_api: TINYFISH_SEARCH_URL,
        fetch_api: TINYFISH_FETCH_URL,
        agent_sse_api: TINYFISH_AGENT_SSE_URL,
        agent_run_api: TINYFISH_AGENT_RUN_URL
      }
    });
  }

  if (req.method === 'GET' && ['/', '/index.html', '/opportunitylens_app.html'].includes(route)) {
    const htmlPath = path.join(__dirname, 'opportunitylens_app.html');
    if (!fs.existsSync(htmlPath)) {
      return sendJson(res, 404, { error: 'opportunitylens_app.html not found' });
    }
    const content = fs.readFileSync(htmlPath);
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    return res.end(content);
  }

  if (req.method === 'POST' && route === '/api/tinyfish/run-sse') {
    const body = await readJsonBody(req).catch(() => ({}));
    const apiKey = getApiKey();

    res.writeHead(200, {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-cache, no-transform',
      'Connection': 'keep-alive',
      'Access-Control-Allow-Origin': '*'
    });

    const writeSse = (ev) => {
      res.write(`data: ${JSON.stringify(ev)}\n\n`);
    };

    if (!apiKey) {
      writeSse({
        type: 'ERROR',
        code: 'MISSING_API_KEY',
        message: "TINYFISH_API_KEY is not configured on the backend server. Set export TINYFISH_API_KEY='your_key' and restart the server."
      });
      return res.end();
    }

    const query = String(body.query || 'AI/ML Research & Python Internships').trim();
    const profile = body.profile || {};
    let targetUrls = body.url ? [String(body.url).trim()] : [];
    const searchMeta = [];

    if (!targetUrls.length) {
      writeSse({
        type: 'PROGRESS',
        stage: 'SEARCH',
        purpose: `Calling TinyFish Search API (GET ${TINYFISH_SEARCH_URL}) for '${query}'...`
      });
      try {
        const qParams = new URLSearchParams({
          query: `${query} student internship fellowship application eligibility deadline`,
          purpose: `Find active student opportunities for ${query}`,
          recency_minutes: '525600'
        });
        const sResp = await fetch(`${TINYFISH_SEARCH_URL}?${qParams.toString()}`, {
          headers: { 'X-API-Key': apiKey, 'Accept': 'application/json' }
        });
        if (sResp.status === 401 || sResp.status === 403) {
          const errText = await sResp.text();
          writeSse({ type: 'ERROR', code: `HTTP_${sResp.status}`, message: `TinyFish authentication failed: ${errText}` });
          return res.end();
        }
        if (sResp.ok) {
          const sJson = await sResp.json();
          for (const r of (sJson.results || []).slice(0, 5)) {
            if (r.url && r.url.startsWith('http')) {
              targetUrls.push(r.url);
              searchMeta.push(r);
            }
          }
          writeSse({
            type: 'SEARCH_RESULTS',
            stage: 'SEARCH',
            count: targetUrls.length,
            results: searchMeta,
            purpose: `TinyFish Search API discovered ${targetUrls.length} candidate URLs.`
          });
        }
      } catch (e) {
        writeSse({ type: 'PROGRESS', stage: 'SEARCH', purpose: `Search API note (${e.message}), continuing to Web Agent...` });
      }
    }

    if (!targetUrls.length) {
      targetUrls = [`https://www.google.com/search?q=${encodeURIComponent(query + ' internship apply')}`];
    }

    const primaryUrl = targetUrls[0];
    const goal = buildAgentGoal(query, profile);

    writeSse({
      type: 'PROGRESS',
      stage: 'AGENT',
      purpose: `Connecting to TinyFish Web Agent SSE (${TINYFISH_AGENT_SSE_URL}) on ${primaryUrl}...`
    });

    try {
      const agentResp = await fetch(TINYFISH_AGENT_SSE_URL, {
        method: 'POST',
        headers: {
          'X-API-Key': apiKey,
          'Content-Type': 'application/json',
          'Accept': 'text/event-stream'
        },
        body: JSON.stringify({
          url: primaryUrl,
          goal,
          output_schema: OPPORTUNITY_OUTPUT_SCHEMA,
          browser_profile: 'lite'
        })
      });

      if (!agentResp.ok) {
        const errText = await agentResp.text();
        writeSse({ type: 'ERROR', code: `HTTP_${agentResp.status}`, message: `TinyFish Agent HTTP ${agentResp.status}: ${errText}` });
        return res.end();
      }

      const reader = agentResp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let lastRunId = `tf_run_${Date.now()}`;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split(/\r?\n/);
        buffer = lines.pop() || '';

        for (const rawLine of lines) {
          const line = rawLine.trim();
          if (!line.startsWith('data:')) continue;
          const dataStr = line.slice(5).trim();
          if (!dataStr) continue;
          let ev;
          try { ev = JSON.parse(dataStr); } catch (_) { continue; }
          if (ev.run_id) lastRunId = ev.run_id;

          if (ev.type === 'COMPLETE') {
            if (ev.status === 'FAILED' || ev.error) {
              writeSse({
                type: 'ERROR',
                run_id: lastRunId,
                code: (ev.error && ev.error.code) || 'AGENT_FAILED',
                message: (ev.error && ev.error.message) || 'TinyFish Web Agent run failed.'
              });
              return res.end();
            }
            const opportunities = extractOpportunities(ev.result, primaryUrl, lastRunId);
            writeSse({
              type: 'COMPLETE',
              run_id: lastRunId,
              status: 'COMPLETED',
              opportunities,
              raw_result: ev.result,
              target_url: primaryUrl,
              search_results: searchMeta
            });
            return res.end();
          } else {
            writeSse(ev);
          }
        }
      }
      res.end();
    } catch (e) {
      writeSse({ type: 'ERROR', code: 'STREAM_ERROR', message: e.message });
      res.end();
    }
    return;
  }

  return sendJson(res, 404, { error: `Unknown route: ${route}` });
});

server.listen(PORT, () => {
  console.log(`OpportunityLens Node.js Server listening on http://localhost:${PORT}`);
});
