# Federation Gridiron — Project Overview

Federation Gridiron is a Django-based American football management simulation intended to begin in **1970** and evolve over seasons. The product vision includes both **web** and **mobile** frontends, so the backend and data model should stay clean, API-friendly, and free of UI-specific coupling.

This document summarizes the **current Django architecture** as implemented in the repository, then outlines the **major components** still needed for a full historical football management simulation.

---

## Vision (high level)

- Simulate a global / federation-style football pyramid with promotion, relegation, and special movement between leagues.
- Start historically in 1970 and advance season by season.
- Support franchise management, competition structure, schedules, game outcomes, and player development over time.
- Keep Django Admin as the early operational surface; later expose the same domain through APIs for web and mobile clients.

---

## Current repository architecture

### Project layout

| Path | Role |
|------|------|
| `federationgridiron/` | Django project package (`settings`, `urls`, `wsgi`, `asgi`) |
| `leagues/` | First domain app — league hierarchy, teams, movement paths |
| `manage.py` | Django management entrypoint |
| `requirements.txt` | Python dependencies |
| `static/` | Collected / static assets (includes Django admin assets) |

There is **no** public site, REST/GraphQL API, or additional domain apps yet. Root URL configuration only exposes Django Admin:

- `GET/POST /admin/` — admin site
- Custom admin route under leagues: `/admin/leagues/league/generate-32-team-league/`

### Stack and runtime

| Concern | Current choice |
|---------|----------------|
| Framework | Django **5.2.x** (see `requirements.txt`) |
| Database | **MySQL** via `mysqlclient`, hosted on PythonAnywhere |
| Config | `python-dotenv` loads env from a host path; DB password from `FG_DB_PASSWORD` |
| Auth | Django’s built-in `django.contrib.auth` (admin staff users only in practice so far) |
| Frontends | None yet; admin UI only |

`INSTALLED_APPS` currently includes Django contrib apps plus `leagues`.

### Design principles already reflected in code

- **Slug-based identity** for leagues, conferences, divisions, and teams (API-friendly stable identifiers).
- **Hierarchical leagues** via optional self-referencing `parent_league`, with validation that preserves a clean path up to a single active tier-1 root.
- **Fixed 32-team league shape** for Federation Gridiron leagues: 2 conferences × 4 divisions × 4 teams.
- **Service-layer separation** for generation logic (`leagues/services/`) kept out of `admin.py`.
- **Admin-first tooling** for structure setup before public/API surfaces exist.

---

## The `leagues` app (what exists today)

### Domain models

#### `League`

- Fields: `name`, `slug` (unique), `tier` (positive integer), optional `parent_league`, optional `description`, `active`, timestamps.
- Hierarchy helpers: `path_to_root()`, `root_league`.
- Validation in `clean()` / `save()` (via `full_clean()`):
  - Tier 1 must have no parent; tier &gt; 1 must have a parent.
  - Parent cannot be self or a descendant.
  - Parent tier must equal `this.tier - 1`.
  - Only **one active tier-1** league may exist.
- Intended long-term shape: one World League at tier 1, with child leagues forming the federation pyramid.

#### `Conference`

- Belongs to a `League`.
- Fields: `name`, `slug`, `sort_order`, `active`, timestamps.
- Unique slug per league.

#### `Division`

- Belongs to a `Conference`.
- Fields: `name`, `slug`, `sort_order`, `active`, timestamps.
- Unique slug per conference.

#### `Team`

- Belongs to a `League`; optional `conference` and `division`.
- Location-ish fields: optional `city`, `region`, `country`.
- Fields: `name`, `slug`, `sort_order`, `active`, timestamps.
- Unique slug per league.

#### `MovementPath`

- Defines **allowed** movement between leagues/teams for future promotion, relegation, lateral, or special moves.
- Fields: `from_league`, optional `from_team`, `to_league`, optional `to_team`, `movement_type`, `active`, optional `notes`, timestamps.
- Movement types: `promotion`, `relegation`, `lateral`, `special`.
- **No performance logic or automatic promotion/relegation yet** — structure only.

### Admin and tooling

- All five models are registered in Django Admin with list displays, search, filters, and ordering.
- League admin uses a custom form so model validation surfaces in the UI.
- **Generate 32-Team League** admin tool:
  - Form (`leagues/forms.py`) validates exactly 2 / 8 / 32 non-empty name lines and unique slugifiable names.
  - Service (`leagues/services/league_generator.py`) upserts conferences, divisions, and teams by slug under a chosen league, preserving `sort_order`.
  - View (`leagues/admin_views.py`) + templates under `leagues/templates/admin/leagues/league/`.

### Explicitly not built yet (by current project direction)

