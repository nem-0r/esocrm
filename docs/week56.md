# Week 56 — progress report

**Project:** a CRM for a company selling esoteric services. Managers chat with clients from personal Telegram accounts, directly inside the CRM (MTProto, not a bot). Backend: Python / FastAPI. Frontend: React / TypeScript. Everything runs in Docker and deploys through GitHub CI.

**Status: all three items are done and in production.** (Items 1b, 2 and 3 were shipped on 10 Sept; item 1a was started on 10 Sept and extended on 1–2 Oct.)

---

## 1a. The Telegram gateway sizes itself

**Problem.** The gateway (the service that keeps every Telegram account connected) used fixed numbers. Upgrading the server, or connecting 10+ accounts, meant editing config by hand.

**What we did.** At start-up the gateway now reads the server's CPU cores and memory and decides by itself:
- how many worker processes to run (cores minus one, capped by available memory);
- how many accounts each process takes — accounts are shared fairly, so one process never ends up with all of them;
- the same for the API (number of workers) and the database connection pool.

On 2 Oct we added a "governor": it slows down heavy background work (downloading chat history and files) when disk or memory is tight, and shows the admin a warning banner. A script also recalculates memory limits and database settings after a server upgrade.

**Where.** `backend/app/gateway/topology.py`, `lease.py`, `governor.py`; `backend/app/core/sizing.py`; `deploy/resource_plan.py`; design in `docs/13` and `docs/17`.

**Checked by.** Unit tests (`test_scaling.py`, `test_governor.py`), a lease-distribution test, and a read-only check on the live server after release.

## 1b. "Off by one day" statistics

**Problem.** "Today" was calculated from the server's UTC clock. A sale at 23:25 Moscow time (20:25 UTC) could land on the wrong day — or in the wrong month at a month boundary — so daily numbers disagreed with what managers saw.

**What we did.** Every period boundary (day, month, quarter, exports) is now computed in the organization's configured timezone, with the end of a range defined as the start of the *next* day so the last day is never lost.

**Where.** `backend/app/services/worktime.py` (`day_bounds`), `stats_service.py`, `api/v1/stats.py`. Tests: `verify_tz.py`, `verify_analytics.py`, `verify_exports.py`.

## 2. Editing a sent message

**Problem.** Managers could only write new messages, not fix a typo in one already sent.

**What we did.** A manager can edit the text of a sent message. The CRM updates its own copy and sends an "edit" command to the gateway, which edits the real message in Telegram, so the client sees the correction too. The same permission rules apply on the backend and in the UI.

**Where.** API: `PATCH /conversations/{id}/messages/{id}` (`api/v1/messages.py`, `message_service.py`). Gateway: `handlers.py`, `mtproto_provider.py`. UI: `MessageBubble.tsx` (inline editing), `queries.ts`.

## 3. Mobile layout polish

**Problem.** On phones, buttons and controls sat against the screen edge or under the system bar (chat and deal screens).

**What we did.** Added safe-area padding for the bottom bars and re-laid out the deal card's action area; small fixes to the accounts page and pop-up sheets.

**Where.** `DealCardPage.tsx`, `AccountsPage.tsx`, `shared/ui/overlays.tsx`, `styles/index.css`. Checked in a real Chrome at phone and desktop widths.

---

## One-sentence summary for the video

*The system now adapts to its server on its own, its statistics follow the company's local clock, managers can correct sent messages, and the phone layout no longer crowds the screen edge.*
