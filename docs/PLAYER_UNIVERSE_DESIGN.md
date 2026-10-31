# Federation Gridiron — Shared Fictional Player Universe

This document proposes how Federation Gridiron gives every user the same fictional 1970 players and the same annual college draft classes, while each user's league keeps its own careers: team assignments, statistics, injuries, and development.

It is a design proposal. It does not add Django models, migrations, services, settings, or admin screens.

The football people in this system are fictional. Era profiles borrow the broad shape of professional football history (roster size, run/pass emphasis, draft length, body types). They are not a real-league roster dump and they do not use real player identities.

---

## Current architecture

The repository today is a Django 5.2 project (`requirements.txt`) with one domain app, `leagues`. Public URLs expose Django Admin only. There is no player, season, roster, or end-user save model. `leagues/views.py` is empty.

### What the league catalog already is

| Model | Role today |
|-------|------------|
| `League` | Tiered federation node. `slug` is globally unique. `parent_league` builds a pyramid. `clean()` allows only one active tier-1 league. |
| `Conference` | Child of a league. Unique `(league, slug)`. |
| `Division` | Child of a conference. Unique `(conference, slug)`. |
| `Team` | Franchise inside a league, optionally placed in a conference and division. Unique `(league, slug)`. Optional `city`, `region`, `country`. |
| `MovementPath` | Allowed promotion, relegation, lateral, or special move between leagues and optional teams. Paths are definitions only; nothing executes them. |

`leagues/services/league_generator.py` upserts a fixed shape onto one league: 2 conferences, 8 divisions, 32 teams. Identity is the slug. Re-running the generator updates the same rows.

That catalog is shared operator data. A `Team` row is a franchise identity in the federation, not one user's copy of a club. `League.active` and the single tier-1 rule are catalog rules. A user's playthrough must not create `League` rows and must not move `Team.league`, `Team.conference`, or `Team.division`, because those fields are visible to every campaign that points at the catalog.

Auth is Django's built-in user model. No `AUTH_USER_MODEL` override is set. The database is MySQL through `mysqlclient`. Domain writes go through `full_clean()` on `League` and through small service functions (`generate_league_structure`) that use `@transaction.atomic` and slug natural keys.

### What this design adds on top

Two future apps, neither of which is created by this change:

| App | Owns | Writes |
|-----|------|--------|
| `universe` | The shared fictional population: releases, eras, colleges, biographies, opening assignments, draft classes | A generator command, run once per release |
| `campaigns` | One user's federation playthrough from 1970 onward | Per-user services (`start_campaign`, draft materialization, later the sim) |

`leagues` stays the franchise catalog. Player code reads `Team` and never rewrites the pyramid as a side effect of a user's season.

```text
leagues (exists)                universe (proposed, shared)          campaigns (proposed, per user)
League → Conference             UniverseRelease                      Campaign
         → Division             Era, EraRosterQuota                  CampaignPlayer → UniversePlayer
         → Team ←────────────── OpeningAssignment                    RosterStint → Team
                                DraftClass → DraftProspect           SeasonLine, Injury, CampaignRating
                                UniversePlayer, College
```

---

## Requirements this design meets

| Requirement | Where it lives |
|-------------|----------------|
| Every user's league starts with the same fictional 1970 players | A published **opening cohort** on a `UniverseRelease`. Starting a campaign copies assignment slots into that campaign only. |
| Every user encounters the same annual college draft classes | `DraftClass` / `DraftProspect` rows for each year, shared by every campaign pinned to that release. |
| Career statistics, team assignments, injuries, and development stay independent | Those facts are rows under `Campaign`. They reference the shared person by foreign key. |
| Player characteristics reflect historical football eras | `Era` profiles drive who is generated (body, position mix, skill weights) and, separately, the rules of the season being played. |
| Decades of seasons do not duplicate the master player database per user | One biography row per fictional person per release. A campaign stores a thin overlay, and only for players who have actually entered that campaign. |

"The same" means the same `universe_key` and the same immutable biography, body, potential, college, and opening slot. It does not mean the same mutable career.

---

## Terms

| Term | Meaning |
|------|---------|
| **Universe release** | One immutable, versioned publication of the shared fictional population and its draft classes. |
| **Universe player** | One fictional person inside a release. Biography and ceilings live here. |
| **Opening cohort** | The 1970 professional population already on rosters or in the free-agent pool when a campaign begins. |
| **Draft class** | The fictional college entrants for one future year (1971, 1972, …). |
| **Campaign** | One user's playthrough of the federation, starting in the 1970 preseason. This is the isolation boundary. It is not a `leagues.League` row. |
| **Campaign player** | That person's mutable life inside one campaign: status, current ratings, assignments, stats, injuries. |
| **Catalog team** | An existing `leagues.Team` franchise. Campaigns share the row and keep their own assignment and placement state. |

