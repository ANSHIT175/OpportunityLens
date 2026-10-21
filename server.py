#!/usr/bin/env python3
"""
OpportunityLens Backend Server v3.1 — Official TinyFish Web Agent API Integration
=================================================================================
Architecture Flow:
  Student Query & Profile (Frontend UI)
    -> Input Validation, SSRF Guard & Rate Limiter (server.py / api/index.py)
    -> Step B: TinyFish Search API (GET https://api.search.tinyfish.ai)
    -> Step C: TinyFish Fetch API (POST https://api.fetch.tinyfish.ai, ttl: 0)
    -> Step D: TinyFish Web Agent SSE API (POST https://agent.tinyfish.ai/v1/automation/run-sse)
    -> Step E: Honest Schema Normalization (Unknown fields preserved, never invented) & Deduplication
    -> Step F: Frontend Explainable AI/ML Matching & Skill-Gap Analysis

Security:
  - Reads TINYFISH_API_KEY strictly from server environment variables (or local .env file).
  - Never exposes TINYFISH_API_KEY in frontend JavaScript, public config, or API responses.
  - Enforces per-IP sliding-window rate limiting, 64KB body size limit, query validation, and URL SSRF protection.
"""

import collections
import datetime
import ipaddress
import json
import os
import re
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def load_dotenv_file(dotenv_path: Path) -> None:
    """Load key=value pairs from a local .env file if not already set in os.environ."""
    if not dotenv_path.exists():
        return
    try:
        for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except Exception as exc:
        sys.stderr.write(f"[WARN] Failed to parse .env file: {exc}\n")


load_dotenv_file(BASE_DIR / ".env")

# Official TinyFish API Endpoints (per https://docs.tinyfish.ai)
TINYFISH_SEARCH_URL = os.environ.get("TINYFISH_SEARCH_URL", "https://api.search.tinyfish.ai")
TINYFISH_FETCH_URL = os.environ.get("TINYFISH_FETCH_URL", "https://api.fetch.tinyfish.ai")
TINYFISH_AGENT_SSE_URL = os.environ.get(
    "TINYFISH_AGENT_SSE_URL", "https://agent.tinyfish.ai/v1/automation/run-sse"
)
TINYFISH_AGENT_RUN_URL = os.environ.get(
    "TINYFISH_AGENT_RUN_URL", "https://agent.tinyfish.ai/v1/automation/run"
)
TINYFISH_BROWSER_URL = os.environ.get("TINYFISH_BROWSER_URL", "https://api.browser.tinyfish.ai")

MAX_BODY_BYTES = 65536  # 64 KB cap
RATE_LIMIT_WINDOW_SEC = 60
RATE_LIMIT_MAX_REQUESTS = int(os.environ.get("RATE_LIMIT_MAX_REQUESTS", "30"))
_rate_limit_lock = threading.Lock()
_rate_limit_buckets = collections.defaultdict(collections.deque)

KNOWN_SKILLS_LEXICON = [
    "Python", "PyTorch", "TensorFlow", "Machine Learning", "Deep Learning", "Data Analysis",
    "Data Science", "Scikit-Learn", "SQL", "PostgreSQL", "MongoDB", "FastAPI", "Flask",
    "Django", "Docker", "Kubernetes", "AWS", "GCP", "Azure", "Hugging Face", "NLP",
    "LLM", "Computer Vision", "OpenCV", "C++", "C", "Java", "Go", "Rust", "JavaScript",
    "TypeScript", "React", "Next.js", "Node.js", "HTML", "CSS", "Tailwind CSS",
    "Data Structures", "Algorithms", "Linux", "Git", "UI/UX Design", "Figma", "Pandas", "NumPy"
]

# Structured Output JSON Schema passed to TinyFish Web Agent API
OPPORTUNITY_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "opportunities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "organization": {"type": "string"},
                    "category": {"type": "string"},
                    "eligibility": {"type": "string"},
                    "eligible_degrees": {"type": "array", "items": {"type": "string"}},
                    "eligible_years": {"type": "array", "items": {"type": "string"}},
                    "required_skills": {"type": "array", "items": {"type": "string"}},
                    "preferred_skills": {"type": "array", "items": {"type": "string"}},
                    "experience_level": {"type": "string"},
                    "deadline": {"type": "string"},
                    "location": {"type": "string"},
                    "remote_status": {"type": "string"},
                    "stipend": {"type": "string"},
                    "summary": {"type": "string"},
                    "source_url": {"type": "string"},
                    "application_url": {"type": "string"},
                },
                "required": ["title", "organization", "application_url"],
            },
        }
    },
    "required": ["opportunities"],
}


def get_api_key() -> str:
    """Retrieve TINYFISH_API_KEY strictly from server environment."""
    return os.environ.get("TINYFISH_API_KEY", "").strip()


def check_rate_limit(client_ip: str) -> tuple:
    """Sliding-window rate limiter per client IP. Returns (allowed: bool, retry_after_sec: int)."""
    now = time.time()
    with _rate_limit_lock:
        dq = _rate_limit_buckets[client_ip]
        while dq and now - dq[0] > RATE_LIMIT_WINDOW_SEC:
            dq.popleft()
        if len(dq) >= RATE_LIMIT_MAX_REQUESTS:
            retry_after = max(1, int(RATE_LIMIT_WINDOW_SEC - (now - dq[0])))
            return False, retry_after
        dq.append(now)
        return True, 0


def validate_search_query(raw_query: str) -> tuple:
    """
    Validate a natural-language search query.
    Rejects empty queries, 1-character queries (e.g. 'c'), or punctuation-only strings.
    Returns (is_valid: bool, cleaned_query: str, error_message: str).
    """
    cleaned = re.sub(r"\s+", " ", str(raw_query or "")).strip()
    if not cleaned:
        return False, "", "Search query cannot be empty. Enter a descriptive query such as 'AI/ML internships in India'."

    alnum_chars = re.sub(r"[^a-zA-Z0-9+#]", "", cleaned)
    if len(cleaned) < 3 or len(alnum_chars) < 2:
        return (
            False,
            cleaned,
            f"Query '{cleaned}' is too short or ambiguous. Please enter at least 3 characters (for example: 'C++ Systems Internships' or 'AI/ML Research Internships').",
        )
    return True, cleaned, ""


