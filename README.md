# Coforce Talent Platform

Residency tracking, skills, bench-to-demand matching and training planning for **Coforce**, a fictional
IT services company (10,000 employees, 9 IBUs). All people, clients and projects are synthetic.

| Stage | Scope | Status |
|---|---|---|
| 1 | Skill taxonomy + synthetic data generator | ✅ |
| 2 | FastAPI backend (auth, roles, residency engine, profiles, talent search) + React: dashboard, explorer, profiles | ✅ |
| 3 | Matching engine, demands (form + live supply check), match pipeline, release approvals | ✅ |
| 4 | Demand assistant chatbot (Gemini + LangGraph, rule-based fallback) alongside the form | ✅ |
| 5 | Training pipeline planner: demand plans, HR approval of over-provisioning, batch calendar, future-need batches, onboarding | ✅ |
| 6 | Upskilling advisor: per-person recommendations, learning paths, self-enrolment, bench-wide view | ✅ |
| 7 | Nightly jobs, System page, one-command scripts, Docker, CI | ✅ |

## Layout
```
coforce/
├── scripts/                 # setup / dev / test / reset-data / build (.ps1 + .sh)
├── Dockerfile, docker-compose.yml, docker/entrypoint.sh
├── .github/workflows/ci.yml
├── data_generator/          # Stage 1 — python -m data_generator.generate
├── data/                    # generated: csv/, coforce.db, data_report.md
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI app, CORS, error mapping, startup migration + residency refresh
│   │   ├── config.py        # env-driven settings (DB path, JWT, demo mode, as-of date)
│   │   ├── db.py            # sqlite3 helpers (swap for Postgres later)
│   │   ├── migrations.py    # idempotent column/index additions
│   │   ├── deps.py          # DB-per-request, current user, role guards
│   │   ├── schemas.py       # request bodies
│   │   ├── routers/         # auth, employees (+ /me), residency, reference (skills, meta, certifications)
│   │   └── services/        # framework-free business logic (unit-tested)
│   │       ├── residency.py # gaps-and-islands residency engine → residency_snapshot
│   │       ├── employees.py # search, 360 profile, self-service skills/certs, HR review
│   │       ├── skills.py    # normaliser: exact → synonym → fuzzy
│   │       └── auth.py      # PBKDF2 passwords, JWT
│   ├── tests/               # test_services.py (logic) + test_api.py (HTTP, needs fastapi+httpx)
│   └── requirements.txt
└── frontend/                # React + Vite (no UI library)
    └── src/ pages/ components/
```

## Run it

**Prerequisites:** Python 3.10+ (3.11/3.12 recommended) and Node.js 18+.

### Quick start with the scripts
| | Windows (PowerShell) | macOS / Linux |
|---|---|---|
| One-time setup (venv, packages, data, `backend/.env`) | `.\scripts\setup.ps1` | `sh scripts/setup.sh` |
| Run (backend :8000 + frontend :5173) | `.\scripts\dev.ps1` | `sh scripts/dev.sh` |
| Tests | `.\scripts\test.ps1` | `sh scripts/test.sh` |
| Regenerate clean data (stop the backend first) | `.\scripts\reset-data.ps1` | `sh scripts/reset-data.sh` |
| Production-style: build the UI, serve everything on :8000 | `.\scripts\build.ps1` | `cd frontend && npm run build` then start uvicorn |