A user may own more than one campaign. Campaigns do not share mutable player rows with each other.

---

## Sharing rules

1. A fictional person's name, birth year, college, position, height, weight, handedness, era of origin, initial ratings, and potential are stored once, on the universe release.
2. A campaign stores the foreign key to that person plus the facts that diverge: current ratings, roster stints, season lines, injuries, and draft results.
3. Campaign services write campaign tables only. They load universe rows and catalog teams as read-only references.
4. The generator writes universe tables only. It does not create campaigns.
5. Future draft classes remain universe rows until that campaign's clock reaches the class year. Starting a campaign in 1970 does not insert campaign rows for the 1988 class.
6. Publishing a release freezes every universe row that campaigns can cite. A correction is a new release. Existing campaigns stay pinned to the release they started on, so a retune cannot change the 1970 roster or a draft class already in progress.
7. New campaigns use the latest published release. "Everyone has the same players" means everyone on that release.

The current-rating numbers on a campaign player are an overlay, initialized from the shared initial ratings. They are not a second biography. Potential stays on the universe row and is read from there whenever development runs, so two campaigns share a ceiling even after their current ratings have split.

---

## Universe release

`UniverseRelease` is the unit of sameness and the unit of reproducibility.

| Field | Purpose |
|-------|---------|
| `slug` | Stable id, unique. Example: `fg-1970-1`. |
| `generator_version` | Algorithm id, example: `fg-player-gen-1`. |
| `seed` | Integer stored on the release row. Generation does not read it from Django settings. |
| `root_league` | FK to the tier-1 `League` this population was built for. `on_delete=PROTECT`. |
| `catalog_fingerprint` | Hash of the franchise tree at generation time (see below). |
| `through_year` | Last draft class year materialized in the shared tables. Default horizon **2049**. |
| `status` | `draft` or `published`. |
| timestamps | Same `created_at` / `updated_at` pattern as `leagues`. |

`catalog_fingerprint` is the SHA-256 of one sorted line per franchise:

```text
{tier}:{league_slug}:{conference_slug}:{division_slug}:{team_slug}
```

`start_campaign` proceeds when the live tree still hashes to the release fingerprint. A slug rename is a different catalog; existing releases keep describing the catalog they were built against. Editing a team display name while keeping the slug does not change the fingerprint.

While `status` is `draft`, the generator may rebuild that release from `(generator_version, seed, fingerprint)`. After `published`, updates to existing players, quotas, opening slots, and existing draft classes are rejected. Appending a draft class for a year beyond `through_year` is allowed when the generator version and seed are unchanged, because those years are a pure function of the seed and do not rewrite older keys. The append advances `through_year`.

Campaigns use `on_delete=PROTECT` toward the release. Deleting a published population out from under a playthrough is a catalog operation that should fail until those campaigns are gone.

---

## Identity and deterministic generation

Every universe player has a `universe_key`, unique across the table, max length 64:

```text
p-{release_slug}-{cohort}-{year}-{index:05d}
```

Examples:

```text
p-fg-1970-1-opening-1970-00412
p-fg-1970-1-draft-1984-00103
```

`cohort` is `opening` or `draft`. The numeric primary key stays internal (`BigAutoField`, matching `DEFAULT_AUTO_FIELD`). APIs and fixtures cite `universe_key`. That matches the slug identity already used for leagues and teams. The key is opaque: release slugs contain hyphens, so clients look the row up instead of splitting the string.

Attribute draws do not use `random.Random`, whose stream can change across Python versions. Each draw is an HMAC-SHA256 of the release seed and a named key, so one attribute can be rebuilt without reshuffling the rest of the population:

```text
unit = HMAC_SHA256(seed, f"{universe_key}:weight") interpreted as a unit interval
```

The same `(generator_version, seed, catalog fingerprint, era tables)` rebuilds the same keys and the same bodies. The MySQL rows are the queryable copy of that function. Rebuilding an unpublished release replaces that copy in place, keyed by `universe_key`. A fresh environment can republish the same fictional league without hand-copying a database dump.

Display names come from fictional given-name and surname lists shipped with the generator. The stored `name` is the result. `universe_key` is the identity; display names may collide, and the UI disambiguates with position, college, and birth year. The generator never reads a real player registry.

---

## Eras

Two different things are easy to tangle. This design keeps them apart.

