# Federation Gridiron — Shared Fictional Player Universe

This document specifies the shared fictional people of Federation Gridiron and the per-timeline state that diverges from them.

This revision replaces the earlier draft of this file. That draft assumed 32 franchises, a first draft in 1971, classes generated through 2049, and 28,384 universe player rows. Those figures are obsolete. The current estimates are in [Population and row counts](#population-and-row-counts). The retired arithmetic is isolated under [Obsolete 32-team estimate](#obsolete-32-team-estimate) so it is not reused by accident.

It is a design proposal. It does not add Django models, migrations, services, settings, admin screens, or rows in a database.

The people in this system are fictional. Era profiles borrow the broad shape of professional football history (roster size, run/pass emphasis, draft length, body types). They are not a real-league roster dump and they do not use real player identities.

---

## Current architecture

The repository today is a Django 5.2 project (`requirements.txt`) with one domain app, `leagues`. Public URLs expose Django Admin only. There is no player, season, roster, staff, or end-user save model. `leagues/views.py` is empty.

| Model | Role today |
|-------|------------|
| `League` | Tiered federation node. `slug` is globally unique. `parent_league` builds a pyramid. `clean()` allows only one active tier-1 league. |
| `Conference` | Child of a league. Unique `(league, slug)`. |
| `Division` | Child of a conference. Unique `(conference, slug)`. |
| `Team` | Franchise inside a league, optionally placed in a conference and division. Unique `(league, slug)`. Optional `city`, `region`, `country`. |
| `MovementPath` | Allowed promotion, relegation, lateral, or special move. Definitions only; nothing executes them. |

`leagues/services/league_generator.py` still upserts **2 conferences, 8 divisions, and 32 teams** (`CONFERENCE_COUNT`, `DIVISION_COUNT`, `TEAM_COUNT`). That generator is the code on `main`. It is not the product shape this design uses.

The product shape is **24 fictional franchises** in the 1970 league. Proposed catalog layout, when the generator is later allowed to change: 2 conferences, 3 divisions per conference, 4 teams per division. Population math below depends on the count 24, not on the conference names. This document does not change the generator.

A `Team` row is a franchise identity shared by every timeline. Timeline code reads it and does not write `Team.league`, `Team.conference`, or `Team.division`. Those fields are catalog data. Promotion results, if they ever differ by timeline, belong on timeline placement rows.

Auth is Django's built-in user model. No `AUTH_USER_MODEL` override is set. The database is MySQL through `mysqlclient`. Domain writes go through `full_clean()` on `League` and through small `@transaction.atomic` services.

```text
leagues (exists, catalog)          universe (proposed, shared)           timelines (proposed, per user)
League → Conference                UniverseRelease                       Timeline
         → Division                Era, College                          owned franchise, directives
         → Team ←───────────────── OpeningAssignment                     RosterStint → Team
                                   OpeningContract                       TimelineContract
                                   OpeningStaffAssignment                StaffStint
                                   UniversePlayer / GM / Coach           overlays, current ratings
                                   pre-1970 histories and stats          1970–2029 stats, injuries,
                                   DraftClass 1970–2029                  awards, draft picks, contracts
```

Two future apps carry this, and neither is created here:

| App | Owns | Who writes it |
|-----|------|----------------|
| `universe` | One published release: players, GMs, coaches, 1970 rosters, free agents, draft classes, pre-1970 histories | A generator command, once per release |
| `timelines` | One owner's 60-season playthrough | Timeline services. They reference universe rows and never update them |

"Campaign" in the previous draft of this document is this timeline. The architecture is unchanged in kind: one shared universe, many independent playthroughs. The horizon, franchise count, opening draft, and staff model are what changed.

---

## Requirements

| Requirement | Where it lives |
|-------------|----------------|
| The game starts in 1970 with 24 fictional franchises | Catalog count used by the release. Opening assignments cover 24 teams. |
| The owner picks a franchise, keeps or replaces its GM and head coach, runs the 1970 rookie draft, sets a few broad instructions, then the season simulates | Timeline opening sequence. Shared data is read; the decisions are timeline rows. |
| A playthrough lasts 60 seasons, labeled 1970 through 2029 | [Year labels](#year-labels-and-the-60-season-horizon). |
| 100 initial GMs and 100 initial head coaches, each distinct | Shared staff cohort, with strengths, weaknesses, traits, and pre-1970 histories. |
| Every new timeline starts from the same identities, starting ratings, traits, pre-1970 histories and statistics, 1970 rosters, free-agent pool, and rookie classes | Published `UniverseRelease`. |
| Timelines diverge only after their decisions and seasons begin | Timeline tables. Universe rows stay immutable after publish. |
| The first draft is the 1970 rookie class; each later season drafts the next shared class | `DraftClass` years 1970–2029. |
| Each timeline keeps its own draft choices, assignments, injuries, current ratings, contracts, statistics, awards, and in-timeline career histories | Timeline entities in the data dictionary. |
| Decades of seasons do not copy the master people into each timeline | Biographies exist once. A timeline stores an overlay for a person when that person has entered that timeline. |

---

## Terms

| Term | Meaning |
|------|---------|
| **Universe release** | One immutable publication of the shared people, the 1970 league snapshot, and the draft classes. |
| **Timeline** | One owner's 60-season playthrough. The isolation boundary. Not a `leagues.League` row. |
| **Opening cohort** | Professionals already in the league before the 1970 rookie draft: 24 rosters plus the free-agent pool. They are not members of the 1970 draft class. |
| **1970 rookie class** | The shared draft class used before the first season. |
| **Archive cohort** | Fictional players who appear in 1960–1969 league history and are gone before the 1970 draft. Shared, so the record book is the same in every timeline. They do not play in 1970. |
| **Initial staff** | 100 GMs and 100 head coaches available at the 1970 decision point. |
| **Incoming staff class** | Shared GM and head-coach candidates who become hireable in a later offseason. Same identities in every timeline. |
| **Catalog franchise** | A `leagues.Team` row. Timelines share the row and keep their own assignments. |

An owner may have more than one timeline. Timelines do not share mutable rows.

---

## Year labels and the 60-season horizon

A **season label** is the calendar year in which that season is played. It is not a count of offseasons, and it is not `1970 + season_count`.

```text
season_index   1 .. 60
season_year    1969 + season_index
```

| season_index | season_year | Draft class used before that season |
|--------------|-------------|-------------------------------------|
| 1 | 1970 | 1970 |
| 2 | 1971 | 1971 |
| 60 | **2029** | 2029 |

The draft class year equals the season year that follows the draft. The first action of a new timeline, after the franchise and staff choices, is the **1970** draft. The draft before the last season uses the **2029** class. There are **60** shared classes, 1970 through 2029 inclusive (`2029 − 1970 + 1 = 60`).

**Working decision: the final playable season is 2029, not 2030.**

2030 would be `season_index` 61. Labeling the last season 2030 while also starting in 1970 produces 61 seasons (`2030 − 1970 + 1 = 61`). The estimates in this document use 2029. Choosing 2030 instead would add one modern-era class of 216 prospects and one more season of timeline rows; see the delta in the population section. That choice is listed under [Unresolved decisions](#unresolved-decisions) so it can be confirmed before implementation.

Pre-1970 history uses years **1960–1969**. Those years are outside the 60. Timeline stat tables accept only `season_year` 1970–2029. Shared history tables accept only `season_year` 1960–1969.

The football clock is this integer year plus a phase. `TIME_ZONE` does not define it.

---

## Opening sequence

A new timeline is pinned to a published release and starts **before** the 1970 season, with the shared 1969 world still intact.

1. **Choose a franchise.** One of the 24. Stored as `Timeline.owned_franchise`. The other 23 stay AI-controlled.
2. **Keep or replace that franchise's GM and head coach.** The shared snapshot already employs one GM and one head coach on each franchise (24 of each). The other 76 GMs and 76 head coaches are unemployed and are the only legal opening hires. Replacing writes timeline staff stints only. The other 23 franchises keep their shared GM and head coach until later timeline events.
3. **Complete the 1970 rookie draft.** Materialize the shared 1970 class into this timeline. Draft order comes from the shared 1969 standings (worst record picks first). The owner drafts for the owned franchise. AI GMs draft for the others, starting from their shared tendencies. Picks are timeline rows. The class list itself is not modified.
4. **Set a few broad instructions** for the owned franchise's GM and head coach. Four GM directives and four coach directives, defined in the data dictionary. AI franchises start from directives derived from the shared staff profile, so every timeline begins with the same AI tendencies.
5. **GMs reduce rosters to the 1970 limit** (40 active + 8 taxi) after the draft. That reduction is a timeline result. The shared opening roster remains the pre-draft roster.
6. **Simulate the 1970 season** (14 games under the `run-first` era). Phases then follow the usual loop: offseason, next year's shared draft class, season, through the 2029 season. After 2029 the phase is `complete`. No 2030 draft is generated for this horizon.

Phases on the timeline: `choose_franchise`, `staff`, `draft`, `instructions`, `roster_cut`, `regular`, `playoffs`, `offseason`, `complete`.

The shared snapshot is identical until step 1 writes the first timeline decision. Steps 1–4 are owner decisions. Step 5 and the 1970 games are the first simulated events. A timeline RNG seed is drawn at creation and is used only once simulation starts (steps 5 and 6), not to generate the people.

Whether two timelines that make the same choices in steps 1–4 must then produce a byte-identical 1970 season is unresolved. This design allows the season simulator to use the timeline seed, so identical opening decisions can still diverge once the season runs. The opening identities, ratings, histories, rosters, and draft class cannot diverge before those decisions.

---

## Sharing rules

1. Name, birth, college, position, body, traits, initial ratings, potential, pre-1970 statistics, pre-1970 employment, and draft-class membership are stored once, on the release.
2. A timeline stores the foreign key to that person plus what diverges after the opening decisions: draft picks, assignments, injuries, current ratings, contracts, 1970–2029 statistics, awards, and career events inside the timeline.
3. Timeline services write timeline tables only.
4. The generator writes universe tables only.
5. A future draft class or incoming staff class stays a universe row until that timeline's clock reaches the class year. Creating a timeline does not insert overlay rows for the 1988 class.
6. The 1970 class is materialized during the opening draft, before the 1970 season, because that draft is part of setup rather than a later offseason.
7. Publishing freezes the release. A correction is a new release. Existing timelines stay pinned, so a retune cannot change the 1970 roster or a class a timeline has already seen.
8. Opening contracts and opening staff assignments are shared templates. `start_timeline` copies them into timeline rows. Later cuts, extensions, and firings update the copies.
9. Pre-1970 counting stats are the shared record book. They are display. Starting ratings and traits are what the 1970 simulator reads. The generator builds the counting stats so they agree with those ratings; the sim does not replay 1960–1969.

---

## Universe release and identity

| Field | Purpose |
|-------|---------|
| `slug` | Stable id, unique. Example: `fg-1970-1`. |
| `generator_version` | Algorithm id. Example: `fg-player-gen-2`. |
| `seed` | Integer stored on the release row. Not a Django setting. |
| `root_league` | FK to the tier-1 `League`. `on_delete=PROTECT`. |
| `franchise_count` | Must be 24 for this product shape. |
| `catalog_fingerprint` | SHA-256 of sorted lines `{tier}:{league_slug}:{conference_slug}:{division_slug}:{team_slug}`. |
| `first_season_year` | 1970. |
| `season_count` | 60. |
| `last_season_year` | 2029 while the working decision holds. |
| `status` | `draft` or `published`. |
| timestamps | Same pattern as `leagues`. |

`start_timeline` requires a published release whose fingerprint still matches the live 24-franchise tree. The current 32-team generator cannot satisfy that check. Publishing waits until the catalog is the 24-franchise tree. This document does not perform that catalog change.

While `status` is `draft`, the generator may rebuild from `(generator_version, seed, fingerprint, era tables)`. After `published`, existing keys are frozen. Appending a year past `last_season_year` is a new product decision, not an implied extension.

Keys are opaque strings, max length 64. Release slugs contain hyphens, so clients look the row up instead of parsing the key.

```text
p-{release_slug}-opening-{index:05d}     veteran or free agent before the 1970 draft
p-{release_slug}-archive-{index:05d}     pro who left before the 1970 draft
p-{release_slug}-draft-{year}-{index:05d}
g-{release_slug}-{debut_year}-{index:03d}    GM
c-{release_slug}-{debut_year}-{index:03d}    head coach
```

Draws use HMAC-SHA256 of the release seed and a named key (`f"{universe_key}:weight"`), not `random.Random`. One attribute can be rebuilt without reshuffling the person. The same seed, generator version, era tables, and franchise fingerprint rebuild the same keys. Display names come from fictional name lists. Staff profiles must be unique across the 100 initial GMs and across the 100 initial head coaches: the publish step rejects two staff in the same role with the same strengths, weaknesses, traits, and career-history signature. Player identity is `universe_key`; display names may collide, and the UI disambiguates with position, college, and birth year.

---

## Eras

| Layer | Question | Effect on an existing person |
|-------|----------|------------------------------|
| **Origin era** | What kind of footballer or coach was created for this cohort? | Stored on the person and left in place. |
| **Season era** | What roster limit, schedule length, injury climate, and cap rules apply in the year being played? | The person is unchanged. The sim reads the era for the timeline's current year. |

| Key | Years | Active | Taxi | Draft rounds | Schedule | Generation emphasis |
|-----|-------|--------|------|--------------|----------|---------------------|
| `run-first` | 1970–1977 | 40 | 8 | 17 | 14 | Fullbacks on every roster, fewer receivers, run-block ahead of pass-block, passing traits rarer at the top, higher injury cost |
| `passing-rules` | 1978–1986 | 45 | 8 | 12 | 16 | Receiver and coverage traits more common |
| `timing-specialists` | 1987–1994 | 47 | 8 | 8 | 16 | Designated pass rush and nickel coverage become ordinary |
| `cap-and-size` | 1995–2003 | 53 | 8 | 7 | 16 | Heavier linemen; salary cap becomes a sim constraint |
| `spread-influence` | 2004–2013 | 53 | 8 | 7 | 16 | More receiver depth, edge speed ahead of bulk |
| `modern-passing` | 2014–2029 | 53 | 8 | 7 | 16 through 2020, 17 from 2021 | Hybrid skill profiles more common |

These are tunable defaults for a fictional 24-franchise league, not a claim that the game replays a real league's calendar. Each era owns a position quota that sums to its active limit and another that sums to its taxi limit. The generator refuses a quota that does not sum. Later-era quotas are data, filled before publish. Direction across eras: FB counts fall, WR and CB counts rise, draft rounds shrink, linemen get heavier.

### 1970 active quota (40 veterans, before the rookie draft)

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

The 1970 rookies are not in this quota. They enter through the draft, and the post-draft cut returns each franchise to 40 + 8.

### Ratings vocabulary

One component set for every era: **27** integers, 0–100, higher is better. Eras change how often high values are drawn. They do not add a column per decade.

| Group | Components |
|-------|------------|
| Physical | `speed`, `acceleration`, `agility`, `strength`, `stamina`, `durability` |
| Mental | `awareness`, `discipline`, `processing` |
| Offense | `throw_power`, `throw_accuracy`, `catching`, `route_running`, `carrying`, `elusiveness`, `run_blocking`, `pass_blocking` |
| Defense | `tackling`, `coverage`, `pass_rush`, `run_defense` |
| Special teams | `kicking`, `punting` |
| Personality | `work_ethic`, `leadership`, `temperament`, `clutch` |

Physical measurables outside that scale: `height_in`, `weight_lb`, `handedness`.

Shared rows store `initial` and `potential` for each component. Timeline rows store `current`, copied from `initial` when the person enters the timeline. Potential stays on the universe row and is read from there during development. Overall is derived for display and is not stored.

`growth_curve` on the universe person: `peak_age`, `development_rate`, `decline_rate`, with per-person variance baked in at generation.

### Colleges

`College` is shared: `slug`, `name`, `region`, pipeline tag (`independent`, `state`, `small`, `academy`). About 48–80 fictional schools. `EraCollegeWeight` changes how often a school is drawn by era. A timeline does not own colleges.

---

## Shared people and the 1970 snapshot

### Players who can play from 1970 on

Opening cohort, before the draft:

```text
rostered = 24 × (40 + 8) = 1,152
free agents = 5 × 24 = 120
opening cohort = 1,272
```

Every opening player has at least one prior pro season. Age is drawn first (starters mostly 24–30, backups 22–32, taxi 21–24, free agents slightly older). `birth_year = 1970 − age` at the 1970 offseason. Rookies born around 1948 are in the draft class, not on these rosters.

`OpeningAssignment`: one per opening player. `team` is null for free agents. `position` is stored on the assignment and must match the player. `depth` is the depth-chart index for that position on that team, starting at 1, across starter, backup, and taxi. Unique `(release, team, position, depth)`. MySQL unique indexes treat NULL `team` as distinct per row, so the free-agent pool does not need a partial index.

`OpeningContract`: one shared template per rostered veteran (1,152). Free agents have none. Copied into the timeline at start.

### 1970–2029 draft classes

`DraftClass` is unique on `(release, year)` for each year from 1970 through 2029.

```text
draftable = era(year).draft_rounds × 24
undrafted_pool = 2 × 24 = 48
class size = draftable + undrafted_pool
```

| Years | Rounds | Draftable | Undrafted | Class size | Classes | Prospects |
|-------|--------|-----------|-----------|------------|---------|-----------|
| 1970–1977 | 17 | 408 | 48 | 456 | 8 | 3,648 |
| 1978–1986 | 12 | 288 | 48 | 336 | 9 | 3,024 |
| 1987–1994 | 8 | 192 | 48 | 240 | 8 | 1,920 |
| 1995–2003 | 7 | 168 | 48 | 216 | 9 | 1,944 |
| 2004–2013 | 7 | 168 | 48 | 216 | 10 | 2,160 |
| 2014–2029 | 7 | 168 | 48 | 216 | 16 | 3,456 |
| **1970–2029** | | **13,272** | **2,880** | | **60** | **16,152** |

A prospect stores a shared `board_rank` and a pool of `draftable` or `undrafted`. The prospect does not store a destination team. Round, pick, and franchise are timeline facts. True ratings stay on the universe player. Any scout fog is a timeline report, never a column on the shared prospect.

`materialize_draft_class` is idempotent on `(timeline, universe_player)`.

### Archive cohort (record book only)

Shared pro history covers 1960–1969 at the same 24 × 48 roster:

```text
historical roster-seasons = 24 × 48 × 10 = 11,520
```

Planning assumption: a mean stay of 5 seasons inside that window, so distinct historical players = `11,520 / 5 = 2,304`. The 1,272 opening players are part of that set. The rest are the archive:

```text
archive ≈ 2,304 − 1,272 = 1,032
```

Archive players have universe identities, pre-1970 stats, and awards. They are not on 1970 rosters, not in the free-agent pool, and not placed on timelines as active players. The generator's actual archive count is whatever headcount fills 11,520 lines once the opening cohort is seated in 1969. 1,032 is the planning figure tied to the 5-year mean.

### Staff

**Initial, hireable before the 1970 draft:** 100 GMs and 100 head coaches.

| | Employed on a 1970 franchise | Unemployed, available to hire |
|--|------------------------------|-------------------------------|
| GM | 24 | 76 |
| Head coach | 24 | 76 |

Each of the 100 in a role has a distinct combination of strength tags, weakness tags, trait tags, and career-history signature. Ages in 1970 run about 32–65. Employed head coaches skew older; the unemployed pool includes younger coordinators.

**Incoming classes, 1971–2029 (59 years):** 8 head-coach candidates and 6 GM candidates per year, shared, debuting in that offseason.

```text
future head coaches = 59 × 8 = 472
future GMs = 59 × 6 = 354
all head coaches = 100 + 472 = 572
all GMs = 100 + 354 = 454
all staff identities = 1,026
```

A timeline creates overlay rows for the initial 200 at `start_timeline`, because the owner may hire any unemployed member before the season. An incoming staff class is materialized in the offseason of its debut year, the same way a draft class is. New staff are not invented inside one timeline, so two timelines are hiring from the same people.

Initial staff draw schemes that exist in 1970: `power_run`, `option`, `pro_style` on offense, and `stop_run` or `balanced` on defense. A coach who debuts in a later class may draw that era's schemes (`west_coast` from 1987, `air_raid` from the spread era).

### Shared 1969 standings

`UniverseFranchiseSeason` for each franchise for each year 1960–1969 (240 rows). The 1969 wins, losses, and ties set the 1970 draft order. Later draft orders come from that timeline's own standings.

---

## Data dictionary

Columns on every table:

| Column | Meaning |
|--------|---------|
| Layer | `Shared` or `Timeline` |
| Type | Logical type. Integers are small unless noted. |
| Range / units | Legal values. |
| Origin | `Generator`, `Shared template`, `Owner`, `Timeline sim`, or `Derived` |
| Use | `Sim`, `Display`, or `Both` |

`Derived` fields are computed when shown and are not stored. Counting-stat fields that the sim aggregates from games are stored season totals: the match engine (out of scope) writes them, and later ratings, awards, and AI decisions read them.

### How a career is read

A career page concatenates shared rows for years ≤ 1969 with timeline rows for years 1970–2029. It does not copy the shared years into the timeline.

---

### Player identity and body

All of these are one shared row per player (`UniversePlayer`).

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `universe_key` | Shared | string | ≤ 64, opaque, unique | Generator | Both |
| `name` | Shared | string | ≤ 255 | Generator | Display |
| `birth_year` | Shared | smallint | 1920–2008 | Generator | Both |
| `birth_day` | Shared | smallint | 1–365 | Generator | Both |
| `college` | Shared | FK | known college slug | Generator | Display |
| `position` | Shared | enum | `QB` `RB` `FB` `WR` `TE` `OT` `OG` `C` `DT` `DE` `LB` `CB` `S` `K` `P` | Generator | Both |
| `handedness` | Shared | enum | `left` `right` | Generator | Display |
| `height_in` | Shared | smallint | 64–84 inches | Generator, era distribution | Both |
| `weight_lb` | Shared | smallint | 160–360 pounds, by position and era | Generator | Both |
| `cohort` | Shared | enum | `opening` `archive` `draft` | Generator | Both |
| `first_pro_year` | Shared | smallint | 1960–1969 for opening and archive players; the class year for a draftable prospect; null for the undrafted pool, whose actual debut is a timeline fact | Generator | Both |
| `origin_draft_year` | Shared | smallint | ≤ 1969 for opening/archive; class year for the draft cohort; null if the player entered as an undrafted historical free agent | Generator | Display |
| `origin_draft_round` | Shared | smallint | 1–17, or null | Generator | Display |
| `origin_draft_pick` | Shared | smallint | 1–408 in the 1970s shape, or null | Generator | Display |
| `origin_draft_franchise` | Shared | FK | catalog team, or null | Generator | Display |
| `era` | Shared | FK | origin era | Generator | Both |
| `peak_age` | Shared | smallint | 24–32 | Generator | Sim |
| `development_rate` | Shared | smallint | 1–100 | Generator | Sim |
| `decline_rate` | Shared | smallint | 1–100 | Generator | Sim |
| `board_rank` | Shared | smallint | 1–class size; draft cohort only | Generator | Both |

`origin_draft_*` on an opening player is the historical class he already entered. It is not a seat in the 1970 draft. The 1970 class has its own player rows.

### Player ratings

Stored on the shared player as `initial` and `potential`, and on the timeline player as `current`. Same range for every component: integer 0–100. Origin of `initial` and `potential`: Generator. Origin of `current`: Shared template at entry, then Timeline sim. Use: Both. Potential is shared even after `current` diverges.

| Component | What it feeds |
|-----------|----------------|
| `speed`, `acceleration`, `agility` | Movement, separation, coverage, return ability |
| `strength` | Blocking, tackling, pass rush, breaking tackles |
| `stamina` | Late-game decline |
| `durability` | Injury frequency and recovery |
| `awareness`, `processing`, `discipline` | Assignment errors, pre-snap, penalties |
| `throw_power`, `throw_accuracy` | Passing. Near a low baseline for non-quarterbacks |
| `catching`, `route_running` | Receiving |
| `carrying`, `elusiveness` | Rushing and yards after contact |
| `run_blocking`, `pass_blocking` | Offensive line, tight ends, fullbacks |
| `tackling`, `run_defense`, `pass_rush`, `coverage` | Defense |
| `kicking`, `punting` | Specialists. Near a low baseline for everyone else |
| `work_ethic`, `leadership` | Development and teammate effects |
| `temperament` | Penalties, durability of performance after errors |
| `clutch` | Late-game and high-leverage modifier |

Non-position skills are still stored, at a low baseline, so development code can stay position-agnostic.

### Player traits that are not ratings

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `trait_tags` | Shared | set of enum | 0–3 tags from `film_junkie`, `gym_rat`, `hothead`, `quiet_pro`, `mentor`, `injury_prone_label`, `big_play`, `scheme_specific` | Generator | Both |

`injury_prone_label` is flavor consistent with a low `durability`. The sim reads `durability`, not the label. Tags are display plus a small sim modifier where the tag says so (`hothead` adds penalty chance, `mentor` adds a small development bonus to younger teammates). The dictionary treats those two as Sim and the others as Display. They are fixed for the life of the release.

### Opening roster and opening contract

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `team` | Shared | FK, null for free agents | one of 24, or null | Generator | Both |
| `role` | Shared | enum | `starter` `backup` `taxi` `free_agent` | Generator | Both |
| `depth` | Shared | smallint | ≥ 1 on a team; null if free agent | Generator | Both |
| `position` | Shared | enum | same as the player | Generator | Both |
| `years` | Shared template | smallint | 1–5 remaining entering 1970 | Generator | Both |
| `salary_per_year` | Shared template | int | 1970 dollars, 20,000–250,000 | Generator | Display before 1995; Both from the cap era on the timeline copy |
| `guaranteed` | Shared template | int | 1970 dollars, 0–salary × years | Generator | Display before 1995; Both once the cap exists, on the timeline copy |
| `signing_bonus` | Shared template | int | 1970 dollars | Generator | Display |
| `start_year`, `end_year` | Shared template | smallint | `end_year` ≥ 1970, `start_year` ≤ 1970 | Generator | Both |

The timeline copy is the contract that can be cut or extended. The template is not.

### Shared pro history (1960–1969)

`UniverseProStint`: continuous employment before 1970.

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `player` | Shared | FK | opening or archive | Generator | Display |
| `team` | Shared | FK | catalog franchise | Generator | Display |
| `start_year`, `end_year` | Shared | smallint | 1960–1969, end ≥ start | Generator | Display |
| `role` | Shared | enum | `starter` `backup` `taxi` | Generator | Display |

`UniverseProSeason`: one row per player, per year, per franchise. Years 1960–1969 only. The stat columns are the same set as the timeline season, listed by position below. Origin: Generator. Use: Display. The sim does not replay these rows.

`UniverseAward`, years 1960–1969:

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `season_year` | Shared | smallint | 1960–1969 | Generator | Display |
| `kind` | Shared | enum | `mvp` `opoy` `dpoy` `oroy` `droy` `all_league_1` `all_league_2` `all_star` `coach_of_year` `executive_of_year` | Generator | Display |
| `player` | Shared | FK, nullable | player or null | Generator | Display |
| `staff` | Shared | FK, nullable | GM or head coach, or null | Generator | Display |
| `franchise` | Shared | FK | team at the time | Generator | Display |

Exactly one of `player` or `staff` is set. All-league rows name a position as `slot` (`QB`, `RB`, …).

### College history

`UniverseCollegeSeason`, one row per player per college season. Planning default: 4 seasons each. The anchor year is `first_pro_year` when set, otherwise the draft-class year. College seasons are the four years before that anchor. Origin: Generator. Use: Display. Draft `board_rank` already carries the scouting conclusion, so college counting stats are not a second sim input.

| Field | Type | Range / units | Applies to |
|-------|------|---------------|------------|
| `season_year` | smallint | the four years before the anchor year | All |
| `class_standing` | enum | `freshman` `sophomore` `junior` `senior` | All |
| `games`, `starts` | smallint | 0–12 | All |
| Passing, rushing, receiving, defense, kicking columns | same definitions as pro seasons | same ranges, college volume | The player's position group |

---

### Pro season statistics

Stored twice with the same columns:

| Store | Layer | Years | Who |
|-------|-------|-------|-----|
| `UniverseProSeason` | Shared | 1960–1969 | Opening and archive players |
| `TimelineSeason` | Timeline | 1970–2029 | Players who appeared in that timeline |

`TimelineSeason.team` is required. A free agent with no club has no counting line until a stint assigns one. Unique `(player overlay, season_year, team)` on the timeline, and the analogous unique key on the shared row. Yards are signed integers (sacks and tackles for loss can drive negative team yards; individual rush and receiving yards may be negative). Counts are ≥ 0. `games` and `starts` cannot exceed that season's schedule length (14, 16, or 17).

Origin of shared seasons: Generator, Display. Origin of timeline seasons: Timeline sim, Both (awards, development, and AI read them; the UI shows them).

#### All positions

| Field | Type | Range / units | Use on a timeline row |
|-------|------|---------------|------------------------|
| `games` | smallint | 0–schedule length | Both |
| `starts` | smallint | 0–`games` | Both |
| `offensive_snaps` | smallint | 0–`games` × 90 | Both |
| `defensive_snaps` | smallint | 0–`games` × 90 | Both |
| `special_teams_snaps` | smallint | 0–`games` × 30 | Both |
| `penalties` | smallint | 0–30 | Both |
| `penalty_yards` | smallint | 0–300 | Display |
| `fumbles` | smallint | 0–20 | Both |
| `fumbles_lost` | smallint | 0–`fumbles` | Both |

#### Quarterback

Primary sim readers: `throw_power`, `throw_accuracy`, `awareness`, `processing`, plus the passing line.

| Field | Type | Range / units |
|-------|------|---------------|
| `pass_attempts` | smallint | 0–800 |
| `completions` | smallint | 0–`pass_attempts` |
| `pass_yards` | int | −200–6,000 |
| `pass_touchdowns` | smallint | 0–60 |
| `interceptions_thrown` | smallint | 0–40 |
| `sacks_taken` | smallint | 0–80 |
| `sack_yards` | smallint | 0–600 |
| `rush_attempts` | smallint | 0–200 |
| `rush_yards` | int | −100–1,500 |
| `rush_touchdowns` | smallint | 0–20 |

Passer rating is derived for display from attempts, completions, yards, touchdowns, and interceptions.

#### Running back and fullback

| Field | Type | Range / units |
|-------|------|---------------|
| `rush_attempts` | smallint | 0–450 |
| `rush_yards` | int | −50–2,500 |
| `rush_touchdowns` | smallint | 0–30 |
| `targets` | smallint | 0–150 |
| `receptions` | smallint | 0–`targets` |
| `receiving_yards` | int | −20–1,200 |
| `receiving_touchdowns` | smallint | 0–15 |
| `drops` | smallint | 0–`targets` − `receptions` |
| `run_block_snaps` | smallint | 0–offensive snaps | 

`run_block_snaps` is Both for fullbacks and Display for tailbacks.

#### Wide receiver and tight end

| Field | Type | Range / units |
|-------|------|---------------|
| `targets` | smallint | 0–220 |
| `receptions` | smallint | 0–`targets` |
| `receiving_yards` | int | −20–2,000 |
| `receiving_touchdowns` | smallint | 0–25 |
| `drops` | smallint | 0–30 |
| `rush_attempts` | smallint | 0–40 |
| `rush_yards` | int | −10–400 |
| `rush_touchdowns` | smallint | 0–8 |
| `run_block_snaps` | smallint | 0–`offensive_snaps` |
| `pass_block_snaps` | smallint | 0–offensive snaps |

#### Offensive line (`OT`, `OG`, `C`)

| Field | Type | Range / units | Use |
|-------|------|---------------|-----|
| `run_block_snaps` | smallint | 0–`offensive_snaps` | Both |
| `pass_block_snaps` | smallint | 0–`offensive_snaps` | Both |
| `sacks_allowed` | smallint | 0–20 | Both |
| `penalties` | smallint | already on the common line; holding and false starts dominate | Both |

#### Defensive line (`DT`, `DE`)

| Field | Type | Range / units |
|-------|------|---------------|
| `tackles` | smallint | 0–120 |
| `tackles_for_loss` | smallint | 0–30 |
| `sacks` | smallint | 0–250 tenths of a sack (75 means 7.5; display divides by 10) |
| `quarterback_hits` | smallint | 0–40 |
| `forced_fumbles` | smallint | 0–10 |
| `fumble_recoveries` | smallint | 0–8 |
| `passes_defended` | smallint | 0–15 |
| `defensive_touchdowns` | smallint | 0–4 |

The same tenths-of-a-sack encoding is used for linebacker sack totals.

#### Linebacker

Same defensive columns as the line, with higher tackle ranges (`tackles` 0–200, `sacks` usually lower). Add:

| Field | Type | Range / units |
|-------|------|---------------|
| `coverage_snaps` | smallint | 0–`defensive_snaps` |
| `targets_in_coverage` | smallint | 0–80 |
| `completions_allowed` | smallint | 0–`targets_in_coverage` |
| `interceptions` | smallint | 0–10 |

#### Defensive back (`CB`, `S`)

| Field | Type | Range / units |
|-------|------|---------------|
| `tackles` | smallint | 0–140 |
| `interceptions` | smallint | 0–15 |
| `passes_defended` | smallint | 0–30 |
| `targets_in_coverage` | smallint | 0–140 |
| `completions_allowed` | smallint | 0–`targets_in_coverage` |
| `yards_allowed` | int | 0–1,500 |
| `forced_fumbles` | smallint | 0–8 |
| `defensive_touchdowns` | smallint | 0–4 |

#### Kicker

| Field | Type | Range / units |
|-------|------|---------------|
| `fg_made` | smallint | 0–45 |
| `fg_attempts` | smallint | 0–50, ≥ `fg_made` |
| `fg_long` | smallint | 0–65 yards, 0 if no makes |
| `xp_made` | smallint | 0–70 |
| `xp_attempts` | smallint | 0–75, ≥ `xp_made` |
| `kickoff_touchbacks` | smallint | 0–90 |

#### Punter

| Field | Type | Range / units |
|-------|------|---------------|
| `punts` | smallint | 0–120 |
| `punt_yards` | int | 0–6,000 |
| `punts_inside_20` | smallint | 0–`punts` |
| `punt_touchbacks` | smallint | 0–`punts` |
| `punt_long` | smallint | 0–90 yards |

Net average and yards per punt are derived for display.

Unused columns on a season row stay 0. One row shape covers every position and every era, so a 1970-to-2029 record book is one query. The era changes how the sim fills the columns.

---

### Timeline player overlay

Created for opening-cohort players at `start_timeline`, for a draft class when that class is materialized, and never for the archive cohort.

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `timeline` | Timeline | FK | | Owner's new timeline | Both |
| `player` | Timeline | FK | universe player | Shared template | Both |
| `status` | Timeline | enum | `prospect` `active` `taxi` `reserve` `free_agent` `retired` | Timeline sim, or the opening copy | Both |
| `current` ratings | Timeline | 27 × smallint | 0–100, ≤ potential | Copied from `initial`, then Timeline sim | Both |
| `seasons_played` | Timeline | smallint | 0–60, pro seasons inside this timeline only | Derived, may be cached | Display |
| `career_altering_injuries` | Timeline | smallint | 0–10 | Timeline sim | Both |

Unique `(timeline, player)`.

#### Roster stint

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `team` | Timeline | FK, nullable | catalog franchise, null if free agent | Copied from the opening assignment, then Timeline sim | Both |
| `role` | Timeline | enum | `starter` `backup` `taxi` `reserve` `free_agent` | same | Both |
| `start_year`, `start_week` | Timeline | smallint | year 1970–2029; week 0 means preseason | same | Both |
| `end_year`, `end_week` | Timeline | smallint, null if open | ≥ start | Timeline sim | Both |

One open stint per player. A trade closes one stint and opens another in that timeline only.

#### Injury

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `season_year` | Timeline | smallint | 1970–2029 | Timeline sim | Both |
| `week` | Timeline | smallint | 1–schedule length, or 0 in camp | Timeline sim | Both |
| `kind` | Timeline | enum | `sprain` `break` `concussion` `tear` `illness` `wear` | Timeline sim, weighted by season era | Both |
| `body_part` | Timeline | enum | `head` `shoulder` `arm` `hand` `back` `hip` `thigh` `knee` `ankle` `foot` | Timeline sim | Both |
| `weeks_out` | Timeline | smallint | 0–36 | Timeline sim | Sim |
| `season_ending` | Timeline | bool | | Timeline sim | Both |
| `career_altering` | Timeline | bool | when true, the same transaction drops one or more `current` ratings | Timeline sim | Both |

`durability` is the shared tendency. The injury row exists only on the timeline.

#### Timeline contract

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `years` | Timeline | smallint | 1–7 | Copied from the opening template, or created at signing | Both |
| `salary_per_year` | Timeline | int | dollars. Before 1995, display and negotiation flavor. From 1995, cap accounting | same | Both from 1995; Display before |
| `guaranteed` | Timeline | int | dollars | same | same |
| `signing_bonus` | Timeline | int | dollars | same | Display; Sim from 1995 (cap proration) |
| `start_year`, `end_year` | Timeline | smallint | 1970–2035 (a 2029 signing can run past the final season) | same | Both |
| `status` | Timeline | enum | `active` `expired` `cut` `retired` | Timeline sim | Both |
| `franchise` | Timeline | FK | catalog team | same | Both |
| `is_rookie_deal` | Timeline | bool | true for contracts created by a draft signing | Timeline sim | Display |

Cap compliance is a season-era rule from 1995 (`cap-and-size`) onward. The cap limit itself lives on the era, not on the person. There is no shared "this player signed with that team in 1974" row. Only the pre-draft 1970 veteran deals are shared templates.

#### Timeline draft pick

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `season_year` | Timeline | smallint | 1970–2029, the class year | Owner for the owned franchise; Timeline sim for AI clubs | Both |
| `round` | Timeline | smallint | 1–that era's round count | same | Both |
| `pick_number` | Timeline | smallint | 1–`draftable` count | same | Both |
| `franchise` | Timeline | FK | catalog team | same | Both |
| `prospect` | Timeline | FK | shared prospect | same | Both |

Unique `(timeline, season_year, pick_number)` and `(timeline, prospect)`. The undrafted pool has no pick row. Signing an undrafted player creates a contract and a stint only.

#### Timeline award

Same `kind` enum as the shared award, `season_year` 1970–2029, recipient and franchise set by the timeline sim. Display, and Sim for the following offseason's reputation and development.

#### Development event

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `season_year` | Timeline | smallint | 1970–2029 | Timeline sim | Display |
| `component` | Timeline | enum | rating vocabulary | Timeline sim | Display |
| `delta` | Timeline | smallint | −20–+20 | Timeline sim | Display |
| `reason` | Timeline | enum | `age` `snaps` `injury` `coaching` | Timeline sim | Display |

The stored authority for "what is he now" is `current`. The event is the audit trail. Development reads shared `potential`, `peak_age`, and `durability`, plus this timeline's snaps and injuries.

```text
gap = potential − current
delta = f(gap, age vs peak_age, snaps, durability, injury weeks, timeline rng)
current = clamp(current + delta, 0, potential)
```

---

### General managers

One shared row per GM (`UniverseGM`), plus a timeline overlay (`TimelineGM`) holding `current` ratings and employment.

#### Identity and ability

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `universe_key` | Shared | string | `g-…`, unique | Generator | Both |
| `name` | Shared | string | ≤ 255 | Generator | Display |
| `birth_year` | Shared | smallint | initial cohort about 1905–1938 so ages in 1970 are 32–65; incoming classes are about 40 at debut | Generator | Both |
| `debut_year` | Shared | smallint | 1970 for the initial 100; 1971–2029 for incoming classes | Generator | Both |
| `former_role` | Shared | enum | `player` `scout` `college_coach` `personnel` `cap_analyst` | Generator | Display |
| `talent_evaluation` | Shared `initial`/`potential`; Timeline `current` | smallint | 0–100 | Generator, then Timeline sim | Both |
| `scheme_fit` | same | smallint | 0–100 | same | Both |
| `negotiation` | same | smallint | 0–100 | same | Both |
| `draft_strategy` | same | smallint | 0–100 | same | Both |
| `free_agency` | same | smallint | 0–100 | same | Both |
| `roster_construction` | same | smallint | 0–100 | same | Both |
| `player_development` | same | smallint | 0–100 | same | Both |
| `discipline` | same | smallint | 0–100 | same | Both |
| `leadership` | same | smallint | 0–100 | same | Both |
| `risk_tolerance` | same | smallint | 0–100 | same | Both |

#### Distinct strengths, weaknesses, and traits

Exactly two strengths, one weakness, two traits. Tags are not repeated between the strength list and the weakness list. The tuple plus the career-history signature is unique among GMs in the release.

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `strengths` | Shared | 2 enums | `quarterback_eye`, `lineman_eye`, `finds_speed`, `finds_size`, `trade_market`, `cap_discipline`, `loyalty`, `medical_caution`, `late_round`, `character` | Generator | Both |
| `weakness` | Shared | enum | same vocabulary as strengths | Generator | Both |
| `traits` | Shared | 2 enums | `patient`, `impatient`, `secretive`, `public`, `delegator`, `hands_on`, `stubborn`, `adaptable`, `gambler`, `conservative` | Generator | Both |

Strengths add a bonus when the sim resolves that kind of decision. The weakness applies a penalty. Traits break ties (a `gambler` trades more often at the same `risk_tolerance`).

#### GM career history

`UniverseStaffPost` for years ending ≤ 1969 (initial cohort) or ≤ `debut_year − 1` (incoming). Use: Display. Ratings are what the sim reads; the posts are generated to agree with those ratings.

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `role` | Shared | enum | `gm` `personnel_director` `scout` `college_coach` | Generator | Display |
| `franchise` | Shared | FK, nullable | null for a college post | Generator | Display |
| `start_year`, `end_year` | Shared | smallint | through the year before they are hireable | Generator | Display |
| `wins`, `losses`, `ties` | Shared | smallint | ≥ 0; ties allowed; 0 if the post had no games | Generator | Display |
| `playoff_appearances` | Shared | smallint | 0–20 | Generator | Display |
| `championships` | Shared | smallint | 0–`playoff_appearances` | Generator | Display |

Timeline employment is `StaffStint`:

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `role` | Timeline | enum | `gm` | Copied from `OpeningStaffAssignment`, then Owner or Timeline sim | Both |
| `franchise` | Timeline | FK, nullable | null if unemployed | same | Both |
| `start_year`, `end_year` | Timeline | smallint | 1970–2029; `end_year` null while open | same | Both |
| `reason_ended` | Timeline | enum | `opening_replace` `fired` `retired` `hired_elsewhere` `timeline_complete` | Owner or Timeline sim | Display |

The owner's opening replace sets `reason_ended = opening_replace` on the displaced GM and opens a stint for the hire, both before the 1970 draft.

---

### Head coaches

Same identity pattern as GMs, with key prefix `c-`.

#### Ability

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `offensive_scheme` | Shared `initial`/`potential`; Timeline `current` | smallint | 0–100 | Generator, then Timeline sim | Both |
| `defensive_scheme` | same | smallint | 0–100 | same | Both |
| `game_management` | same | smallint | 0–100 | same | Both |
| `player_development` | same | smallint | 0–100 | same | Both |
| `motivation` | same | smallint | 0–100 | same | Both |
| `discipline` | same | smallint | 0–100 | same | Both |
| `in_game_adjustments` | same | smallint | 0–100 | same | Both |
| `aggressiveness` | same | smallint | 0–100 | same | Both |
| `staff_management` | same | smallint | 0–100 | same | Both |
| `clock_management` | same | smallint | 0–100 | same | Both |
| `preferred_offense` | Shared | enum | `power_run` `option` `pro_style`; add `west_coast` from 1987 debuts; add `air_raid` from 2004 debuts | Generator | Both |
| `preferred_defense` | Shared | enum | `stop_run` `balanced` `pressure_quarterback` | Generator | Both |

Scheme enums are fixed on the person. The 0–100 scheme ratings move on the timeline. A 1970 `power_run` coach does not become an `air_raid` coach in 2014; his pass-game rating can still rise.

#### Distinct strengths, weaknesses, and traits

Exactly two strengths, one weakness, two traits. Unique tuple plus career-history signature among head coaches.

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `strengths` | Shared | 2 enums | `run_game`, `passing_game`, `red_zone`, `defense`, `special_teams`, `develops_quarterbacks`, `develops_linemen`, `halftime_adjustments`, `discipline`, `closer` | Generator | Both |
| `weakness` | Shared | enum | same vocabulary | Generator | Both |
| `traits` | Shared | 2 enums | `players_coach`, `disciplinarian`, `innovator`, `conservative`, `volatile`, `even_keel`, `workaholic`, `delegator` | Generator | Both |

#### Coach career history

Same post shape as the GM, with `role` in `head_coach`, `offensive_coordinator`, `defensive_coordinator`, `position_coach`, `college_head_coach`. Wins and losses are the post's record. Display only; `motivation`, scheme ratings, and traits are the sim inputs.

Opening assignment: `OpeningStaffAssignment (release, staff, franchise, role)` for the 24 employed GMs and 24 employed head coaches. Unique `(release, franchise, role)` and unique `(release, staff)`.

---

### Owner directives

Set in the `instructions` phase for the owned franchise. Required before the 1970 season can simulate. AI franchises receive an initial directive derived from the shared staff tags so the starting tendency is identical in every timeline. Later changes append a row; they do not edit the previous one.

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `franchise` | Timeline | FK | the owned franchise for owner-set rows; any franchise for AI rows | Owner or Timeline sim | Sim |
| `role` | Timeline | enum | `gm` `head_coach` | same | Sim |
| `effective_year`, `effective_week` | Timeline | smallint | first row is 1970 week 0 | same | Both |
| `draft_approach` | Timeline | enum | `best_player_available` `fill_needs` `trade_back` | Owner, GM only | Sim |
| `veteran_preference` | Timeline | enum | `youth` `balanced` `win_now` | Owner, GM only | Sim |
| `free_agency` | Timeline | enum | `conservative` `selective` `aggressive` | Owner, GM only | Sim |
| `trade_willingness` | Timeline | enum | `low` `medium` `high` | Owner, GM only | Sim |
| `offensive_emphasis` | Timeline | enum | `run_heavy` `balanced` `pass_heavy` | Owner, coach only | Sim |
| `defensive_emphasis` | Timeline | enum | `stop_run` `balanced` `pressure_quarterback` | Owner, coach only | Sim |
| `fourth_down` | Timeline | enum | `conservative` `balanced` `aggressive` | Owner, coach only | Sim |
| `playing_time` | Timeline | enum | `veterans` `balanced` `develop_youth` | Owner, coach only | Sim |

GM rows leave coach fields null, and the reverse. The UI shows the current directive. The sim reads the row in effect for that week.

---

### Timeline header

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `owner` | Timeline | FK | `settings.AUTH_USER_MODEL` | Owner | Display |
| `release` | Timeline | FK | published release, `PROTECT` | Owner | Both |
| `owned_franchise` | Timeline | FK | one of 24 | Owner | Both |
| `name` | Timeline | string | ≤ 255 | Owner | Display |
| `season_index` | Timeline | smallint | 1–60 | Timeline sim | Both |
| `season_year` | Timeline | smallint | 1970–2029, equals `1969 + season_index` | Derived | Both |
| `phase` | Timeline | enum | see opening sequence | Timeline sim | Both |
| `rng_seed` | Timeline | int | drawn at creation | Generator of the timeline, not the universe seed | Sim |

---

## Population and row counts

Franchise count **24**. Classes **1970–2029** (60). Final season **2029**. Free agents **5 per franchise**. Undrafted pool **2 per franchise**. Roster defaults are the era table above.

### Shared, stored once per release

| Slice | Formula | Rows |
|-------|---------|------|
| Opening rosters | 24 × (40 + 8) | 1,152 |
| Opening free agents | 5 × 24 | 120 |
| **Opening cohort** | | **1,272** |
| Draft prospects 1970–2029 | see class table | **16,152** |
| **Playable player identities** | 1,272 + 16,152 | **17,424** |
| Archive players | 11,520 historical roster-seasons / 5 − 1,272 | **1,032** |
| **All player identities** | 17,424 + 1,032 | **18,456** |
| Initial GMs + head coaches | 100 + 100 | 200 |
| Incoming staff 1971–2029 | 59 × (8 + 6) | 826 |
| **Staff identities** | | **1,026** |
| Shared pro season lines 1960–1969 | 24 × 48 × 10 | 11,520 |
| Shared college season lines | 18,456 × 4 | 73,824 |
| Shared franchise seasons 1960–1969 | 24 × 10 | 240 |
| Opening contracts | rostered veterans | 1,152 |
| Opening staff assignments | 24 GMs + 24 head coaches | 48 |
| Draft classes | one per year | 60 |

Playable identities are the people a timeline can employ from 1970 on. Archive identities exist so 1960–1969 stats and awards point at someone. College lines are display. If college counting stats are dropped later, the shared row count falls by 73,824 and the playable population stays 17,424.

Recommended physical shape: one row per person, with the 27 rating components as columns. That keeps the headline counts equal to people. A normalized rating table would multiply player rows by 27 components and by 2 for initial and potential (`18,456 × 27 × 2 = 996,624` shared rating cells) without adding people.

### Per timeline

| Moment | Player overlays | Staff overlays |
|--------|-----------------|----------------|
| After `start_timeline`, before the 1970 draft | 1,272 opening players | 200 initial staff |
| After the 1970 draft is materialized, before week 1 | 1,272 + 456 = **1,728** | 200 |
| After the 2029 draft, timeline complete | **17,424** | **1,026** |

Archive players add no timeline overlays.

| Timeline store, one finished 60-season run | Formula | Rows |
|--------------------------------------------|---------|------|
| Season lines, one per roster slot | sum of 24 × (active + taxi) × years in each era | **82,464** |
| Season lines if about 10% of player-seasons also need a second team line | 82,464 × 1.10 | about 90,700 |
| Draft picks | 13,272 draftable seats, all filled | **13,272** |
| Injuries | planning rate 30 per franchise per season: 30 × 24 × 60 | **43,200** |
| Contracts | planning mean of one contract per 2 roster-seasons: 82,464 / 2 | **41,232** |
| Awards | about 56 player honors per year × 60, plus coach and GM honors | about 3,500 |
| Staff stints | 24 franchises × 2 roles × (60 / 4-year mean tenure) | **720** |
| Directives | small; a few per franchise over 60 years | hundreds |

Roster-slot seasons by era:

| Era | Years | Slots per year | Slot-seasons |
|-----|-------|----------------|--------------|
| 1970–1977 | 8 | 24 × 48 = 1,152 | 9,216 |
| 1978–1986 | 9 | 24 × 53 = 1,272 | 11,448 |
| 1987–1994 | 8 | 24 × 55 = 1,320 | 10,560 |
| 1995–2003 | 9 | 24 × 61 = 1,464 | 13,176 |
| 2004–2013 | 10 | 1,464 | 14,640 |
| 2014–2029 | 16 | 1,464 | 23,424 |
| **Total** | **60** | | **82,464** |

One thousand finished timelines still share **18,456** player identities and **1,026** staff identities. They do not share season lines, picks, injuries, contracts, or awards. Overlay rows for a class are created when that timeline reaches the class, not at account creation. A timeline abandoned in 1970 holds 1,728 player overlays, not 17,424.

### If the last season were 2030

That is 61 seasons (1970–2030). Add one `modern-passing` class of 216 prospects and one year of timeline volume (1,464 roster-slot seasons, 168 draft picks, and the same per-season injury rate). Playable identities would be **17,640**. Shared pro history through 1969 would not change. This document does not use those 2030 figures. The working horizon is 2029.

### Obsolete 32-team estimate

The previous draft of this file reported **28,384** universe player rows. That number is retired. It is recorded here only so later edits do not treat it as current.

It was:

| Obsolete slice | Rows |
|----------------|------|
| 32 × (40 + 8) opening roster slots | 1,536 |
| Free agents (fixed 160) | 160 |
| Draft classes **1971–2049**, first draft was 1971, 32-team round sizes | 26,688 |
| **Total** | **28,384** |

The current playable count is **17,424**. The gap is the product change, not a rounding error:

- Franchises are 24, not 32, so each year's roster and each draft round are smaller.
- The horizon is 60 seasons, 1970–2029, not classes through 2049.
- The 1970 rookies are a draft class of 456. The old opening roster had already placed a full veteran-and-rookie roster and started drafting in 1971.
- The old total did not count an archive cohort. The current all-identity total, including the 1,032 archive players, is **18,456**, which is still the count for this 24-franchise, 60-season design.

---

## Invariants

A future implementation can turn these into tests. This document does not add them.

1. `start_timeline` does not change universe player, GM, or coach counts.
2. Two new timelines on one release have the same player keys, the same GM keys, the same coach keys, the same pre-1970 stat rows, the same 1970 pre-draft `(player, franchise, role)` tuples, the same free-agent pool, and the same 1970 prospect keys.
3. Those timelines have no 1970–2029 stat rows and no draft picks until the owner finishes the opening decisions and the season runs.
4. The 1970 draft class is the first class materialized. Materializing year Y creates no overlay whose class year is greater than Y.
5. A pick, stint, injury, contract, rating change, award, or 1970-or-later stat written on timeline A is invisible to timeline B and does not update universe tables.
6. Timeline `season_year` values lie in 1970–2029. Shared pro seasons lie in 1960–1969.
7. `season_year = 1969 + season_index`, and a complete timeline has `season_index` 60 and `season_year` 2029.
8. Each of the 100 initial GMs has a distinct strength/weakness/trait/history signature, and the same holds for the 100 initial head coaches.
9. Opening replacement hires are members of the unemployed 76. The other 23 franchises still employ their shared GM and head coach when the 1970 draft starts, unless a later rule explicitly allows poaching.
10. Campaign-style code does not insert `League`, `Conference`, `Division`, or `Team` rows and does not update `Team.league`.
11. A published release rejects edits to an existing `universe_key`.

---

## Fit with the current project

- External ids stay natural keys: `universe_key`, college slug, existing team slug.
- Generation is a management command. The 32-team admin view is the right pattern for a few dozen catalog rows and the wrong pattern for about 18,000 players.
- `League.clean()` and the single active tier-1 rule stay. Timelines reference that tier-1 league and do not become leagues.
- No new Django setting is required. The generator seed lives on the release. Production settings stay out of this work.
- The owner FK should use `settings.AUTH_USER_MODEL` when the model is eventually added. This design does not set `AUTH_USER_MODEL`.
- The live generator's `TEAM_COUNT = 32` disagrees with the 24-franchise snapshot. A release cannot be fingerprinted against that tree. Changing the generator is a later code change, not part of this document.

Suggested order when implementation is allowed:

1. Change the catalog tooling to 24 franchises (separate work). Then generate and publish one universe release: opening cohort, archive, 1970–2029 classes, 100 + incoming staff.
2. Timeline models and `start_timeline`, including the opening sequence through the 1970 draft and directives.
3. Season clock that materializes the next class and the next staff class each offseason, stopping after 2029.
4. Development, injuries, contracts, and awards, writing timeline rows only.

Scoring, schedules, payments, and public pages stay outside this design. Season lines are the durable stat record so a later box score can aggregate into them.

---

## Alternatives considered

**Keep the previous draft's 32-team, 1971-first, through-2049 population.** That is the retired 28,384-row model. It does not match a 24-franchise game, a 1970 rookie draft, or a 60-season run.

**Put 1970 rookies on the opening rosters and skip a preseason draft.** That makes every timeline's 1970 roster identical through week 1, including rookies. The opening sequence now includes the draft as an owner decision, so rookies stay in the shared class until that timeline drafts them.

**Generate replacement coaches inside each timeline.** Sixty seasons outlive the 1970 staff. Per-timeline identities would make two owners hire different fictional people. Incoming classes are shared instead, and the hiring choice is the timeline's.

**Store pre-1970 statistics on each timeline as a copied record book.** The lines would be identical forever. They stay on the universe, and the career page reads both stores.

---

## Unresolved decisions

These do not change the shared-universe versus per-timeline split. Several do change the row counts. The estimates above use the working value in each case.

| Decision | Working value | If it changes |
|----------|----------------|---------------|
| Final season label | **2029** (60 seasons starting in 1970). 2030 would be the 61st season. | One extra class of 216 and one extra season of timeline rows. Confirm before implementation. |
| How the 24 franchises sit in conferences | 2 × 3 divisions × 4 teams. Code on `main` still builds 32. | Count must stay 24 or the population formulas change. Layout does not, as long as the count holds. |
| Leagues below the 24 | None in the estimates. | Extra franchises add roster slots and, if they draft, extra prospects. |
| 1970 roster and later era limits | 40 + 8, then the era table. | Opening cohort and slot-seasons scale directly. |
| Free-agent pool and undrafted pool | 5 and 2 per franchise (120 and 48). | Linear in those constants. |
| Incoming staff | 8 head coaches and 6 GMs per year, 1971–2029. | Staff identity count only. |
| Opening hire pool | The 76 unemployed staff, not a coach already employed by another franchise. | Poaching before the draft would change a second franchise's staff in that timeline only. |
| Identical decisions, then the 1970 season | Season sim may use the timeline RNG, so results can diverge after kickoff. | A fully deterministic season would require the sim to ignore that seed when decisions match. |
| Dollar scale | Amounts are nominal dollars of the contract year. | An indexed scale changes display, not row counts. |
| College counting stats | Stored, 4 seasons per player, display only. | Dropping them removes 73,824 shared rows. |
| Archive mean career | 5 years inside 1960–1969, implying 1,032 archive players. | The generator should emit the real headcount; 11,520 season lines stay fixed if the historical league is 24 × 48 × 10. |
| Contract row estimate | One contract per two roster-seasons. | A different mean length changes the 41,232 figure only. |

---

## Document status

- Describes `leagues` as it exists on `main`, including the 32-team generator, and states the product shape of 24 franchises as a design target rather than as a code change.
- Replaces the previous draft's 32-team, 1971-first, through-2049 assumptions. The retired total of 28,384 is labeled obsolete above and is not the estimate to implement.
- Current shared playable population: **17,424** players. All player identities, including the archive: **18,456**. Staff identities: **1,026**. Final playable season under the working year rule: **2029**.
- Adds no application code, database models, migrations, fixtures, settings, or populated tables.