- Scoring / simulation engine
- End-user accounts, ownership, or franchise control
- Schedules / calendars
- Payments
- Public pages
- APIs for web/mobile clients

---

## Architectural snapshot

```text
federationgridiron (project)
└── leagues (app)
    ├── models: League → Conference → Division → Team
    │           League ← MovementPath → League/Team
    ├── admin + custom “Generate 32-Team League” UI
    ├── forms: Generate32TeamLeagueForm
    └── services: generate_league_structure()
```

Data flow today is **admin operator → forms/services → MySQL**. There is no external client API surface yet.

---

## Major components still needed

The following is a recommended component map for a historical football management simulation starting in 1970. Names are conceptual; they may become Django apps, modules, or bounded services as the project grows.

### 1. Time & seasons

Simulation needs an authoritative clock independent of real-world time.

- **Season / Year** entities (starting 1970) and season phases (offseason, draft, preseason, regular season, playoffs, awards).
- Advancement rules: when a season “ticks,” what commits (standings, contracts, age, injuries).
- Historical mode support: ability to seed and replay eras without rewriting domain logic.

### 2. Competition structure (extend `leagues`)

Already started; still needed:

- Season-scoped standings and tiebreakers.
- Playoff brackets / championship definitions per league.
- Activation of `MovementPath` after season end (promotion/relegation execution as records, not only allowed paths).
- Historical continuity: franchise identity vs. name/city changes over eras.

### 3. Scheduling

- Season schedule generation (regular season + playoffs).
- Home/away, bye weeks, travel constraints as the sim grows.
- Rescheduling and completed-game immutability rules.

### 4. Game simulation & scoring

- Match / game entity linking two teams, date/week, venue, and status.
- Play-level or drive-level simulation engine (or box-score abstraction first).
- Box scores, team/player stats aggregation, weather/home-field modifiers later.
- Deterministic seed support for testing and historical reproducibility.

### 5. Players & personnel

- Player identity, positions, attributes appropriate to era.
- Rosters, depth charts, free agency, drafts, trades, retirements.
- Contracts/salary rules if economy is in scope; otherwise defer payroll.
- Aging, development, injury, and retirement pipelines across seasons.

### 6. Coaching & team management

- Coach/GM entities and AI vs. human control modes.
- Strategy settings (offense/defense preferences) that feed the sim.
- Staff hierarchy if the product goes beyond single-franchise control.

### 7. Users, ownership & permissions

- Distinction between staff/admin operators and players/managers.
- Franchise ownership / manager assignment.
- Auth for web and mobile (token/session strategy TBD); keep domain models free of frontend assumptions.

### 8. Economy & facilities (optional later)

- Team budgets, stadiums, upgrades, sponsorships.
- Ticket/revenue models if management depth is a product goal.
- Payments integration only if real-money features are required — not needed for core sim.

### 9. Media, narrative & history

- News, season recaps, awards, records books.
- Timeline views for “what happened in 1970–…” across the federation.
- Branding fields (logos, colors, nicknames) when presentation layers need them.

### 10. APIs & clients

- Stable JSON (or similar) API layer over the same domain models.
- Web app and mobile app consuming that API.
- Admin remains the ops/content tool; clients remain the player-facing product.

### 11. Ops, data & quality

- Seed/import tooling for 1970 historical starting points.
- Background jobs for season advancement and bulk simulation.
- Tests for hierarchy validation, generator upserts, and eventually sim determinism.
- Environments: local vs. PythonAnywhere production config hygiene (secrets, `DEBUG`, hosts).

---

## Suggested build order (non-binding)

1. **Stabilize league structure data** (leagues, conferences, divisions, teams, movement paths) — largely underway.
2. **Introduce Season / Week time model** so everything else can hang off a year.
3. **Players + rosters** enough to put names on a depth chart.
4. **Schedule + match entities** without a deep engine (manual or stub results first).
5. **Simulation engine** that produces box scores and standings.
6. **Promotion/relegation execution** using existing `MovementPath` definitions.
7. **User ownership / manager control**.
8. **Public API + web/mobile clients**.
9. Economy, narrative, and branding as product depth allows.

---

## Out of scope for immediate next steps (as previously constrained)

Unless priorities change, do **not** jump ahead into scoring engines, end-user accounts, schedules, payments, public pages, or APIs until the domain foundation for time, personnel, and competition state is clearer. The current codebase correctly prioritizes **league structure + admin tooling**.

---

## Document status

- Reflects repository state as of the `docs/project-overview` documentation pass.
- Describes existing code only in the “Current repository architecture” and “`leagues` app” sections.
- Future components are planning guidance, not implemented features.
