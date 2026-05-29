- [x] Add `browser.is_logged_in()` guard before each dial attempt in `src/main.py`; stop with `Google Voice login required.`
- [ ] Add Groq API retry/backoff and safe fallback in `src/ai_groq.py` (429/rate limits: retry 3 times with delays 2s, 5s, 10s; then return fallback text instead of crashing/silence).