| Layer | Question it answers | Changes a player already generated? |
|-------|---------------------|-------------------------------------|
| **Origin era** | What kind of footballer was created for this cohort year? | No. The body, position, and ceilings are stored on the universe player and stay put. |
| **Season era** | What rules and roster limits apply in the year being simulated? | The player's row stays. The sim reads the era for the campaign's current year when it resolves roster limits, schedule length, injury climate, and scheme modifiers. |

A tackle generated in 1972 is still that 1972 body when a campaign reaches 1996. The 1996 season era can change how valuable pass blocking is and how large the roster is. It does not rewrite his height, his potential, or his `universe_key`.

### Era rows

`Era` belongs to a release (or is referenced by it and frozen at publish). Proposed default bands, stored as data so designers can retune them without a code change:

| Key | Years | Active roster | Taxi | Draft rounds | Schedule length | Generation emphasis |
|-----|-------|---------------|------|--------------|-----------------|---------------------|
| `run-first` | 1970–1977 | 40 | 8 | 17 | 14 | Fullbacks on every roster, fewer receivers, run-block ahead of pass-block, passing traits rarer at the top of the distribution, higher injury cost |
| `passing-rules` | 1978–1986 | 45 | 8 | 12 | 16 | Receiver and coverage traits more common; pocket passing gains weight |
| `timing-specialists` | 1987–1994 | 47 | 8 | 8 | 16 | Designated pass rush and nickel-relevant coverage become ordinary; specialists separate from leftovers |
| `cap-and-size` | 1995–2003 | 53 | 8 | 7 | 16 | Heavier linemen, higher athletic thresholds, shorter drafts |
| `spread-influence` | 2004–2013 | 53 | 8 | 7 | 16 | More receiver depth, edge speed ahead of bulk |
| `modern-passing` | 2014–2049 | 53 | 8 | 7 | 16 through 2020, 17 from 2021 | Hybrid skill profiles more common; pass-rate weights stay high |

These numbers are starting defaults for a fictional 32-team tier-1 federation, chosen so the eras are visibly different. They are not a claim that Federation Gridiron replays a real league's calendar. Schedule length and roster limits are season-era inputs for a later sim. The player system stores them so generation and roster validation have one source.

Each era owns a complete position quota for the active roster and another for the taxi squad. The generator refuses a quota that does not sum to that era's roster size. Direction of travel across eras: FB counts fall, WR and CB counts rise, draft rounds shrink, linemen get heavier. Only the 1970 active quota is fixed in this proposal, as the worked example the first generator must satisfy. Later quotas are data filled under the same sum rule before that era's classes are published.

### 1970 active quota (40)

| Position | Count |
|----------|-------|
| QB | 2 |
| RB | 2 |
| FB | 2 |
| WR | 4 |
| TE | 2 |
| OT | 3 |
| OG | 3 |
| C | 2 |
| DT | 4 |
| DE | 3 |
| LB | 6 |
| CB | 3 |
| S | 2 |
| K | 1 |
| P | 1 |

Taxi quota (8): QB 1, RB 1, WR 1, OL 2, DL 1, LB 1, DB 1.

### Ratings

One component vocabulary for every era, scale 0–100, higher is better. Eras change how often high values appear. They do not add a new column per decade, which keeps a 1970 fullback and a 2016 receiver on the same record shape.

| Group | Components |
|-------|------------|
| Physical | `speed`, `acceleration`, `agility`, `strength`, `stamina`, `durability` |
| Mental | `awareness`, `discipline`, `processing` |
| Offense | `throw_power`, `throw_accuracy`, `catching`, `route_running`, `carrying`, `elusiveness`, `run_blocking`, `pass_blocking` |
| Defense | `tackling`, `coverage`, `pass_rush`, `run_defense` |
| Special teams | `kicking`, `punting` |

Physical measurables outside the 0–100 scale: `height_in`, `weight_lb`, `handedness`.

`UniverseRating` stores `initial` and `potential` per component for one universe player. Non-position skills sit near a low baseline rather than being omitted, so development code can stay position-agnostic. Overall rating is computed when displayed. It is not the stored authority.

Origin-era weights (means and spreads by position, stored on the era) are what make a 1970 player look like 1970 football: a common elite runner, a rarer high-volume passer, offensive linemen whose run-block draw sits above their pass-block draw, fullbacks present because the quota requires them. A 2004 class uses the same columns with the later weights, so high `route_running` and `pass_rush` show up more often and the weight distribution for offensive tackles shifts upward.

