# OpportunityLens — Official TinyFish Web Agent Integration 🐟

> **Submission for KRITICAL HACKS '26 (AssetMerkle IGDTUW)**  
> **Domain:** Open Innovation (AI/ML, Web3 & Autonomous Agent Systems)  
> **Sponsor Integration:** TinyFish Web Intelligence (`Search API`, `Fetch API`, `Web Agent SSE`)


---
Websit: https://opportunitylens-k3wq.vercel.app/

## 🌟 Problem & Solution
Students waste hours browsing fragmented job boards, dead links, and unverified listings without knowing how well their current skill stack matches real market requirements.

**OpportunityLens** transforms any natural-language query (*Web3 Smart Contract Auditing*, *AI/ML Research*, *Full-Stack TypeScript*, *Cybersecurity*, *Data Science*, or any custom role) into a ranked, verified feed with:
1. **Dynamic Multi-Domain Discovery:** Real-time matching across AI/ML, Web3/Solidity, Full-Stack, Cybersecurity, Data Science, and custom live searches.
2. **Interactive Skill-Stack Calculation:** Add or remove skills in real time to recalculate AI Match % and identify missing skill gaps.
3. **Verified Direct Portal Links:** Every **"Apply Direct ↗"** button opens the official company career or grant portal (Ethereum Foundation, OpenZeppelin, Hugging Face, Sarvam AI, NVIDIA, Vercel, Razorpay, Polygon Labs, etc.) in a new tab.
4. **Live URL Scanner & Tailored Cold Pitch Generator:** Scan any custom URL via TinyFish Fetch and generate tailored outreach emails.

---

## 🛠️ TinyFish API Integration
- **TinyFish Search API (`GET https://api.search.tinyfish.ai`):** Discovers live career portals and research labs.
- **TinyFish Fetch API (`POST https://api.fetch.tinyfish.ai`):** Extracts clean markdown and skill requirements from target URLs.
- **TinyFish Web Agent SSE (`POST https://agent.tinyfish.ai/v1/automation/run-sse`):** Streams live verification steps to the UI terminal.

---

## 🚀 Quick Start
- **Static / Instant:** Open `index.html` in any browser or deploy directly to Vercel with `Application Preset: Other`.
- **Python Backend:** `python3 server.py` (opens on `http://localhost:8000`)
- **Node.js Backend:** `node server.js` (opens on `http://localhost:8000`)