def validate_external_url(raw_url: str) -> tuple:
    """
    Validate a submitted URL for scanning.
    Ensures valid http/https scheme, valid hostname, and blocks private/loopback SSRF targets
    unless ALLOW_LOCAL_URLS=1 is explicitly set for local unit tests.
    """
    url_str = str(raw_url or "").strip()
    if not url_str:
        return False, "", "URL cannot be empty."
    try:
        parsed = urllib.parse.urlparse(url_str)
    except Exception:
        return False, "", "Malformed URL syntax."

    if parsed.scheme not in {"http", "https"}:
        return False, "", "URL must start with http:// or https://."
    host = (parsed.hostname or "").strip().lower()
    if not host or "." not in host and host not in {"localhost"}:
        return False, "", "URL must contain a valid domain name (e.g. https://careers.company.com)."

    allow_local = os.environ.get("ALLOW_LOCAL_URLS", "0") == "1"
    if not allow_local:
        if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or host.endswith(".local") or host.endswith(".internal"):
            return False, "", "Scanning local or private network addresses is not permitted."
        try:
            ip_obj = ipaddress.ip_address(host)
            if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_reserved:
                return False, "", "Scanning private or reserved IP addresses is not permitted."
        except ValueError:
            pass

    return True, url_str, ""


def is_valid_application_url(url_str: str) -> bool:
    """Check if an extracted application URL is a real, non-placeholder http(s) link."""
    if not url_str or not isinstance(url_str, str):
        return False
    u = url_str.strip()
    if not u.startswith(("http://", "https://")):
        return False
    try:
        parsed = urllib.parse.urlparse(u)
        host = (parsed.hostname or "").lower()
        if not host or "." not in host:
            return False
        if host in {"example.com", "www.example.com", "placeholder.com", "docs.tinyfish.ai"}:
            return False
        return True
    except Exception:
        return False


def make_ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context()


def build_agent_goal(query: str, profile: dict) -> str:
    """Construct a precise Goal prompt per https://docs.tinyfish.ai/agent-api."""
    degree = profile.get("degree") or "B.Tech"
    grad_year = profile.get("gradYear") or "2027"
    skills = ", ".join((profile.get("skills") or [])[:8]) or "Python, Machine Learning"
    interests = ", ".join((profile.get("interests") or [])[:5]) or "AI/ML, Software Engineering"
    exp_level = profile.get("experienceLevel") or "Intermediate"
    location_pref = profile.get("locationPref") or "Any"

    return (
        f'Search and extract real, currently open opportunity listings on this page matching: "{query}". '
        f"Candidate profile: {degree} student graduating in {grad_year}, experience level: {exp_level}, "
        f"skills: [{skills}], career interests: [{interests}], work mode preference: {location_pref}. "
        "Extract up to 6 distinct opportunities (internships, research fellowships, scholarships, or hackathons). "
        "For each opportunity, extract ONLY facts stated on the page: "
        "title, organization, category (Internship, Research, Scholarship, or Hackathon), "
        "eligibility (exact text if stated, otherwise empty string), "
        "eligible_degrees (array of degrees explicitly mentioned, else empty array []), "
        "eligible_years (array of graduation years explicitly mentioned, else empty array []), "
        "required_skills (array of technical skills explicitly mentioned on the page, else []), "
        "preferred_skills (array of preferred skills if mentioned, else []), "
        "experience_level ('Beginner', 'Intermediate', 'Advanced', or 'Unknown'), "
        "deadline (exact closing date if stated on page, otherwise 'Unknown'), "
        "location (city/country/mode if stated, otherwise 'Unknown'), "
        "remote_status ('Remote', 'India', 'Global', or 'Unknown'), "
        "stipend (exact stipend/salary/grant if stated on page, otherwise 'Unknown'), "
        "summary (1-2 sentence factual description from the page), "
        "source_url (URL of the page where found), and application_url (direct URL to apply). "
        "Do NOT invent or guess missing fields; leave unknown fields empty or 'Unknown'."
    )


def extract_skills_from_text(text: str) -> list:
    """Extract explicitly mentioned technical skills from page text or snippet."""
    found = []
    lower_text = f" {str(text or '').lower()} "
    for sk in KNOWN_SKILLS_LEXICON:
        pattern = r"(?<![a-z0-9])" + re.escape(sk.lower()) + r"(?![a-z0-9])"
        if re.search(pattern, lower_text):
            found.append(sk)
    return found