`growth_curve` is frozen on the universe player at generation: `peak_age`, `development_rate`, `decline_rate`, with small per-player variance already baked in from the HMAC draw. Campaigns share the curve and the ceiling. They differ in snaps, injuries, and current values.

### Season-era rules the player system must already know

Even before a simulation engine exists, campaign roster checks need:

- active and taxi limits for the campaign's current year
- the draft-round count that sizes that year's class
- an injury-climate multiplier and a mix of injury kinds (earlier eras: longer absences; later eras: more graded soft-tissue losses and fewer season-long mysteries)

Scheme and passing modifiers can wait for the sim. The columns for them belong on `Era`, not on the player.

---

## Colleges

`College` is shared reference data inside the release: `slug`, `name`, `region`, and a pipeline tag (`independent`, `state`, `small`, `academy` — fictional labels). A campaign does not own colleges.

`EraCollegeWeight (era, college, weight)` is how often the generator picks that school in that era, so 1970 pipelines and 2010 pipelines can differ while the school list stays stable. Every universe player stores a college FK, including opening-cohort veterans (the school they came from).

College names are fictional, authored for Federation Gridiron, on the order of 48–80 schools. That table is tiny and shared.

---

## The 1970 opening population

The opening cohort is everyone already in professional football in September 1970: rostered players and the free-agent pool. The first class a running campaign drafts is **1971**, after the 1970 season. Rookies already on 1970 rosters are opening-cohort players whose `origin_draft_year` is 1970. They are one row each, in the opening cohort, and campaigns do not draft them again.

### How many

For each catalog team in the tier-1 tree (and any lower-tier teams already attached to that tree):

```text
slots = era.active_quota + era.taxi_quota
```

With only the current 32-team structure present, 1970 is `32 × (40 + 8) = 1,536` rostered players. Add a shared free-agent pool of **160**, position mix drawn with the same era weights. Total opening population: **1,696**. If lower-tier teams exist when the release is generated, they get the same per-team treatment. The generator sizes itself from the fingerprinted tree. The player tables do not hard-code 32; that count comes from the catalog the release was built against.

Each roster slot has a role: `starter`, `backup`, or `taxi`.

| Role | Age draw | Ratings |
|------|----------|---------|
| Starter | Mostly 24–30, a few older | `initial` closer to `potential`, near the peak of the growth curve |
| Backup | Wider, 22–32 | Mid initial, remaining room to the ceiling |
| Taxi | Mostly 21–24 | Lower initial, more remaining potential |
| Free agent | Same spreads, slightly older bias | Below the starter band |

Age is chosen first. `birth_year = 1970 - age` for opening players, plus a `birth_day` of 1–365 from the HMAC draw so later sims can mark in-season birthdays. A 1970 population therefore contains veterans born in the 1930s and 1940s and rookies born around 1948–1949. That spread is what makes the first season a league in progress, with veterans and rookies already on the depth chart.

### Opening assignment

`OpeningAssignment` is the shared template:

| Field | Notes |
|-------|--------|
| `release` | FK |
| `universe_player` | One assignment per opening player. Unique. |
| `team` | FK to `leagues.Team`, null for the free-agent pool. `on_delete=PROTECT`. |
| `position` | Copied from the player so a slot can be constrained without a join. The generator rejects a row whose position disagrees with `UniversePlayer.position`. |
| `role` | `starter`, `backup`, `taxi`, `free_agent` |
| `depth` | Depth-chart index for that position on that team, starting at 1. It runs through starters, backups, and taxi as one list. |

Unique `(release, team, position, depth)`. MySQL unique indexes treat a NULL `team` as distinct on each row, so the free-agent pool (null team) does not collide with itself and does not need a partial index. A franchise cannot be given two players at the same position and depth. Every campaign that starts on this release begins with the same person in the same role on the same franchise. After kickoff, roster stints in the campaign diverge and the template is left untouched.

`UniversePlayer` fields specific to origin:

| Field | Opening cohort | Draft cohort |
|-------|----------------|--------------|
| `entry_route` | `opening_roster` | `college_draft` |
| `entry_year` | 1970 | class year, ≥ 1971 |
| `origin_draft_year` | the fictional class they came from, ≤ 1970 | equal to `entry_year` |
| `era` | the origin era (1970 band) | the era that contains the class year |

Position is a single primary position from the quota list: `QB`, `RB`, `FB`, `WR`, `TE`, `OT`, `OG`, `C`, `DT`, `DE`, `LB`, `CB`, `S`, `K`, `P`. A later season may use a player in a package role (slot, nickel, edge). That role is campaign usage, not a new universe identity.

---

## Annual draft classes

