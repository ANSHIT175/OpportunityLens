# OpportunityLens — AI/ML Profile Intelligence × Official TinyFish Web Agent API

**Live Website (Vercel):** [https://opportunitylens-k3wq.vercel.app/](https://opportunitylens-k3wq.vercel.app/)  
**GitHub Repository:** [https://github.com/ANSHIT175/OpportunityLens](https://github.com/ANSHIT175/OpportunityLens)  
**Hackathon:** KRITICAL HACKS 2026 — AI/ML Track

---

## 1. Project Overview

**OpportunityLens** is a personalized opportunity-discovery and decision-intelligence platform built for students. Instead of forcing students to manually search across dozens of job boards, university lab pages, scholarship portals, and hackathon listings, OpportunityLens combines **live-web discovery and extraction via the official TinyFish Web Agent APIs** with a **transparent, reproducible 6-factor AI/ML matching and skill-gap engine**.

A student enters their academic profile (degree, graduation year, experience level, verified skills, career interests, and work-mode preferences) and a natural-language query. OpportunityLens discovers live pages, extracts structured requirements without fabricating missing data, computes an explainable match score, highlights exact missing skills, and provides direct links to official application portals.

---

## 2. Problem Statement & Target Users

### The Problem
- **Fragmented Sources:** High-value student opportunities (IIT research fellowships, startup AI internships, merit scholarships, and hackathons) are scattered across hundreds of independent portals.
- **Manual Eligibility & Requirement Checking:** Generic search engines return raw links without verifying whether a student's degree, graduation year, or skill stack actually satisfies the fine-print eligibility criteria.
- **Unexplained Fit & Unclear Skill Gaps:** Students rarely know *why* they are a 70% vs. 95% fit for a role or which single skill (e.g., `PyTorch` or `FastAPI`) will unlock the highest number of opportunities.

### Target Users
1. **Primary Users (B2C):** Undergraduate and postgraduate students (`B.Tech`, `M.Tech`, `BCA/B.Sc`, `PhD`) seeking internships, research fellowships, scholarships, and hackathons.
2. **Institutional Customers (B2B):** University Training & Placement (T&P) cells and coding bootcamps seeking batch-wide skill-gap analytics and verified opportunity feeds for their cohorts.

---

## 3. Main Features

- **Candidate Profile & Stack Builder (`localStorage` Persistent):** Configure degree, graduation year, experience level (`Beginner`, `Intermediate`, `Advanced`), career interests, work mode (`Any`, `Remote`, `India`, `Global`), and interactive skill tags that persist across browser refreshes.
- **Natural-Language Opportunity Search (with Input Validation):** Parses queries like *"Find AI/ML internships in India for a student with Python, PyTorch, FastAPI, Docker, Hugging Face, and SQL skills"* into structured search intent, while rejecting 1-character or ambiguous inputs (such as `"c"`).
- **Live TinyFish Multi-Stage Pipeline (SSE Streaming):** Streams real-time progress states (`Initializing search` → `Discovering opportunity sources` → `Extracting requirements` → `Checking eligibility information` → `Calculating matches` → `Completed / Failed`) from the backend.
- **Honest Normalization & Deduplication:** Preserves missing fields as `"Unknown (Not listed on page)"` rather than inventing deadlines, stipends, or skills; removes duplicate listings across sources; and flags broken or missing application URLs.
- **Transparent 6-Factor AI/ML Match Score (100-Point Rubric):**
  - **Skill Overlap (40 pts):** Core required skills matched + preferred skill bonus (awards neutral `14/40` if skills are not listed on the source page, never a fake 100%).
  - **Query & Role Relevance (20 pts):** Alignment with natural-language query tokens and domain.
  - **Eligibility Fit (15 pts):** Degree and graduation year verification.
  - **Career Interests Alignment (10 pts):** Overlap with student's stated career goals.
  - **Location & Work Mode Fit (10 pts):** Remote / India / Global preference alignment.
  - **Experience Level Fit (5 pts):** Alignment with `Beginner` / `Intermediate` / `Advanced` role expectations.
- **Interactive Skill-Gap Lab:** Ranks missing skills by opportunity demand (prioritizing the currently selected opportunity) and lets students click **`+ Simulate Learning <Skill>`** to immediately recalculate match scores across all extracted roles.
- **Live URL Scanner (`+ Scan URL`):** Validates any public `http(s)` opportunity URL (with SSRF protection blocking localhost/private IPs) and extracts structured fields via TinyFish Fetch + Web Agent APIs.
- **Persistent Saved & Applied Shortlist:** Save opportunities, generate tailored application pitches, mark roles as applied, and export the shortlist as `.JSON`.

---

## 4. Technology Stack

| Layer | Technology | Purpose |
| :--- | :--- | :--- |
| **Frontend UI** | HTML5, Tailwind CSS v3.4.17, Vanilla ES6+ JS | Responsive single-page workspace (`opportunitylens_app.html` / `index.html`) |
| **Primary Backend** | Python 3 (`server.py` & `api/index.py`) | Zero-dependency HTTP & Vercel Serverless backend with SSE streaming, SSRF guard & rate limiting |
| **Alternative Backend** | Node.js 18+ (`server.js`) | Built-in `http` + `fetch` server supporting identical `/api/*` endpoints |
| **Live-Web Intelligence** | [Official TinyFish Web Agent APIs](https://docs.tinyfish.ai) | Search API, Fetch API (`ttl: 0`), and Web Agent SSE (`run-sse`) |
| **Testing & QA** | Python `unittest` (`test_opportunitylens.py`) | Automated verification of secret safety, query validation, SSRF guard, deduplication & SSE flow |

---

## 5. System Architecture

```text
+-----------------------------------------------------------------------------------+
|                      OPPORTUNITYLENS FRONTEND (index.html)                        |
|  - Candidate Profile & Persistent Shortlist (localStorage)                        |
|  - Natural-Language Query Parser & 1-Char Query Validator                         |
|  - 6-Factor Reproducible AI/ML Matching Engine & Skill-Gap Simulator              |
+-----------------------------------------+-----------------------------------------+
                                          |
                     Relative HTTP / SSE  |  POST /api/tinyfish/run-sse
                     (Zero Exposed Keys)  |  POST /api/tinyfish/scan-url
                                          v
+-----------------------------------------------------------------------------------+
|               SECURE BACKEND ORCHESTRATOR (server.py / api/index.py)              |
|  - Reads TINYFISH_API_KEY strictly from Server Environment (os.environ)           |
|  - Per-IP Sliding-Window Rate Limiter (30 req/min) & 64KB Payload Cap             |
|  - Query Validator (>=3 chars) & External URL SSRF Protection                     |
+-------------------+-----------------------------+---------------------------------+
                    |                             |
   1. Discovery     |                             | 2. Markdown Extraction
   GET /search      v                             v POST /fetch (ttl: 0)
+-----------------------------------+   +-------------------------------------------+
|      TinyFish Search API          |   |           TinyFish Fetch API              |
|   https://api.search.tinyfish.ai  |   |       https://api.fetch.tinyfish.ai       |
+-----------------------------------+   +-------------------------------------------+
                    \                             /
                     \   3. Interactive DOM SSE  /
                      v  POST /v1/automation/run-sse
            +-----------------------------------------------+
            |           TinyFish Web Agent SSE API          |
            | https://agent.tinyfish.ai/v1/automation/run-sse|
            +-----------------------------------------------+
```

---

## 6. How TinyFish Is Integrated & Which APIs Are Used

All TinyFish calls are authenticated exclusively on the server using the `X-API-Key` HTTP header read from `os.environ["TINYFISH_API_KEY"]`:

1. **TinyFish Search API (`GET https://api.search.tinyfish.ai`):**
   - Called in `call_tinyfish_search()` with `query`, `purpose`, and `recency_minutes=525600` to discover live candidate opportunity URLs matching the student's query and degree profile.
2. **TinyFish Fetch API (`POST https://api.fetch.tinyfish.ai`):**
   - Called in `call_tinyfish_fetch()` with `urls`, `format: "markdown"`, and `ttl: 0` to retrieve fresh Markdown from discovered opportunity pages and parse explicit skills, deadlines, stipends, and eligibility rules.
3. **TinyFish Web Agent SSE API (`POST https://agent.tinyfish.ai/v1/automation/run-sse`):**
   - Called in `_handle_agent_sse_stream()` with `url`, natural-language `goal`, `output_schema` (`OPPORTUNITY_OUTPUT_SCHEMA`), and `browser_profile: "lite"`. Streams real-time `STARTED`, `STREAMING_URL`, `PROGRESS`, and `COMPLETE` Server-Sent Events to the frontend.
4. **TinyFish Synchronous Web Agent API (`POST https://agent.tinyfish.ai/v1/automation/run`):**
   - Called in `call_tinyfish_agent_sync()` for single-URL scanning (`/api/tinyfish/scan-url`) and the interactive API Playground (`/api/tinyfish/playground`).

---

## 7. Setup & Local Development Instructions

### Prerequisites
- **Python 3.9+** (standard library only — zero `pip install` required) **OR** **Node.js 18+**.
- A valid **TinyFish API Key** from [https://agent.tinyfish.ai/api-keys](https://agent.tinyfish.ai/api-keys).

### Step 1: Clone the Repository
```bash
git clone https://github.com/ANSHIT175/OpportunityLens.git
cd OpportunityLens
```

### Step 2: Configure Environment Variables
Copy `.env.example` to `.env` (which is ignored by Git via `.gitignore`) and insert your key:
```bash
cp .env.example .env
```
Or export `TINYFISH_API_KEY` directly in your terminal:
```bash
export TINYFISH_API_KEY="your_tinyfish_api_key_here"
export PORT="8000"
```

### Step 3: Start the Server
```bash
python3 server.py
```
Then open **[http://localhost:8000](http://localhost:8000)** in your browser.

### Step 4: Run the Automated Test Suite
```bash
python3 test_opportunitylens.py
```

---

## 8. Vercel Deployment Instructions

1. Push this repository to GitHub (`https://github.com/ANSHIT175/OpportunityLens`).
2. Import the repository into **[Vercel](https://vercel.com/new)**.
3. In your Vercel project dashboard, navigate to **Settings → Environment Variables** and add:
   - **Name:** `TINYFISH_API_KEY`
   - **Value:** `<your_actual_tinyfish_api_key>`
   - **Environments:** `Production`, `Preview`, `Development`
4. Trigger a **Redeploy** from the **Deployments** tab.
5. Verify your deployment by visiting `https://opportunitylens-k3wq.vercel.app/api/health` — it should return `"tinyfish_api_key_configured": true` without exposing your secret key.

---

## 9. Sample Queries to Try

- `"Find AI/ML internships in India for a student with Python, PyTorch, FastAPI, Docker, Hugging Face, and SQL skills"`
- `"AI/ML Research & Python Internships"`
- `"C++ Systems & Software Engineering Internships"`
- `"Remote Full-Stack, React & AI Web Agent Internships"`
- `"Computer Science Undergraduate Scholarships & Fellowships"`

---

## 10. Known Limitations

- **Serverless Execution Timeouts on Complex Portals:** On Vercel's free Hobby tier, serverless functions have a maximum execution window (10s–60s). If a target website uses heavy anti-bot challenges or slow multi-page navigation during a Web Agent run, OpportunityLens automatically merges and returns the structured data extracted from TinyFish Search + Fetch (`ttl: 0`) so the user still receives verified links and extracted text.
- **Unstated Fields on Source Pages:** Many university lab pages and startup career listings do not publicly list stipend amounts or closing dates. OpportunityLens intentionally displays `"Unknown (Not listed on page)"` rather than guessing or fabricating numbers.
- **API Credit Consumption:** Every live search or URL scan consumes real TinyFish API credits associated with the server's `TINYFISH_API_KEY`.