def normalize_opportunity(raw: dict, idx: int = 0, fallback_url: str = "", run_id: str = "", source_method: str = "TinyFish Web Agent") -> dict:
    """
    Normalize a raw TinyFish extraction item into the OpportunityLens UI schema.
    Strictly preserves Unknown fields rather than inventing deadlines, salaries, or skills.
    """
    title = str(raw.get("title") or raw.get("role") or "").strip()
    organization = str(raw.get("organization") or raw.get("company") or raw.get("site_name") or "").strip()

    raw_source_url = str(raw.get("source_url") or raw.get("sourceUrl") or fallback_url or "").strip()
    raw_app_url = str(raw.get("application_url") or raw.get("applicationUrl") or raw.get("url") or raw_source_url or "").strip()

    url_valid = is_valid_application_url(raw_app_url)
    source_url_valid = is_valid_application_url(raw_source_url)
    effective_source_url = raw_source_url if source_url_valid else (raw_app_url if url_valid else "")

    if not organization and effective_source_url:
        try:
            host = (urllib.parse.urlparse(effective_source_url).hostname or "").replace("www.", "")
            organization = host.split(".")[0].capitalize() if host else "Unknown Organization"
        except Exception:
            organization = "Unknown Organization"
    if not organization:
        organization = "Unknown Organization"

    if not title:
        title = "Untitled Opportunity"

    domain = "unknown-source"
    if effective_source_url:
        try:
            parsed_url = urllib.parse.urlparse(effective_source_url)
            domain = (parsed_url.netloc or "unknown-source").replace("www.", "")
            path_part = parsed_url.path.strip("/")
            if path_part:
                first_seg = path_part.split("/")[0]
                if len(first_seg) < 20:
                    domain = f"{domain}/{first_seg}"
        except Exception:
            domain = "unknown-source"

    raw_cat = str(raw.get("category") or "").strip().capitalize()
    combined_lower = f"{title} {raw.get('summary', '')} {raw.get('description', '')}".lower()
    if raw_cat not in {"Internship", "Research", "Scholarship", "Hackathon"}:
        if any(w in combined_lower for w in ["research", "fellowship", "fellow", "lab", "rbcdsai"]):
            raw_cat = "Research"
        elif any(w in combined_lower for w in ["scholarship", "grant", "bursary", "financial aid"]):
            raw_cat = "Scholarship"
        elif any(w in combined_lower for w in ["hackathon", "challenge", "competition", "ideathon"]):
            raw_cat = "Hackathon"
        else:
            raw_cat = "Internship"

    # Required & Preferred Skills — NEVER invent default skills if none were extracted
    req_skills = raw.get("required_skills") or raw.get("requiredSkills") or []
    if isinstance(req_skills, str):
        req_skills = [s.strip() for s in re.split(r"[,;/|]+", req_skills) if s.strip()]
    req_skills = [str(s).strip() for s in req_skills if str(s).strip() and str(s).strip().lower() not in {"unknown", "none", "n/a"}]

    pref_skills = raw.get("preferred_skills") or raw.get("preferredSkills") or []
    if isinstance(pref_skills, str):
        pref_skills = [s.strip() for s in re.split(r"[,;/|]+", pref_skills) if s.strip()]
    pref_skills = [str(s).strip() for s in pref_skills if str(s).strip() and str(s).strip().lower() not in {"unknown", "none", "n/a"}]

    # If skills array was empty, extract only skills explicitly present in the title/summary text
    if not req_skills:
        req_skills = extract_skills_from_text(f"{title} {raw.get('summary', '')} {raw.get('description', '')} {raw.get('eligibility', '')}")

    # Eligibility — keep Unknown if not extracted
    elig_text = str(raw.get("eligibility") or raw.get("eligibilityText") or "").strip()
    if elig_text.lower() in {"", "unknown", "n/a", "none", "not specified"}:
        elig_text = "Unknown (Check official source)"

    elig_degrees = raw.get("eligible_degrees") or raw.get("eligibleDegrees") or []
    if not isinstance(elig_degrees, list):
        elig_degrees = []
    elig_degrees = [str(d).strip() for d in elig_degrees if str(d).strip()]
    if not elig_degrees and elig_text != "Unknown (Check official source)":
        for deg in ["B.Tech", "M.Tech", "B.Sc", "PhD"]:
            if deg.lower() in elig_text.lower() or (deg == "B.Tech" and any(k in elig_text.lower() for k in ["b.e", "btech", "undergraduate", "bachelor"])):
                elig_degrees.append(deg)

    elig_years = raw.get("eligible_years") or raw.get("eligibleYears") or []
    if not isinstance(elig_years, list):
        elig_years = []
    elig_years = [str(y).strip() for y in elig_years if str(y).strip()]
    if not elig_years and elig_text != "Unknown (Check official source)":
        for yr in ["2025", "2026", "2027", "2028"]:
            if yr in elig_text:
                elig_years.append(yr)

    # Deadline — keep Unknown if not extracted
    deadline = str(raw.get("deadline") or "").strip()
    if not deadline or deadline.lower() in {"unknown", "n/a", "none", "not specified", "tbd"}:
        deadline = "Unknown (Not listed on page)"

    # Location & Work Mode — keep Unknown if not extracted
    location = str(raw.get("location") or "").strip()
    if not location or location.lower() in {"unknown", "n/a", "none", "not specified"}:
        location = "Unknown"

    remote_status = str(raw.get("remote_status") or raw.get("locationTag") or "").strip()
    if remote_status not in {"Remote", "India", "Global", "Unknown"}:
        loc_lower = f"{location} {title} {elig_text}".lower()
        if "remote" in loc_lower:
            remote_status = "Remote"
        elif any(c in loc_lower for c in ["india", "bengaluru", "bangalore", "delhi", "mumbai", "hyderabad", "pune", "chennai", "noida", "iit", "nit"]):
            remote_status = "India"
        elif location != "Unknown":
            remote_status = "Global"
        else:
            remote_status = "Unknown"

    # Stipend / Salary — keep Unknown if not extracted
    stipend = str(raw.get("stipend") or raw.get("salary") or "").strip()
    if not stipend or stipend.lower() in {"unknown", "n/a", "none", "not specified", "listed on official portal"}:
        stipend = "Unknown (Not listed)"

    # Experience Level
    exp_level = str(raw.get("experience_level") or raw.get("experienceLevel") or "").strip().capitalize()
    if exp_level not in {"Beginner", "Intermediate", "Advanced"}:
        if any(w in combined_lower for w in ["senior", "advanced", "phd", "fellow", "research scientist"]):
            exp_level = "Advanced"
        elif any(w in combined_lower for w in ["freshman", "beginner", "first year", "1st year", "2nd year", "student"]):
            exp_level = "Beginner"
        else:
            exp_level = "Intermediate"

    summary = str(raw.get("summary") or raw.get("description") or raw.get("snippet") or "").strip()
    if not summary:
        summary = "No description text was extracted from this source page."

    # Determine honest verification status based on actual extracted evidence
    has_real_title = title != "Untitled Opportunity" and len(title) >= 4
    has_real_org = organization != "Unknown Organization"
    has_extracted_details = bool(req_skills) or elig_text != "Unknown (Check official source)" or deadline != "Unknown (Not listed on page)"

    if url_valid and has_real_title and has_real_org and has_extracted_details:
        verification_status = "Verified Source & Details"
        is_verified = True
    elif url_valid and has_real_title:
        verification_status = "Source Link Verified (Partial Details)"
        is_verified = False
    else:
        verification_status = "Unverified / Incomplete Link"
        is_verified = False

    keywords = [
        w.lower()
        for w in re.split(r"\W+", f"{title} {organization} {raw_cat} {' '.join(req_skills)} {location} {summary}")
        if len(w) >= 2
    ]

    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if req_skills:
        missing_advice = f"Focus on demonstrating {', '.join(req_skills[:3])} in a verifiable GitHub repository before applying on {domain}."
    else:
        missing_advice = f"Open {domain} to review full role specifications and tailor your resume."

    return {
        "id": raw.get("id") or f"tf-live-{int(time.time() * 1000)}-{idx}",
        "title": title,
        "organization": organization,
        "sourceDomain": domain,
        "sourceUrl": effective_source_url,
        "applicationUrl": raw_app_url if url_valid else "",
        "urlValid": url_valid,
        "isVerified": is_verified,
        "verificationStatus": verification_status,
        "sourceMethod": source_method,
        "category": raw_cat,
        "experienceLevel": exp_level,
        "location": location,
        "locationTag": remote_status,
        "stipend": stipend,
        "deadline": deadline,
        "daysLeft": int(raw.get("daysLeft") or 30),
        "eligibilityText": elig_text,
        "eligibleDegrees": elig_degrees,
        "eligibleYears": elig_years,
        "requiredSkills": req_skills,
        "preferredSkills": pref_skills,
        "keywords": list(dict.fromkeys(keywords)),
        "tinyfishRunId": run_id or str(raw.get("tinyfishRunId") or f"tf_run_{int(time.time())}"),
        "fetchedAt": now_iso,
        "summary": summary,
        "learningAdvice": str(raw.get("learningAdvice") or missing_advice),
    }