`DraftClass` is unique on `(release, year)` for each year from 1971 through `through_year`.

`DraftProspect`:

| Field | Notes |
|-------|--------|
| `draft_class` | FK |
| `universe_player` | Unique. A person belongs to one cohort only. |
| `board_rank` | Shared default ordering, 1…N. This is a consensus board, not a destination. |
| `pool` | `draftable` or `undrafted` |

Class size for year Y:

```text
draftable = era(Y).draft_rounds × tier1_team_count
undrafted = 64
```

For the 1970–1977 defaults and 32 teams, each class is `17 × 32 + 64 = 608` prospects. From 1995 onward the default is `7 × 32 + 64 = 288`. The prospect rows exist once. Their measurables, college, position, and ratings were drawn with that year's origin era, so a 1974 class and a 2014 class do not look like the same athletes with different birthdays.

A prospect does not store a team. Landing spot, round actually taken, and whether they go undrafted are campaign facts (`CampaignDraftPick`). Two users face the same board and leave with different classes because their standings, trades of picks, and choices differ.

True ratings stay on the universe player. A `CampaignScoutReport` (campaign, prospect, component, revealed grade, scouted_on) is the per-user fog of war. Putting a revealed grade on `DraftProspect` would leak one user's scouting into every other campaign. Scout reports can follow the first playable draft; the split is part of the model now so the shared class stays a record of who exists, not of what any one user has learned.

When a campaign enters the draft phase of year Y, `materialize_draft_class` inserts campaign players for that class only. The operation is idempotent on `(campaign, universe_player)`. Prospects stay `prospect` until that campaign drafts or signs them. Undrafted prospects become free agents in that campaign only.

---

## Campaign state

### `Campaign`

| Field | Notes |
|-------|--------|
| `owner` | FK to `settings.AUTH_USER_MODEL`. Required. `on_delete` cascades the playthrough. |
| `release` | FK, `PROTECT`. |
| `root_league` | The tier-1 catalog league. Must match the release. |
| `name` | User-facing save name. |
| `current_year` | Integer. Starts at 1970. |
| `phase` | `preseason`, `regular`, `playoffs`, `offseason`, `draft`. Starts at `preseason`. |
| `rng_seed` | Drawn at creation. Distinct from the universe seed. |
| timestamps | |

The season clock is an integer football year plus a phase, not a Django `DateField`. `TIME_ZONE` does not define football time.

`rng_seed` feeds injury and development draws through the same HMAC pattern, keyed by campaign id, year, week, and `universe_key`. Two campaigns with the same user actions still diverge. Replaying one campaign stays reproducible because its seed is fixed.

`owner` points at the swappable user model so a later custom user does not require a rewrite. This design does not change `AUTH_USER_MODEL`. Public registration can wait. The foreign key is specified now so the first player rows are never global mutable state. Early admin demos use a staff owner; they are still campaigns.

### `CampaignPlayer`

Unique `(campaign, universe_player)`.

| Field | Notes |
|-------|--------|
| `status` | `prospect`, `active`, `taxi`, `reserve`, `free_agent`, `retired` |
| `campaign` | Denormalized alongside the player FK so season queries and deletes filter on one column. Services check that it matches `campaign_player.campaign`. |

Created for:

- every opening-cohort player, inside `start_campaign`
- every prospect in a draft class, inside `materialize_draft_class`, in the year that class is encountered

Retirement sets `status` and closes the open roster stint. Season lines stay. The universe player is unchanged, and the same person can still be active in another campaign.

### Team assignment

`RosterStint`:

| Field | Notes |
|-------|--------|
| `campaign`, `campaign_player` | Both stored. |
| `team` | FK to catalog `Team`, null while a free agent. `PROTECT`. |
| `role` | `starter`, `backup`, `taxi`, `reserve`, `free_agent` |
| `start_year`, `start_week` | Opening stints start at 1970, week 0. |
| `end_year`, `end_week` | Null while current. |

A player has one open stint. A trade or cut closes it and opens another in the same campaign. The opening template is not updated. Uniqueness of "who is on this franchise" is enforced inside the campaign, never globally: many campaigns can roster the same `universe_key` on different teams at once.

### Where the franchise sits in the pyramid

Promotion and relegation will eventually differ per campaign. Those results must not be written onto `Team.league`.

`CampaignPlacement` snapshots catalog membership at campaign start and then stores that campaign's own placement:

| Field | Notes |
|-------|--------|
| `campaign`, `team`, `season_year` | Unique together. |
| `league`, `conference`, `division` | FKs copied from the catalog for 1970. Later seasons update only these campaign rows when movement is executed. |