If PowerShell refuses to run scripts: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` (once).
Open http://localhost:5173 — the login page lists a demo account per role (password `Coforce@123`).

### Manually
```bash
python -m venv .venv && .venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r backend/requirements.txt
python -m data_generator.generate --out data              # only if data/coforce.db is missing
cd backend && uvicorn app.main:app --reload --port 8000   # terminal 1  → http://localhost:8000/docs
cd frontend && npm install && npm run dev                 # terminal 2  → http://localhost:5173
cd backend && pytest                                      # tests
```

### Docker
```bash
docker compose up --build          # → http://localhost:8000 (UI + API in one container)
```
The database lives in `./data` (generated on first start if missing). Pass `GEMINI_API_KEY` via your shell or a
`.env` file next to `docker-compose.yml`.

### Nightly job
Runs inside the backend every day at `COFORCE_NIGHTLY_AT` (default `02:00`, `off` to disable), or on demand from the
**System** page (manager/HR) or `python -m app.jobs nightly` (from `backend/`). Steps: roll training forward by date
(completed courses add/raise skills: Beginner→2, Intermediate→3, Advanced→4) → recompute residency → re-run matching
for open demands → refresh upskilling recommendations. Every run is logged (`job_runs`). Because the synthetic data is
dated **2026-11-25**, the clock stays there unless you set `COFORCE_AS_OF=` (empty) in `backend/.env` to use today's date.

### CI
`.github/workflows/ci.yml` (GitHub Actions): generates the data and runs the backend tests, builds the frontend, then
builds the Docker image.

### Configuration (`backend/.env`, see `.env.example`)
`GEMINI_API_KEY`, `GEMINI_MODEL`, `COFORCE_JWT_SECRET`, `COFORCE_AS_OF`, `COFORCE_NIGHTLY_AT`, `COFORCE_DB`,
`COFORCE_DEMO_MODE`, `COFORCE_USE_LANGGRAPH`, `COFORCE_LOG_LEVEL`.

## Roles
| Role | Who | Can |
|---|---|---|
| employee | A1–A3 delivery staff | view own profile, add skills/tools, upload certifications, see upskilling recommendations and start learning |
| lead | B1–B2 | + residency dashboard, talent explorer, every profile, raise demands (assistant or form) |
| manager | C1+ | + recompute residency, approve releases (Stage 3) |
| hr | HR / TA / RMG | + verify certifications, approve training plans and the over-provisioning factor |

## Residency rules
- Stints on the same project/account separated by ≤ 30 days are continuous.
- **Rotation due** when current account residency > the account's threshold (24 months).
- Allocated people stay visible for demands but carry **"Approval needed"** — their release needs PM/manager sign-off.
- Cool-off: after leaving an account, an employee can't be redeployed there for `cool_off_months` (6).
- The data is generated as of **2026-11-25**; `COFORCE_AS_OF=""` switches the engine to the real date.

## Matching engine (Stage 3) — `backend/app/services/matching.py`
1. **Candidate pool**: bench + in-training + allocated people rolling off before *needed-by + 30 days* + allocated people past
   the 24-month rotation threshold (optionally: every allocated person).
2. **Hard filters**: grade within the band (one grade below allowed as a stretch) · location in the demand's list, or remote
   allowed, or open to relocation · not in cool-off for the demand's account · not already on the project ·
   ≥ 50% weighted mandatory-skill coverage.
3. **Score (0–100)**: mandatory 40 · good-to-have 15 · experience 10 · role family 10 · context (same IBU, domain skills,
   same project type) 10 · availability vs needed-by 10 · location 5 · up to +5 bench priority (cost of bench) ·
   −4 per missing / −2 per under-level mandatory skill.
   Skill credit = level vs required × recency (≤2 y: 1, ≤4 y: 0.85, older 0.7) × verified (1 / 0.85); a missing skill
   gets ≤ 0.45 partial credit through the skill graph (e.g. PySpark → Databricks).
4. **Gap plan**: each unmet mandatory skill → course from the catalogue + training days → `ready_by`; flags whether the
   person can be ready before needed-by.
5. **Pipeline**: suggested → shortlisted → nominated (bench) / release requested → release approved (allocated: the
   project manager, or any manager of that IBU, decides) → selected. Re-running matching keeps pipeline statuses. When
   selections reach the number of positions, the demand becomes *Fulfilled – Internal*. Every action is logged in `match_events`.
6. **Supply check** (`POST /api/demands/supply-check`): eligible / fully-matching / trainable counts plus, for every
   mandatory skill, how many more people would fully match if it were good-to-have — used live by the form and by the
   Stage 4 chatbot.

Weights and thresholds are constants at the top of `matching.py`.

## Demand assistant (Stage 4) — `backend/app/services/chat/`
A slot-filling conversation that asks, one question at a time, for everything a demand needs: role & positions →
mandatory skills (+ levels) → good-to-have → experience/grade → account → project → locations & remote → work mode →
start date → duration → shift → client interview → priority → over-provisioning factor. Any message can answer several
questions at once (paste a whole JD), change earlier answers ("location Pune", "make Kafka optional", "remove Docker"),
skip with defaults ("skip"), or start over. From the moment skills, engagement and location are known, every turn runs
the live **supply check** and names the bottleneck skill. "Create demand" creates it through the same service as the
form (`source = chatbot`) and runs matching; "Open in form" carries the draft into the form.

**Brains** — `llm.py`: with `GEMINI_API_KEY` set, Gemini (`GEMINI_MODEL`, default `gemini-3.8-flash`) extracts a
structured JSON patch (schema = `Patch`); its result is merged with the rule-based parser's (Gemini wins on conflicts), and
any Gemini error falls back to rules for that turn. Without a key everything runs on the rule-based parser (`rules.py`).
All values are validated in `apply.py` (skills via the taxonomy, accounts/projects by name, dates not in the past, …),
so the LLM can never write an invalid demand.

**Graph** — `graph.py`: `understand → apply → (cancel | create | check → respond)`. Compiled as a LangGraph `StateGraph`
when `langgraph` is installed; otherwise the same node functions run on a small built-in runner.

**Add your key later**: copy `backend/.env.example` to `backend/.env`, set `GEMINI_API_KEY=…`, restart the backend
(or `POST /api/chat/demand/status/reload` as manager/HR). The badge on the assistant page shows which brain is active.

## Training pipeline planner (Stage 5) — `backend/app/services/training.py`
**Demand plan** (button “Plan training for gaps” on a demand):
1. *Target* = positions × over-provisioning factor (set by the raiser; HR approves or changes it).
2. *Already fit* = people meeting every mandatory skill and available before needed-by.
3. *Trainees* = best near-fit candidates — bench first, then in-training, then rolling off (rotation-due optional) — whose
   gaps total ≤ 30 working days and can finish before **needed-by − interview buffer** (7 days with client interview, else 2).
   Optionally every other suitable bench person ("now and future need").
4. *Schedule*: working days only, 3-day lead time. Each person's gap skills run in prerequisite order; learners free within
   5 working days of each other share a batch; classroom batches hold 6–25 (smaller groups take the self-paced course);
   up-skilling uses the Advanced course. Anyone who would finish late is swapped for the next candidate (3 rounds), and
   any remaining shortfall is reported with advice (hire externally / later date / relax a skill).
5. *Approval*: plans are `pending` until HR approves (HR can't approve their own). Approval books the batches and
   enrollments, stores HR as approver of the factor on the demand and shortlists the trainees in the match pipeline.
   Trainees in an active plan aren't offered to other plans. Cancelling an approved plan cancels its bookings.

**Future needs**: per skill, open need (× factor) + 2 months of recent run-rate vs. people on bench / rolling off at
level 3+; “Plan batch” prepares the bench people closest to that skill (prerequisites, related skills, role family).
**Batch calendar** shows scheduled, running and proposed batches; **Onboarding** tracks new hires' curriculum progress;
employees see their upcoming training on *My profile*.

## Upskilling advisor (Stage 6) — `backend/app/services/upskilling.py`
For every skill a person doesn't yet have at level 3 (taken from open demands, their role family and the skill graph):

| Component | Max | How |
|---|---|---|
| Unlock | 35 | re-runs the **matching engine** on every open demand the person passes grade/location/cool-off for, with the skill added at level 3: counts new full matches, new eligibility and score gain |
| Market demand | 25 | open positions × over-provisioning factor (mandatory 1, good-to-have 0.4) + 2 months of the last 6 months' run-rate, square-root normalised |
| Adjacency | 20 | strongest prerequisite / related / same-family link to a skill they hold (× their level), or a head start if they're already at level 1–2 |
| Role fit | 15 | core / common / optional skill for their role family (80% for the role they were hired as) |
| Effort | −5 | training days incl. missing prerequisites (30 days = full penalty) |

Each recommendation lists plain-language reasons, the demands it opens up, an ordered **learning path** (missing
prerequisites first) with classroom and self-paced options, and open seats in scheduled batches. Skills already covered
as a prerequisite in a higher-ranked path are not repeated. Employees can **start learning** from *My profile*
(self-paced, or join a classroom batch with free seats) — enrolments are created in order with working-day dates.
Managers see the **Upskilling advisor**: the most-recommended skills across the bench (→ “Plan batch” in the training
planner) and every bench person's top 3, longest on bench first. The bench snapshot (`upskill_recs`) takes ~5 s to
compute and is refreshed with *Recompute*.

## Key API endpoints
| Method | Path | Who |
|---|---|---|
| POST | `/api/auth/login` · GET `/api/auth/me` · POST `/api/auth/change-password` | all |
| GET | `/api/residency/summary` · `/heatmap` · `/employees` | lead/manager/hr |
| POST | `/api/residency/refresh` | manager/hr |
| GET | `/api/employees?skills=pyspark,databricks&min_proficiency=3&status=bench` | lead/manager/hr |
| GET | `/api/employees/{emp_id}` | self or lead/manager/hr |
| GET/POST/DELETE | `/api/me/profile` · `/api/me/skills` · `/api/me/certifications` | self |
| GET / POST | `/api/certifications?status=pending` · `/api/certifications/{id}/review` | hr |
| GET | `/api/skills/search?q=` · `/api/skills/normalize?text=` · `/api/meta` · `/api/projects?account_id=` | all |
| GET / POST | `/api/demands` · `/api/demands/supply-check` · `/api/demands/{id}` · PATCH `/api/demands/{id}/status` | lead/manager/hr |
| POST / GET | `/api/demands/{id}/match` · `/api/demands/{id}/matches` · POST `/api/demands/{id}/matches/{emp}/action` | lead/manager/hr |
| GET / POST | `/api/approvals` · `/api/approvals/{demand_id}/{emp_id}` | approver (PM) or IBU manager |
| POST / GET | `/api/training/plans/preview` · `/api/training/plans` · `/api/training/plans/{id}` · POST `…/{id}/decision` (hr) · `…/{id}/cancel` | lead/manager/hr |
| GET | `/api/training/calendar` · `/api/training/forecast` · `/api/training/onboarding` · `/api/training/my` | lead/manager/hr · `my`: all |
| GET / POST | `/api/me/upskilling` · `/api/me/upskilling/{skill_id}/start` · `/api/employees/{id}/upskilling` | self (or lead/manager/hr) |
| GET / POST | `/api/upskilling/bench` · `/api/upskilling/refresh` | lead/manager/hr · refresh: manager/hr |
| GET / POST | `/api/chat/demand/status` · `/api/chat/demand/sessions` · `/api/chat/demand/sessions/{id}` · `/api/chat/demand/sessions/{id}/messages` | lead/manager/hr |