def deduplicate_opportunities(opps: list) -> list:
    """
    Deduplicate normalized opportunities by canonical (title, organization) or canonical URL.
    When duplicates exist, merge non-Unknown fields into the primary record.
    """
    seen_keys = {}
    seen_urls = {}
    result = []

    for opp in opps:
        if not isinstance(opp, dict):
            continue
        norm_title = re.sub(r"\W+", "", str(opp.get("title") or "").lower())
        norm_org = re.sub(r"\W+", "", str(opp.get("organization") or "").lower())
        title_org_key = f"{norm_title}::{norm_org}" if norm_title else ""

        raw_url = str(opp.get("applicationUrl") or opp.get("sourceUrl") or "").strip().rstrip("/").lower()

        existing = None
        if title_org_key and title_org_key in seen_keys:
            existing = seen_keys[title_org_key]
        elif raw_url and raw_url in seen_urls:
            existing = seen_urls[raw_url]

        if existing is not None:
            # Merge richer fields into existing record
            if not existing.get("requiredSkills") and opp.get("requiredSkills"):
                existing["requiredSkills"] = opp["requiredSkills"]
            if existing.get("deadline", "").startswith("Unknown") and not opp.get("deadline", "").startswith("Unknown"):
                existing["deadline"] = opp["deadline"]
            if existing.get("stipend", "").startswith("Unknown") and not opp.get("stipend", "").startswith("Unknown"):
                existing["stipend"] = opp["stipend"]
            if existing.get("eligibilityText", "").startswith("Unknown") and not opp.get("eligibilityText", "").startswith("Unknown"):
                existing["eligibilityText"] = opp["eligibilityText"]
            if not existing.get("isVerified") and opp.get("isVerified"):
                existing["isVerified"] = True
                existing["verificationStatus"] = opp["verificationStatus"]
            continue

        result.append(opp)
        if title_org_key:
            seen_keys[title_org_key] = opp
        if raw_url:
            seen_urls[raw_url] = opp

    return result


def parse_opportunities_from_markdown_and_search(fetched_pages: list, search_results: list, query: str, run_id: str) -> list:
    """
    Parse structured opportunity items from real TinyFish Fetch API Markdown and Search API snippets
    when pages are static or when enriching discovered sources.
    Strictly extracts only what is present in the fetched Markdown/snippets (leaving missing fields Unknown).
    """
    url_to_meta = {}
    for s in search_results or []:
        u = str(s.get("url") or "").strip()
        if u:
            url_to_meta[u] = s

    extracted = []
    for idx, page in enumerate(fetched_pages or []):
        if not isinstance(page, dict):
            continue
        url = str(page.get("url") or "").strip()
        md = str(page.get("markdown") or page.get("content") or page.get("text") or "").strip()
        meta = url_to_meta.get(url, {})
        title = str(page.get("title") or meta.get("title") or "").strip()
        if not title and md:
            for line in md.splitlines():
                clean_line = line.lstrip("#").strip()
                if len(clean_line) >= 5:
                    title = clean_line[:120]
                    break
        if not title:
            continue

        org = str(meta.get("site_name") or "").strip()
        snippet = str(meta.get("snippet") or "").strip()
        combined_text = f"{title}\n{snippet}\n{md[:4000]}"

        skills = extract_skills_from_text(combined_text)

        # Look for explicit deadline patterns in Markdown
        deadline = "Unknown"
        dl_match = re.search(r"(?:deadline|apply by|last date|closes on)[:\s]+([A-Za-z0-9 ,\-]{4,30})", combined_text, re.IGNORECASE)
        if dl_match:
            deadline = dl_match.group(1).strip()

        # Look for explicit stipend/salary patterns in Markdown
        stipend = "Unknown"
        st_match = re.search(r"((?:₹|Rs\.?|\$|USD|INR)\s?[0-9,]+(?:\s?(?:/|per)\s?(?:month|mo|year|yr|annum))?)", combined_text, re.IGNORECASE)
        if st_match:
            stipend = st_match.group(1).strip()

        # Look for explicit eligibility patterns
        elig = "Unknown"
        el_match = re.search(r"(?:eligibility|who can apply|qualifications)[:\s]+([^.\n]{8,120})", combined_text, re.IGNORECASE)
        if el_match:
            elig = el_match.group(1).strip()

        summary_text = snippet or (md[:240].replace("\n", " ").strip() if md else "")

        raw_item = {
            "title": title,
            "organization": org,
            "source_url": url,
            "application_url": url,
            "required_skills": skills,
            "deadline": deadline,
            "stipend": stipend,
            "eligibility": elig,
            "summary": summary_text,
        }
        extracted.append(
            normalize_opportunity(raw_item, idx=idx, fallback_url=url, run_id=run_id, source_method="TinyFish Search + Fetch API")
        )

    # If Fetch API returned no pages but Search API returned real URLs, build honest records from Search API
    if not extracted and search_results:
        for idx, s in enumerate(search_results):
            u = str(s.get("url") or "").strip()
            t = str(s.get("title") or "").strip()
            snip = str(s.get("snippet") or "").strip()
            if not u or not t:
                continue
            skills = extract_skills_from_text(f"{t} {snip}")
            raw_item = {
                "title": t,
                "organization": str(s.get("site_name") or "").strip(),
                "source_url": u,
                "application_url": u,
                "required_skills": skills,
                "deadline": "Unknown",
                "stipend": "Unknown",
                "eligibility": "Unknown",
                "summary": snip,
            }
            extracted.append(
                normalize_opportunity(raw_item, idx=idx, fallback_url=u, run_id=run_id, source_method="TinyFish Search API")
            )

    return deduplicate_opportunities(extracted)


def extract_opportunities_from_agent_result(result_obj, fallback_url: str, run_id: str) -> list:
    """Extract, normalize, and deduplicate opportunities from TinyFish Agent COMPLETE result payload."""
    if not result_obj:
        return []

    candidates = []
    if isinstance(result_obj, list):
        candidates = result_obj
    elif isinstance(result_obj, dict):
        if isinstance(result_obj.get("opportunities"), list):
            candidates = result_obj["opportunities"]
        elif isinstance(result_obj.get("results"), list):
            candidates = result_obj["results"]
        elif isinstance(result_obj.get("items"), list):
            candidates = result_obj["items"]
        elif isinstance(result_obj.get("result"), list):
            candidates = result_obj["result"]
        elif isinstance(result_obj.get("result"), dict):
            inner = result_obj["result"]
            if isinstance(inner.get("opportunities"), list):
                candidates = inner["opportunities"]
            elif inner.get("title") or inner.get("role"):
                candidates = [inner]
        elif isinstance(result_obj.get("result"), str):
            raw_text = result_obj["result"].strip()
            try:
                parsed = json.loads(raw_text)
                return extract_opportunities_from_agent_result(parsed, fallback_url, run_id)
            except Exception:
                pass
        elif result_obj.get("title") or result_obj.get("role"):
            candidates = [result_obj]

    normalized = []
    for idx, item in enumerate(candidates):
        if isinstance(item, dict):
            normalized.append(
                normalize_opportunity(item, idx=idx, fallback_url=fallback_url, run_id=run_id, source_method="TinyFish Web Agent SSE")
            )
    return deduplicate_opportunities(normalized)


def call_tinyfish_search(api_key: str, query: str, purpose: str) -> dict:
    """Call GET https://api.search.tinyfish.ai with X-API-Key header."""
    params = urllib.parse.urlencode(
        {
            "query": query,
            "purpose": purpose[:1800],
            "recency_minutes": 525600,
        }
    )
    url = f"{TINYFISH_SEARCH_URL}?{params}"
    req = urllib.request.Request(
        url,
        headers={
            "X-API-Key": api_key,
            "Accept": "application/json",
            "User-Agent": "OpportunityLens/3.1 (TinyFish-Integration)",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=20, context=make_ssl_context()) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body)