Player stints point at the franchise (`Team`). They do not point at a league membership that another user's season might change. Executing `MovementPath` is a later season component. The player design only requires that the placement it would update is campaign-scoped. Until that component exists, every campaign keeps the 1970 catalog placement it copied at start.

### Statistics

`SeasonLine` is the career record. One row per campaign player, per year, per franchise they played for:

| Group | Fields |
|-------|--------|
| Participation | `games`, `starts` |
| Passing | `pass_attempts`, `completions`, `pass_yards`, `pass_touchdowns`, `interceptions_thrown` |
| Rushing | `rush_attempts`, `rush_yards`, `rush_touchdowns` |
| Receiving | `targets`, `receptions`, `receiving_yards`, `receiving_touchdowns` |
| Defense | `tackles`, `sacks`, `interceptions`, `passes_defended`, `forced_fumbles` |
| Ball security | `fumbles`, `fumbles_lost` |
| Kicking | `fg_made`, `fg_attempts`, `xp_made`, `xp_attempts`, `punts`, `punt_yards` |

Unused columns stay zero. Every era uses this shape so a record book from 1970 through 2020 is one query. Era changes how the future sim fills the columns (a 1972 season produces more rushing attempts), not which table holds them.

`team` is a required FK to the catalog franchise the line was earned with. A free agent with no club has no counting line until a stint assigns one. Unique `(campaign_player, season_year, team)`. Also store `campaign` and index `(campaign, season_year)`.

Game-level player lines are a later child of a match entity. They are out of this proposal so decades of history do not multiply by a 14-to-17-game schedule before the box score exists. If they are added, `SeasonLine` remains the durable rollup and game lines may be pruned.

### Injuries

`Injury` belongs to a campaign player:

| Field | Notes |
|-------|--------|
| `year`, `week` | When it happened in that campaign. |
| `kind` | `sprain`, `break`, `concussion`, `tear`, `illness`, `wear` |
| `body_part` | Short code |
| `weeks_out` | |
| `season_ending` | |
| `career_altering` | When true, the same transaction writes a downward `CampaignRating` change. |

`durability` on the universe player is the shared tendency. The injury events are not. A career-altering injury in one campaign does not change potential and does not appear in any other campaign.

Kind frequencies and the era's injury multiplier come from the season era. The roll uses the campaign `rng_seed`.

### Development

`CampaignRating` stores the operational current value per component. `start_campaign` and draft materialization initialize it from `UniverseRating.initial`.

A later annual development step, run inside the campaign only:

```text
gap = potential - current          # potential read from UniverseRating
delta = f(gap, age vs peak_age, snaps from SeasonLine,
          durability, injury weeks this year, campaign rng draw)
current = clamp(current + delta, 0, potential)
```

`DevelopmentEvent` (campaign player, year, component, delta, reason) is the audit row written in that same transaction. Reasons include `age`, `snaps`, `injury`, and later `coaching` when that component exists. Coaching and team facilities, when they are built, modify the delta. They do not modify the universe player.

Contracts and salaries are intentionally absent. A future contract row would hang off `CampaignPlayer`, because money and length diverge per playthrough, and the cap era would be a season-era rule rather than a fact about the fictional person.

---

## Lifecycles

### Generate and publish a release

Operator command, modeled on the existing service style (`@transaction.atomic`, a small result dataclass) but batched. The 32-team admin view is the right pattern for 42 catalog rows. It is the wrong pattern for a full release of 28,384 players on today's 32-team tree: generation is a management command, not a browser request.

1. Confirm `root_league` is the active tier-1 league and record `catalog_fingerprint`.
2. Write eras, quotas, colleges, and college weights.
3. Build opening players, ratings, and `OpeningAssignment` rows.
4. Build draft classes from 1971 through `through_year`.
5. Leave the release in `draft` for inspection in admin.
6. `publish` flips status. Published rows are frozen except for the append rule above.

Unpublished regeneration upserts on `universe_key` inside one transaction, in chunks (about 500), and deletes keys that the seed no longer produces. Published regeneration is refused.

### Start a campaign

`start_campaign(owner, release, name)`:

1. Refuse unless the release is published and the catalog fingerprint still matches.
2. Create the campaign at 1970, phase `preseason`, with a new `rng_seed`.
3. Bulk-create `CampaignPlacement` for 1970 from the current catalog membership of each fingerprinted team.
4. Bulk-create `CampaignPlayer`, `CampaignRating`, and the opening `RosterStint` for the opening cohort only, including free agents (`team` null, role `free_agent`).
5. Do not read or copy draft classes.