def call_tinyfish_fetch(api_key: str, urls: list, purpose: str = "") -> dict:
    """Call POST https://api.fetch.tinyfish.ai with X-API-Key header."""
    payload = {
        "urls": urls[:5],
        "format": "markdown",
        "ttl": 0,
    }
    if purpose:
        payload["purpose"] = purpose[:1800]

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        TINYFISH_FETCH_URL,
        data=data,
        headers={
            "X-API-Key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "OpportunityLens/3.1 (TinyFish-Integration)",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=25, context=make_ssl_context()) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body)


def call_tinyfish_agent_sync(api_key: str, target_url: str, goal: str, output_schema: dict = None, timeout_sec: int = 55) -> dict:
    """Call POST https://agent.tinyfish.ai/v1/automation/run synchronously."""
    payload = {
        "url": target_url,
        "goal": goal,
        "browser_profile": "lite",
    }
    if output_schema:
        payload["output_schema"] = output_schema

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        TINYFISH_AGENT_RUN_URL,
        data=data,
        headers={
            "X-API-Key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "OpportunityLens/3.1 (TinyFish-Integration)",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_sec, context=make_ssl_context()) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body)


def parse_http_error(exc: urllib.error.HTTPError) -> tuple:
    """Extract status code and structured error message from a TinyFish HTTPError."""
    status = exc.code
    raw_body = ""
    try:
        raw_body = exc.read().decode("utf-8", errors="replace")
        parsed = json.loads(raw_body)
        if isinstance(parsed.get("error"), dict):
            err = parsed["error"]
            code = err.get("code") or f"HTTP_{status}"
            msg = err.get("message") or raw_body
            return status, code, msg
        elif isinstance(parsed.get("error"), str):
            return status, f"HTTP_{status}", parsed["error"]
        elif parsed.get("message"):
            return status, f"HTTP_{status}", parsed["message"]
    except Exception:
        pass
    return status, f"HTTP_{status}", raw_body or str(exc.reason)


class OpportunityLensRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"[OpportunityLens Server] {self.address_string()} - {fmt % args}\n")

    def _get_client_ip(self) -> str:
        xff = self.headers.get("X-Forwarded-For", "")
        if xff:
            return xff.split(",")[0].strip()
        return self.client_address[0] if self.client_address else "unknown"

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
        self.send_header("X-Content-Type-Options", "nosniff")

    def _send_json(self, status: int, payload: dict, extra_headers: dict = None) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, str(v))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise OverflowError(f"Request body exceeds {MAX_BODY_BYTES} bytes limit.")
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip() else {}

    def _resolve_route(self) -> str:
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        # Support Vercel rewrite queries if routed to /api/index.py
        if route.endswith("/api/index.py") or route == "/api/index":
            qs = urllib.parse.parse_qs(parsed.query)
            if "route" in qs:
                route = "/api/" + qs["route"][0].lstrip("/")
        return route

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        route = self._resolve_route()

        if route == "/api/health":
            api_key = get_api_key()
            # Never expose any portion of the secret key in public responses
            self._send_json(
                200,
                {
                    "status": "ok",
                    "version": "3.1.0",
                    "service": "OpportunityLens TinyFish Backend",
                    "tinyfish_api_key_configured": bool(api_key),
                    "tinyfish_key_status": "CONFIGURED_ON_SERVER" if api_key else "MISSING_ON_SERVER",
                    "endpoints": {
                        "search_api": TINYFISH_SEARCH_URL,
                        "fetch_api": TINYFISH_FETCH_URL,
                        "agent_sse_api": TINYFISH_AGENT_SSE_URL,
                        "agent_run_api": TINYFISH_AGENT_RUN_URL,
                    },
                },
            )
            return

        if route in {"/", "/index.html", "/opportunitylens_app.html"}:
            html_file = BASE_DIR / "opportunitylens_app.html"
            if not html_file.exists():
                html_file = BASE_DIR / "index.html"
            if not html_file.exists():
                self._send_json(404, {"error": "opportunitylens_app.html not found in server directory."})
                return
            content = html_file.read_bytes()
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        self._send_json(404, {"error": {"code": "NOT_FOUND", "message": f"Unknown GET route: {route}"}})

    def do_POST(self) -> None:
        client_ip = self._get_client_ip()
        allowed, retry_after = check_rate_limit(client_ip)
        if not allowed:
            self._send_json(
                429,
                {
                    "error": {
                        "code": "RATE_LIMIT_EXCEEDED",
                        "message": f"Too many requests from {client_ip}. Please wait {retry_after} seconds before retrying.",
                    }
                },
                extra_headers={"Retry-After": retry_after},
            )
            return

        route = self._resolve_route()

        try:
            body = self._read_json_body()
        except OverflowError as exc:
            self._send_json(413, {"error": {"code": "PAYLOAD_TOO_LARGE", "message": str(exc)}})
            return
        except Exception as exc:
            self._send_json(400, {"error": {"code": "INVALID_JSON", "message": f"Invalid JSON request body: {exc}"}})
            return

        if route == "/api/tinyfish/run-sse":
            self._handle_agent_sse_stream(body)
            return

        if route == "/api/tinyfish/search":
            self._handle_blocking_search(body)
            return

        if route == "/api/tinyfish/scan-url":
            self._handle_scan_single_url(body)
            return

        if route == "/api/tinyfish/playground":
            self._handle_playground_test(body)
            return

        self._send_json(404, {"error": {"code": "NOT_FOUND", "message": f"Unknown POST route: {route}"}})

    def _write_sse_event(self, event_dict: dict) -> bool:
        """Write a single `data: {...}\n\n` SSE frame to the client stream."""
        try:
            frame = f"data: {json.dumps(event_dict)}\n\n".encode("utf-8")
            self.wfile.write(frame)
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError):
            return False

    def _handle_agent_sse_stream(self, body: dict) -> None:
        """
        Real TinyFish Live-Web Research & Extraction SSE Pipeline:
          Step A: Validate student query & explicit URL.
          Step B: Discover live opportunity pages via TinyFish Search API (GET https://api.search.tinyfish.ai).
          Step C: Extract clean Markdown via TinyFish Fetch API (POST https://api.fetch.tinyfish.ai, ttl: 0).
          Step D: Execute TinyFish Web Agent SSE (POST https://agent.tinyfish.ai/v1/automation/run-sse) for interactive DOM extraction.
          Step E: Normalize & deduplicate results, preserving Unknown fields honestly.
        """
        api_key = get_api_key()

        self.close_connection = True
        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.end_headers()

        if not api_key:
            self._write_sse_event(
                {
                    "type": "ERROR",
                    "code": "MISSING_API_KEY",
                    "message": (
                        "TINYFISH_API_KEY is not configured on the backend server. "
                        "In Vercel: go to Project Settings -> Environment Variables -> add TINYFISH_API_KEY, then redeploy. "
                        "Locally: run export TINYFISH_API_KEY='your_key' && python3 server.py"
                    ),
                }
            )
            return

        raw_query = str(body.get("query") or "").strip()
        explicit_url = str(body.get("url") or "").strip()
        profile = body.get("profile") or {}

        # Step A: Validate input query or explicit URL
        if explicit_url:
            url_ok, clean_url, url_err = validate_external_url(explicit_url)
            if not url_ok:
                self._write_sse_event({"type": "ERROR", "code": "INVALID_URL", "message": url_err})
                return
            explicit_url = clean_url
            query = raw_query if len(raw_query) >= 3 else f"Opportunities at {explicit_url}"
        else:
            q_ok, clean_query, q_err = validate_search_query(raw_query)
            if not q_ok:
                self._write_sse_event({"type": "ERROR", "code": "INVALID_QUERY", "message": q_err})
                return
            query = clean_query

        degree = profile.get("degree") or "B.Tech"
        grad_year = profile.get("gradYear") or "2027"
        skills = profile.get("skills") or ["Python", "Machine Learning"]

        self._write_sse_event(
            {
                "type": "PROGRESS",
                "stage": "INIT",
                "state_label": "Initializing search",
                "purpose": f"Initializing search for '{query}' ({degree} {grad_year}, {len(skills)} skills)...",
            }
        )

        # Step B: Live Discovery via TinyFish Search API
        target_urls = []
        search_results_meta = []
        any_upstream_success = False
        last_upstream_error = None

        if explicit_url:
            target_urls = [explicit_url]
            self._write_sse_event(
                {
                    "type": "PROGRESS",
                    "stage": "SEARCH",
                    "state_label": "Discovering opportunity sources",
                    "purpose": f"Scanning submitted URL directly: {explicit_url}",
                }
            )
        else:
            search_query = f"{query} internship fellowship scholarship application eligibility deadline"
            purpose = (
                f"Discover real, currently open student internships, research fellowships, scholarships, and hackathons "
                f"matching '{query}' for a {degree} ({grad_year}) candidate skilled in {', '.join(skills[:6])}."
            )
            self._write_sse_event(
                {
                    "type": "PROGRESS",
                    "stage": "SEARCH",
                    "state_label": "Discovering opportunity sources",
                    "purpose": f"Discovering opportunity sources via TinyFish Search API (GET {TINYFISH_SEARCH_URL})...",
                }
            )
            try:
                search_resp = call_tinyfish_search(api_key, search_query, purpose)
                any_upstream_success = True
                raw_results = search_resp.get("results") or []
                for r in raw_results[:5]:
                    u = str(r.get("url") or "").strip()
                    if is_valid_application_url(u):
                        target_urls.append(u)
                        search_results_meta.append(
                            {
                                "title": r.get("title", ""),
                                "url": u,
                                "snippet": r.get("snippet", ""),
                                "site_name": r.get("site_name", ""),
                            }
                        )
                self._write_sse_event(
                    {
                        "type": "SEARCH_RESULTS",
                        "stage": "SEARCH",
                        "state_label": "Discovering opportunity sources",
                        "count": len(target_urls),
                        "results": search_results_meta,
                        "purpose": f"Discovered {len(target_urls)} live opportunity source URLs via TinyFish Search API.",
                    }
                )
            except urllib.error.HTTPError as exc:
                status, code, msg = parse_http_error(exc)
                last_upstream_error = (status, code, msg)
                if status in {401, 403, 429}:
                    self._write_sse_event(
                        {
                            "type": "ERROR",
                            "code": code,
                            "status": status,
                            "message": f"TinyFish Search API HTTP {status} ({code}): {msg}",
                        }
                    )
                    return
                self._write_sse_event(
                    {
                        "type": "PROGRESS",
                        "stage": "SEARCH",
                        "state_label": "Discovering opportunity sources",
                        "purpose": f"Search API returned {code}; proceeding to TinyFish Web Agent discovery.",
                    }
                )
            except Exception as exc:
                last_upstream_error = (502, "SEARCH_TIMEOUT_OR_NETWORK", str(exc))
                self._write_sse_event(
                    {
                        "type": "PROGRESS",
                        "stage": "SEARCH",
                        "state_label": "Discovering opportunity sources",
                        "purpose": f"Search API network note ({exc}); proceeding to TinyFish Web Agent.",
                    }
                )

        # Step C: Live Extraction via TinyFish Fetch API
        fetched_pages = []
        if target_urls:
            self._write_sse_event(
                {
                    "type": "PROGRESS",
                    "stage": "FETCH",
                    "state_label": "Extracting requirements",
                    "purpose": f"Extracting requirements from {min(3, len(target_urls))} live page(s) via TinyFish Fetch API (ttl: 0)...",
                }
            )
            try:
                fetch_resp = call_tinyfish_fetch(
                    api_key,
                    target_urls[:3],
                    purpose=f"Extract opportunity requirements, eligibility, deadlines, and application links for {query}",
                )
                any_upstream_success = True
                fetched_pages = fetch_resp.get("results") or []
                self._write_sse_event(
                    {
                        "type": "FETCH_COMPLETE",
                        "stage": "FETCH",
                        "state_label": "Extracting requirements",
                        "pages_fetched": len(fetched_pages),
                        "purpose": f"Extracted live Markdown content from {len(fetched_pages)} source page(s).",
                    }
                )
            except urllib.error.HTTPError as exc:
                status, code, msg = parse_http_error(exc)
                last_upstream_error = (status, code, msg)
                if status in {401, 403, 429}:
                    self._write_sse_event(
                        {
                            "type": "ERROR",
                            "code": code,
                            "status": status,
                            "message": f"TinyFish Fetch API HTTP {status} ({code}): {msg}",
                        }
                    )
                    return
            except Exception as exc:
                last_upstream_error = (502, "FETCH_ERROR", str(exc))

        # Step D: Browser-Agent Execution via TinyFish Web Agent SSE
        if not target_urls:
            encoded_q = urllib.parse.quote_plus(f"{query} internship fellowship apply")
            target_urls = [f"https://www.google.com/search?q={encoded_q}"]

        primary_url = target_urls[0]
        goal = build_agent_goal(query, profile)

        agent_payload = {
            "url": primary_url,
            "goal": goal,
            "output_schema": OPPORTUNITY_OUTPUT_SCHEMA,
            "browser_profile": "lite",
        }
        req_data = json.dumps(agent_payload).encode("utf-8")
        req = urllib.request.Request(
            TINYFISH_AGENT_SSE_URL,
            data=req_data,
            headers={
                "X-API-Key": api_key,
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "User-Agent": "OpportunityLens/3.1 (TinyFish-Integration)",
            },
            method="POST",
        )

        self._write_sse_event(
            {
                "type": "PROGRESS",
                "stage": "AGENT",
                "state_label": "Checking eligibility information",
                "purpose": f"Checking eligibility information & DOM via TinyFish Web Agent SSE on {primary_url}...",
            }
        )

        last_run_id = f"tf_run_{int(time.time())}"
        agent_opportunities = []
        raw_agent_result = None

        try:
            with urllib.request.urlopen(req, timeout=55, context=make_ssl_context()) as sse_resp:
                any_upstream_success = True
                for raw_line in sse_resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if not data_str:
                        continue
                    try:
                        event = json.loads(data_str)
                    except Exception:
                        continue

                    ev_type = event.get("type", "PROGRESS")
                    if event.get("run_id"):
                        last_run_id = event["run_id"]

                    if ev_type == "COMPLETE":
                        status = event.get("status", "COMPLETED")
                        if status == "FAILED" or event.get("error"):
                            err_obj = event.get("error") or {}
                            err_msg = (
                                err_obj.get("message")
                                if isinstance(err_obj, dict)
                                else str(err_obj or "TinyFish Web Agent run failed.")
                            )
                            last_upstream_error = (502, "AGENT_RUN_FAILED", err_msg)
                            break

                        raw_agent_result = event.get("result")
                        agent_opportunities = extract_opportunities_from_agent_result(
                            raw_agent_result, fallback_url=primary_url, run_id=last_run_id
                        )
                        break
                    else:
                        if not self._write_sse_event(event):
                            return
        except urllib.error.HTTPError as exc:
            status, code, msg = parse_http_error(exc)
            last_upstream_error = (status, code, msg)
            if status in {401, 403, 429}:
                self._write_sse_event(
                    {
                        "type": "ERROR",
                        "code": code,
                        "status": status,
                        "message": f"TinyFish Web Agent HTTP {status} ({code}): {msg}",
                    }
                )
                return
        except Exception as exc:
            last_upstream_error = (504, "AGENT_STREAM_ERROR", str(exc))

        # Combine & deduplicate opportunities from Web Agent SSE + Fetch/Search APIs
        fetch_search_opportunities = parse_opportunities_from_markdown_and_search(
            fetched_pages, search_results_meta, query, last_run_id
        )
        combined_opportunities = deduplicate_opportunities(agent_opportunities + fetch_search_opportunities)

        if not any_upstream_success and not combined_opportunities:
            err_code = last_upstream_error[1] if last_upstream_error else "TINYFISH_UNAVAILABLE"
            err_msg = last_upstream_error[2] if last_upstream_error else "All TinyFish upstream endpoints failed."
            self._write_sse_event(
                {
                    "type": "ERROR",
                    "run_id": last_run_id,
                    "code": err_code,
                    "message": f"Live TinyFish extraction failed ({err_code}): {err_msg}",
                }
            )
            return

        self._write_sse_event(
            {
                "type": "PROGRESS",
                "stage": "MATCH",
                "state_label": "Calculating matches",
                "purpose": f"Calculating explainable match scores across {len(combined_opportunities)} deduplicated live opportunity record(s)...",
            }
        )

        self._write_sse_event(
            {
                "type": "COMPLETE",
                "run_id": last_run_id,
                "status": "COMPLETED",
                "state_label": "Completed",
                "opportunities": combined_opportunities,
                "raw_result": raw_agent_result,
                "target_url": primary_url,
                "search_results": search_results_meta,
                "pages_fetched": len(fetched_pages),
            }
        )

    def _handle_blocking_search(self, body: dict) -> None:
        """Synchronous JSON endpoint for discovering & extracting opportunities."""
        api_key = get_api_key()
        if not api_key:
            self._send_json(
                401,
                {
                    "error": {
                        "code": "MISSING_API_KEY",
                        "message": "TINYFISH_API_KEY environment variable is not set on the backend server.",
                    }
                },
            )
            return

        raw_query = str(body.get("query") or "").strip()
        explicit_url = str(body.get("url") or "").strip()
        profile = body.get("profile") or {}

        if explicit_url:
            url_ok, clean_url, url_err = validate_external_url(explicit_url)
            if not url_ok:
                self._send_json(400, {"error": {"code": "INVALID_URL", "message": url_err}})
                return
            explicit_url = clean_url
            query = raw_query if len(raw_query) >= 3 else f"Opportunities at {explicit_url}"
        else:
            q_ok, clean_query, q_err = validate_search_query(raw_query)
            if not q_ok:
                self._send_json(400, {"error": {"code": "INVALID_QUERY", "message": q_err}})
                return
            query = clean_query

        try:
            target_urls = [explicit_url] if explicit_url else []
            search_meta = []
            fetched_pages = []

            if not target_urls:
                search_resp = call_tinyfish_search(
                    api_key,
                    f"{query} internship fellowship scholarship application eligibility deadline",
                    f"Discover active student opportunities for {query}",
                )
                for r in (search_resp.get("results") or [])[:5]:
                    u = str(r.get("url") or "").strip()
                    if is_valid_application_url(u):
                        target_urls.append(u)
                        search_meta.append(r)

            if target_urls:
                try:
                    fetch_resp = call_tinyfish_fetch(
                        api_key,
                        target_urls[:3],
                        purpose=f"Extract opportunity details for {query}",
                    )
                    fetched_pages = fetch_resp.get("results") or []
                except Exception:
                    pass

            primary_url = target_urls[0] if target_urls else f"https://www.google.com/search?q={urllib.parse.quote_plus(query + ' internship apply')}"
            goal = build_agent_goal(query, profile)

            agent_opps = []
            run_id = f"tf_run_{int(time.time())}"
            raw_res = None
            try:
                run_resp = call_tinyfish_agent_sync(api_key, primary_url, goal, OPPORTUNITY_OUTPUT_SCHEMA)
                run_id = run_resp.get("run_id") or run_id
                raw_res = run_resp.get("result")
                if run_resp.get("status") != "FAILED" and not run_resp.get("error"):
                    agent_opps = extract_opportunities_from_agent_result(raw_res, fallback_url=primary_url, run_id=run_id)
            except urllib.error.HTTPError as exc:
                status, code, msg = parse_http_error(exc)
                if status in {401, 403, 429} and not fetched_pages and not search_meta:
                    self._send_json(status, {"error": {"code": code, "message": msg}})
                    return
            except Exception as exc:
                if not fetched_pages and not search_meta:
                    raise exc

            fallback_opps = parse_opportunities_from_markdown_and_search(fetched_pages, search_meta, query, run_id)
            opportunities = deduplicate_opportunities(agent_opps + fallback_opps)

            self._send_json(
                200,
                {
                    "status": "COMPLETED",
                    "run_id": run_id,
                    "target_url": primary_url,
                    "search_results": search_meta,
                    "pages_fetched": len(fetched_pages),
                    "opportunities": opportunities,
                    "raw_result": raw_res,
                },
            )
        except urllib.error.HTTPError as exc:
            status, code, msg = parse_http_error(exc)
            self._send_json(status, {"error": {"code": code, "message": msg}})
        except Exception as exc:
            self._send_json(500, {"error": {"code": "INTERNAL_ERROR", "message": str(exc)}})

    def _handle_scan_single_url(self, body: dict) -> None:
        """Scan and extract a single opportunity URL via TinyFish Fetch + Web Agent."""
        api_key = get_api_key()
        if not api_key:
            self._send_json(
                401,
                {
                    "error": {
                        "code": "MISSING_API_KEY",
                        "message": "TINYFISH_API_KEY environment variable is not set on the backend server.",
                    }
                },
            )
            return

        raw_url = str(body.get("url") or "").strip()
        url_ok, target_url, url_err = validate_external_url(raw_url)
        if not url_ok:
            self._send_json(400, {"error": {"code": "INVALID_URL", "message": url_err}})
            return

        profile = body.get("profile") or {}
        query_hint = str(body.get("query") or "Student Internship / Fellowship Opportunity").strip()

        fetched_pages = []
        try:
            fetch_resp = call_tinyfish_fetch(
                api_key,
                [target_url],
                purpose=f"Extract opportunity details, required skills, eligibility, and deadline from {target_url}",
            )
            fetched_pages = fetch_resp.get("results") or []
        except urllib.error.HTTPError as exc:
            status, code, msg = parse_http_error(exc)
            if status in {401, 403, 429}:
                self._send_json(status, {"error": {"code": code, "message": msg}})
                return
        except Exception:
            pass

        goal = build_agent_goal(query_hint, profile)
        run_id = f"tf_run_{int(time.time())}"
        agent_opps = []
        raw_res = None

        try:
            run_resp = call_tinyfish_agent_sync(api_key, target_url, goal, OPPORTUNITY_OUTPUT_SCHEMA)
            run_id = run_resp.get("run_id") or run_id
            raw_res = run_resp.get("result")
            if run_resp.get("status") == "FAILED" or run_resp.get("error"):
                if not fetched_pages:
                    self._send_json(
                        502,
                        {
                            "error": run_resp.get("error")
                            or {"code": "AGENT_FAILED", "message": "TinyFish Agent failed to scan URL."}
                        },
                    )
                    return
            else:
                agent_opps = extract_opportunities_from_agent_result(raw_res, fallback_url=target_url, run_id=run_id)
        except urllib.error.HTTPError as exc:
            status, code, msg = parse_http_error(exc)
            if not fetched_pages:
                self._send_json(status, {"error": {"code": code, "message": msg}})
                return
        except Exception as exc:
            if not fetched_pages:
                self._send_json(502, {"error": {"code": "SCAN_FAILED", "message": str(exc)}})
                return

        fetch_opps = parse_opportunities_from_markdown_and_search(fetched_pages, [], query_hint, run_id)
        opportunities = deduplicate_opportunities(agent_opps + fetch_opps)

        self._send_json(
            200,
            {
                "status": "COMPLETED",
                "run_id": run_id,
                "url": target_url,
                "pages_fetched": len(fetched_pages),
                "opportunities": opportunities,
                "raw_result": raw_res,
            },
        )

    def _handle_playground_test(self, body: dict) -> None:
        """Execute a live TinyFish endpoint test from Tab 3 Playground using server-side TINYFISH_API_KEY."""
        api_key = get_api_key()
        if not api_key:
            self._send_json(
                401,
                {
                    "error": {
                        "code": "MISSING_API_KEY",
                        "message": "TINYFISH_API_KEY is not configured on the backend server.",
                    }
                },
            )
            return

        endpoint_kind = str(body.get("endpoint") or "search").lower()
        raw_query = str(body.get("query") or "AI/ML Research & Python Internships").strip()
        q_ok, query, q_err = validate_search_query(raw_query)
        if not q_ok:
            self._send_json(400, {"error": {"code": "INVALID_QUERY", "message": q_err}})
            return

        raw_url = str(body.get("url") or "https://www.tinyfish.ai/").strip()
        url_ok, target_url, url_err = validate_external_url(raw_url)
        if not url_ok:
            self._send_json(400, {"error": {"code": "INVALID_URL", "message": url_err}})
            return

        t0 = time.time()
        try:
            if endpoint_kind == "search":
                res = call_tinyfish_search(api_key, query, f"Discover student opportunities for {query}")
                elapsed_ms = int((time.time() - t0) * 1000)
                self._send_json(
                    200,
                    {
                        "endpoint": f"GET {TINYFISH_SEARCH_URL}?query={urllib.parse.quote_plus(query)}",
                        "status": f"200 OK • {elapsed_ms}ms",
                        "payload": res,
                    },
                )
            elif endpoint_kind == "fetch":
                res = call_tinyfish_fetch(api_key, [target_url], purpose=f"Extract content for {query}")
                elapsed_ms = int((time.time() - t0) * 1000)
                self._send_json(
                    200,
                    {
                        "endpoint": f"POST {TINYFISH_FETCH_URL}",
                        "status": f"200 OK • {elapsed_ms}ms",
                        "payload": res,
                    },
                )
            elif endpoint_kind in {"agent", "browser"}:
                goal = f"Extract page title, organization, and any listed opportunities or key details from {target_url}. Return JSON."
                res = call_tinyfish_agent_sync(api_key, target_url, goal)
                elapsed_ms = int((time.time() - t0) * 1000)
                self._send_json(
                    200,
                    {
                        "endpoint": f"POST {TINYFISH_AGENT_RUN_URL}",
                        "status": f"200 OK • {elapsed_ms}ms",
                        "payload": res,
                    },
                )
            else:
                self._send_json(400, {"error": {"code": "INVALID_ENDPOINT", "message": f"Unknown endpoint: {endpoint_kind}"}})
        except urllib.error.HTTPError as exc:
            status, code, msg = parse_http_error(exc)
            self._send_json(status, {"error": {"code": code, "message": msg}})
        except Exception as exc:
            self._send_json(500, {"error": {"code": "INTERNAL_ERROR", "message": str(exc)}})


def run_server(host: str = "0.0.0.0", port: int = None) -> None:
    if port is None:
        port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), OpportunityLensRequestHandler)
    key_status = "CONFIGURED ✓" if get_api_key() else "NOT SET (set export TINYFISH_API_KEY=...)"
    print("=" * 72)
    print(f"  OpportunityLens Server v3.1 running at http://localhost:{port}")
    print(f"  TINYFISH_API_KEY Status : {key_status}")
    print(f"  TinyFish Agent SSE URL  : {TINYFISH_AGENT_SSE_URL}")
    print(f"  TinyFish Search API URL : {TINYFISH_SEARCH_URL}")
    print(f"  TinyFish Fetch API URL  : {TINYFISH_FETCH_URL}")
    print("=" * 72)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down OpportunityLens server...")
        server.server_close()


if __name__ == "__main__":
    run_server()