Two campaigns started on the same release have equal sets of opening `universe_key` values and equal `(universe_key, team slug, role)` assignments. The second start does not increase `UniversePlayer` row count.

### Reach a later draft

When the season clock enters phase `draft` for `current_year` (first time: 1971):

1. Load `DraftClass` for that year on the pinned release. If the year is beyond `through_year`, append it first under the publish-append rule, or refuse if the release can no longer be extended.
2. `materialize_draft_class` bulk-creates campaign players and ratings for prospects not already present.
3. The campaign's draft board reads those prospects. Picks write `CampaignDraftPick (campaign, year, round, pick_number, team, prospect)`.
4. Unique `(campaign, year, pick_number)` and unique `(campaign, prospect)`.
5. Undrafted `draftable` prospects and the `undrafted` pool become free-agent stints in this campaign.

Advancing from 1970 to 1971 does not materialize 1972.

### Read a player card

Biography, body, college, origin era, and potential come from the universe player. Status, team, current ratings, this year's injury, and career totals come from the campaign. A client of one campaign receives only that campaign's overlay. Admin staff can inspect the shared universe without opening a campaign.

Illustrative shape for a later API, not implemented here:

```json
{
  "universe_key": "p-fg-1970-1-opening-1970-00412",
  "name": "Marcus Hale",
  "position": "RB",
  "birth_year": 1946,
  "college_slug": "red-cedar-state",
  "height_in": 71,
  "weight_lb": 208,
  "origin_era": "run-first",
  "campaign": {
    "year": 1974,
    "team_slug": "harbor-city",
    "status": "active",
    "ratings": { "speed": 78, "carrying": 74 },
    "rush_yards_career": 3410
  }
}
```

---

## Storage across decades

Rough size for one release built on today's 32-team tree, using the default quotas and an 80-year horizon (draft classes 1971–2049):

| Slice | Formula | Rows |
|-------|---------|------|
| Opening rosters | 32 × (40 + 8) | 1,536 |
| Opening free agents | fixed pool | 160 |
| Drafts 1971–1977 | 7 × (17 × 32 + 64) | 4,256 |
| Drafts 1978–1986 | 9 × (12 × 32 + 64) | 4,032 |
| Drafts 1987–1994 | 8 × (8 × 32 + 64) | 2,560 |
| Drafts 1995–2049 | 55 × (7 × 32 + 64) | 15,840 |
| **Universe players, one release** | 1,536 + 160 + 4,256 + 4,032 + 2,560 + 15,840 | **28,384** |
| Colleges, eras, quotas | | a few hundred |

Those 28,384 biographies exist once.

A campaign that plays the 1970 through 2019 seasons (50 seasons) and then runs the 2020 draft has overlaid the opening cohort plus 50 classes (1971–2020): 1,696 + 4,256 + 4,032 + 2,560 + 26 × 288 = **20,032** campaign players. Classes after 2020 are still only universe rows.

Season lines are the campaign table that grows with play. The 1970 tree has 1,536 rostered slots. With taxi players who appear, free agents who appear, and a second line when a player changes teams mid-year, a season is on the order of 1,600–2,000 lines. Fifty seasons are on the order of 80,000–100,000 lines per campaign. Injuries, stints, and draft picks are smaller.

One thousand such campaigns still share the same 28,384 universe players. Season-line growth is linear in campaigns × seasons played × players who appeared. It does not grow with campaigns × future classes × biography width.

MySQL notes for the eventual migration, consistent with the current strict-mode MySQL setup:

- Relational columns for ratings and season lines. No Postgres `ArrayField`. Avoid a JSON blob as the authority for ratings so era code can query a component without depending on a JSON feature flag.
- `PositiveSmallIntegerField` for 0–100 ratings.
- Index `(release, entry_route, entry_year)` on universe players, unique `(release, year)` on draft classes, unique `universe_key`.
- Index `(campaign, season_year)` on season lines. Range partitioning by `season_year` is a later option if a single campaign table becomes large. It is not required to ship the model.
- Bulk insert the opening overlay in one transaction. Per-row `update_or_create`, which the 32-team generator uses, fits 42 catalog rows and is a poor fit for the 1,696 opening players.

---

## Invariants

A future implementation can turn these into tests. This document does not add them.

1. `UniversePlayer` count is unchanged by `start_campaign` and by `materialize_draft_class`.
2. Two fresh campaigns on one release have the same opening `universe_key` set and the same opening `(universe_key, team.slug, role)` tuples.
3. Their draft-class prospect keys for any published year are equal to each other and to `DraftProspect` for that year.
4. An injury, season line, roster stint, rating change, or draft pick written in campaign A is invisible to campaign B and does not update universe tables.
5. Materializing year Y creates no campaign players whose `entry_year` is greater than Y.
6. An opening player generated for 1970 references the `run-first` era. A prospect in a 2004 class references `spread-influence`. Across a fixed seed, those cohorts differ in the era weights the generator was given (position mix, weight distribution, skill emphasis).
7. A published release rejects edits to an existing `universe_key`.
8. `start_campaign` rejects a release whose fingerprint does not match the live league tree.
9. Campaign code does not insert `League`, `Conference`, `Division`, or `Team` rows and does not update `Team.league`.
10. Every overlay row's denormalized `campaign` matches its `campaign_player.campaign`.

---

## How this stays inside the current project

- Slug and natural-key identity stay the external ids (`universe_key`, college slug, existing team slug).
- Service functions stay out of admin modules. Admin can gain a read-only universe browser and a publish action later; the heavy generate path is a command.
- `League.clean()` and the single active tier-1 rule stay as they are. Campaigns reference that tier-1 league and do not become leagues.
- `MovementPath` stays a catalog of allowed moves. Per-user movement results, when built, write `CampaignPlacement`.
- No new Django setting is required. The generator seed lives on `UniverseRelease`, so production settings and the PythonAnywhere environment file stay out of this work.
- No custom user model is required. The campaign owner FK should use `settings.AUTH_USER_MODEL` when the model is eventually added. If the product needs user fields Django's user does not have, decide that before the first campaign migration.

Suggested order when implementation is allowed:

1. `universe` models, era and college seed data, generator command, publish. Inspect 1970 assignments and a sample of later classes in admin. Still no campaigns.
2. `campaigns` models and `start_campaign` / `materialize_draft_class`, with the invariants above as tests.
3. Connect materialization to the season clock once that clock exists. The first draft a 1970 campaign runs is the 1971 class.
4. Development and injury resolution services, still writing only campaign rows.
5. Read API for the player card, joining universe biography to one campaign overlay.

Scoring, schedules, payments, and public pages stay outside this design. The overlay is shaped so a later box score can add a `SeasonLine` without moving biography into the campaign.

---

## Alternatives considered

**Copy the full player database into each user's league at creation, including future draft classes.** Every campaign would start identical, and isolation would be trivial. Storage and publish corrections would scale with users × decades × biography width, which is the duplication this design avoids. Future classes would also be frozen into saves that have not reached them, so a legitimate append of the year 2050 would not be one shared write.

**Store no universe rows and generate each player from the seed at read time.** The seed function in this proposal is already deterministic, and a pure function would use the least disk. Admin inspection, draft boards, and joins from season lines need a stable row to point at. Materializing once per release keeps the function as the source of truth and MySQL as the shared index.

**One global player row whose team, stats, and injuries update in place.** That matches a single shared multiplayer world. It cannot give two users independent careers for the same 1970 person. Federation Gridiron's current catalog is one shared pyramid; the playthrough requirement is many independent histories of that pyramid.

**Clone `League` / `Team` rows per user and hang players off the clone.** Isolation would ride on the cascade that already deletes a league's teams. It fights the single active tier-1 rule and turns franchise identity into a per-user slug. The 32-team tree is small enough to clone, and this design still does not clone it: franchises stay catalog rows, and `RosterStint` plus `CampaignPlacement` carry the divergence. Player biographies are the large shared set; teams are not.

---

## Decisions left open

These have defaults above. Changing them does not change the sharing model.

- Exact quotas and measurables for eras after 1970, under the rule that each quota sums to that era's roster limit.
- Whether lower-tier teams are loaded before the first universe release. If they are not, the first population covers the 32-team tree plus the free-agent pool, and a later catalog change is a new release.
- How large a draft class must be once lower-tier clubs also need incoming players. The default class is tier-1 rounds × tier-1 teams, plus 64 undrafted prospects. A deeper pyramid needs a larger undrafted pool, or its own rounds, before that release is published.
- A product cap on campaigns per user. The schema allows more than one.
- How soon scout reports are playable. The table split is specified; the UI can wait.
- Whether franchise display-name changes over eras are campaign overrides. Identity remains the catalog team slug either way.

---

## Document status

- Describes the `leagues` app as it exists on `main`: catalog models, one active tier-1 constraint, and the 32-team slug generator. No player code exists there yet.
- Specifies a shared universe plus a per-campaign overlay so 1970 rosters and draft classes are common, and careers are not.
- Adds no application code, database models, migrations, fixtures, or settings.
