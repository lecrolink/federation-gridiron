# Federation Gridiron — Shared Fictional Player Universe

This document specifies the shared fictional people of Federation Gridiron and the per-timeline state that diverges from them.

This revision replaces the earlier draft of this file. That draft assumed 32 franchises, a first draft in 1971, classes generated through 2049, and 28,384 universe player rows. Those figures are obsolete. The playable timeline is **60 seasons, 1970 through 2029 inclusive**. The league has 24 franchises in 1970, 28 from 1976 through 1995, and 32 from 1996 through 2029. That schedule is shared. Totals that still depend on roster size or draft-round defaults are marked as such in [Population and row counts](#population-and-row-counts). The retired 32-team, through-2049 arithmetic stays under [Obsolete 32-team estimate](#obsolete-32-team-estimate).

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

The confirmed schedule is 24 franchises for 1970–1975, 28 for 1976–1995, and 32 for 1996–2029. Names, cities, and permanent slugs are not chosen. The release uses placeholders until they are. This document does not change the generator and does not hard-code 24 as the size of every season.

A franchise identity is shared by every timeline on a release. The current `Team` row can store only one conference and one division, so it cannot by itself represent expansion, realignment, or a last active season. Those facts belong on the shared franchise records below. Timeline code does not create franchises and does not write `Team.league`, `Team.conference`, or `Team.division`.

Auth is Django's built-in user model. No `AUTH_USER_MODEL` override is set. The database is MySQL through `mysqlclient`. Domain writes go through `full_clean()` on `League` and through small `@transaction.atomic` services.

```text
leagues (exists, catalog)          universe (proposed, shared)           timelines (proposed, per user)
League → Conference                UniverseRelease                       Timeline
         → Division                Era, College                          owned franchise, directives
         → Team ←───────────────── OpeningAssignment                     RosterStint → Team
                                   OpeningContract                       TimelineContract
                                   OpeningStaffAssignment                StaffStint
                                   UniversePlayer / GM / Coach           overlays, current ratings
                                   Franchise + ExpansionEvent            expansion picks, hires
                                   pre-1970 histories and stats          1970–2029 stats, injuries,
                                   DraftClass 1970–2029                  awards, draft picks, contracts
```

Two future apps carry this, and neither is created here:

| App | Owns | Who writes it |
|-----|------|----------------|
| `universe` | One published release: players, GMs, coaches, 1970 rosters, free agents, draft classes, the expansion schedule, pre-1970 histories | A generator command, once per release |
| `timelines` | One owner's 60-season playthrough | Timeline services. They reference universe rows and never update them |

"Campaign" in the previous draft of this document is this timeline. The architecture is unchanged in kind: one shared universe, many independent playthroughs. The horizon, franchise count, opening draft, and staff model are what changed.

---

## Requirements

| Requirement | Where it lives |
|-------------|----------------|
| The game starts in 1970 with 24 franchises; 4 clubs enter in 1976 (28) and 4 enter in 1996 (32) | Shared expansion schedule. `N(1970–1975) = 24`, `N(1976–1995) = 28`, `N(1996–2029) = 32`. |
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
| **Universe release** | One immutable publication of the shared people, the 1970 league snapshot, the draft classes, and the expansion schedule. |
| **Timeline** | One owner's 60-season playthrough. The isolation boundary. Not a `leagues.League` row. |
| **Opening cohort** | Professionals already in the league before the 1970 rookie draft: the 24 founding rosters plus that year's free-agent pool. They are not members of the 1970 draft class. |
| **1970 rookie class** | The shared draft class used before the first season. |
| **Archive cohort** | Fictional players who appear in 1960–1969 league history and are gone before the 1970 draft. Shared, so the record book is the same in every timeline. They do not play in 1970. |
| **Initial staff** | 100 GMs and 100 head coaches available at the 1970 decision point. |
| **Incoming staff class** | Shared GM and head-coach candidates who become hireable in a later offseason. Same identities in every timeline. |
| **`N(year)`** | Active franchises that season. 24 for 1970–1975, 28 for 1976–1995, 32 for 1996–2029. |
| **Founding franchise** | One of the 24 clubs whose first active season is 1970. |
| **Expansion franchise** | A shared club whose first active season is later than 1970. The release names it before any timeline reaches that season. |

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

**The playable timeline is 60 seasons: 1970 through 2029 inclusive. The final playable season is 2029.**

2030 would be `season_index` 61 (`2030 − 1970 + 1 = 61`). It is not a playable season. Class size for each of the 60 years is `rounds(year) × N(year)` plus that year's undrafted pool. `N(year)` comes from the shared schedule, so the 60 classes stay identical across timelines even when some years have more than 24 teams.

Pre-1970 history uses years **1960–1969**. Those years are outside the 60. Timeline stat tables accept only `season_year` 1970–2029. Shared history tables accept only `season_year` 1960–1969.

The football clock is this integer year plus a phase. `TIME_ZONE` does not define it.

---

## Opening sequence

A new timeline is pinned to a published release and starts **before** the 1970 season, with the shared 1969 world still intact.

1. **Choose a franchise.** One of the 24 founding clubs. Stored as `Timeline.owned_franchise`. A normal timeline starts in 1970. In 1976 and 1996 the owner may stay or apply for an expansion club, as described in [Owner access to expansion](#owner-access-to-expansion).
2. **Keep or replace that franchise's GM and head coach.** The shared snapshot already employs one GM and one head coach on each franchise (24 of each). The other 76 GMs and 76 head coaches are unemployed and are the only legal opening hires. Replacing writes timeline staff stints only. The other 23 franchises keep their shared GM and head coach until later timeline events.
3. **Complete the 1970 rookie draft.** Materialize the shared 1970 class into this timeline. Draft order comes from the shared 1969 standings (worst record picks first). The owner drafts for the owned franchise. AI GMs draft for the others, starting from their shared tendencies. Picks are timeline rows. The class list itself is not modified.
4. **Set a few broad instructions** for the owned franchise's GM and head coach. Four GM directives and four coach directives, defined in the data dictionary. AI franchises start from directives derived from the shared staff profile, so every timeline begins with the same AI tendencies.
5. **GMs reduce rosters to the 1970 limit** (40 active + 8 taxi) after the draft. That reduction is a timeline result. The shared opening roster remains the pre-draft roster.
6. **Simulate the 1970 season** (14 games under the `run-first` era). Phases then follow the usual loop: offseason, next year's shared draft class, season, through the 2029 season. If the shared schedule says new franchises enter before a season, that offseason runs the expansion event, then that year's shared draft. After 2029 the phase is `complete`. No 2030 draft is generated.

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
10. Franchise identity, first and last active seasons, per-season conference and division membership, and expansion events are stored once on the release. Every timeline on that release introduces the same franchises in the same seasons.
11. A year's draft class is fixed by the release and by `N(year)` on that schedule. Standings inside a timeline change pick order. They do not add or remove prospects.
12. An expansion draft assigns people who are already in that timeline. It does not create a universe player, GM, or coach. Any extra rookies or free agents allowed in an expansion year are already members of that year's shared class or shared pool.

---

## Universe release and identity

| Field | Purpose |
|-------|---------|
| `slug` | Stable id, unique. Example: `fg-1970-1`. |
| `generator_version` | Algorithm id. Example: `fg-player-gen-2`. |
| `seed` | Integer stored on the release row. Not a Django setting. |
| `root_league` | FK to the tier-1 `League`. `on_delete=PROTECT`. |
| `founding_franchise_count` | 24. This is the 1970 size, not the size of later seasons. |
| `expansion_schedule_version` | Integer on the release. Filling in expansion years, clubs, or rules bumps the version. A published release does not change its schedule in place. |
| `catalog_fingerprint` | SHA-256 of the franchise list (slug, first season, last season) plus each season's conference and division membership plus each expansion event. A timeline stays bound to that fingerprint. |
| `first_season_year` | 1970. |
| `season_count` | 60. |
| `last_season_year` | 2029. |
| `status` | `draft` or `published`. |
| timestamps | Same pattern as `leagues`. |

`start_timeline` requires a published release whose fingerprint still matches the shared franchise list and expansion schedule. The current 32-team generator cannot satisfy that check. Publishing waits until the catalog can represent the release's franchises, including expansion clubs once the schedule names them. This document does not perform that catalog change. `N(year)` is computed from the schedule. It is not a settings constant and it is not `TEAM_COUNT`.

While `status` is `draft`, the generator may rebuild from `(generator_version, seed, fingerprint, era tables)`. After `published`, existing keys are frozen. Appending a year past `last_season_year` is a new product decision, not an implied extension.

Keys are opaque strings, max length 64. Release slugs contain hyphens, so clients look the row up instead of parsing the key.

```text
p-{release_slug}-opening-{index:05d}     veteran or free agent before the 1970 draft
p-{release_slug}-archive-{index:05d}     pro who left before the 1970 draft
p-{release_slug}-draft-{year}-{index:05d}
g-{release_slug}-{debut_year}-{index:03d}    GM
c-{release_slug}-{debut_year}-{index:03d}    coach (head coach or coordinator)
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

These roster and draft-round defaults are per team, not a league-size constant. They are not a claim that the game replays a real league's calendar. A season with more active franchises uses the same per-team quota and the same round count, multiplied by `N(year)`. Each era owns a position quota that sums to its active limit and another that sums to its taxi limit. The generator refuses a quota that does not sum. Later-era quotas are data, filled before publish. Direction across eras: FB counts fall, WR and CB counts rise, draft rounds shrink, linemen get heavier.

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

One component set for every era: **31** integers, 0–100, higher is better. Eras change how often high values are drawn. They do not add a column per decade. A displayed overall is derived from the components that matter for the player's position. The sim does not read that overall, and overall is not stored.

| Group | Components |
|-------|------------|
| Physical | `speed`, `acceleration`, `agility`, `strength`, `stamina`, `durability` |
| Mental | `awareness`, `discipline`, `processing` |
| Offense | `throw_power`, `throw_accuracy`, `catching`, `route_running`, `carrying`, `elusiveness`, `run_blocking`, `pass_blocking` |
| Defense | `tackling`, `coverage`, `pass_rush`, `run_defense` |
| Special teams | `kick_power`, `kick_accuracy`, `punt_power`, `punt_accuracy` |
| Personality | `work_ethic`, `leadership`, `temperament`, `clutch`, `consistency`, `adaptability` |

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
N(1970) = 24
rostered = N(1970) × (40 + 8) = 1,152
free agents = 5 × N(1970) = 120
opening cohort = 1,272
```

That 1970 arithmetic is fixed, because the league starts with 24 clubs. It is not reused as `N(year)` for 1971–2029.

Every opening player has at least one prior pro season. Age is drawn first (starters mostly 24–30, backups 22–32, taxi 21–24, free agents slightly older). `birth_year = 1970 − age` at the 1970 offseason. Rookies born around 1948 are in the draft class, not on these rosters.

`OpeningAssignment`: one per opening player. `team` is null for free agents. `position` is stored on the assignment and must match the player. `depth` is the depth-chart index for that position on that team, starting at 1, across starter, backup, and taxi. Unique `(release, team, position, depth)`. MySQL unique indexes treat NULL `team` as distinct per row, so the free-agent pool does not need a partial index.

`OpeningContract`: one shared template per rostered veteran (1,152). Free agents have none. Copied into the timeline at start.

### 1970–2029 draft classes

`DraftClass` is unique on `(release, year)` for each year from 1970 through 2029.

```text
draftable(year) = era(year).draft_rounds × N(year)
undrafted(year) = 2 × N(year)
class size(year) = draftable(year) + undrafted(year)
```

`N(year)` is the shared schedule, so every timeline on the release receives the same class. Round counts and the two undrafted prospects per team are still design defaults. The row counts below are exact only while those defaults hold.

| Years | N | Rounds | Draftable | Undrafted | Class size | Years | Prospects |
|-------|---|--------|-----------|-----------|------------|-------|-----------|
| 1970–1975 | 24 | 17 | 2,448 | 288 | 456 | 6 | 2,736 |
| 1976–1977 | 28 | 17 | 952 | 112 | 532 | 2 | 1,064 |
| 1978–1986 | 28 | 12 | 3,024 | 504 | 392 | 9 | 3,528 |
| 1987–1994 | 28 | 8 | 1,792 | 448 | 280 | 8 | 2,240 |
| 1995 | 28 | 7 | 196 | 56 | 252 | 1 | 252 |
| 1996–2003 | 32 | 7 | 1,792 | 512 | 288 | 8 | 2,304 |
| 2004–2013 | 32 | 7 | 2,240 | 640 | 288 | 10 | 2,880 |
| 2014–2029 | 32 | 7 | 3,584 | 1,024 | 288 | 16 | 4,608 |
| **1970–2029** | | | **16,028** | **3,584** | | **60** | **19,612** |

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

Each of the 100 in a role has a distinct combination of strength tags, weakness tags, trait tags, and career-history signature. Ages in 1970 run about 32–65. Employed head coaches skew older. The unemployed 76 are head-coach candidates. Some of them held coordinator posts before 1970. They are not the coordinator pool. Coordinators are additional people, specified under [Coordinators](#coordinators).

**Incoming GM and head-coach classes, 1971–2029.** `E(1976) = 4`, `E(1996) = 4`, and `E(year) = 0` in every other year. The floor is:

```text
incoming_hc(year) >= 8 + E(year)
incoming_gm(year) >= 6 + E(year)
```

That is 8 head coaches and 6 GMs in 57 ordinary years, and 12 head coaches and 10 GMs in 1976 and 1996.

```text
future head coaches = 57 × 8 + 2 × 12 = 480
future GMs = 57 × 6 + 2 × 10 = 362
all head coaches = 100 + 480 = 580
all GMs = 100 + 362 = 462
GM and head-coach identities = 1,042
```

The 8 and 6 are still a staffing assumption. The `E(year)` addend is the minimum so each new club has a shared candidate. Adding more candidates above the floor increases the shared staff count and does not change the franchise schedule.

A timeline creates overlay rows for the initial 200 at `start_timeline`, because the owner may hire any unemployed member before the season. An incoming staff class is materialized in the offseason of its debut year, the same way a draft class is. New staff are not invented inside one timeline, so two timelines are hiring from the same people.

Initial staff draw schemes that exist in 1970: `power_run`, `option`, `pro_style` on offense, and `stop_run` or `balanced` on defense. A coach who debuts in a later class may draw that era's schemes (`west_coast` from 1987, `air_raid` from the spread era).

### Shared 1969 standings

`UniverseFranchiseSeason` for each founding franchise for each year 1960–1969 (24 × 10 = 240 rows). Expansion clubs have no 1960–1969 record. The 1969 wins, losses, and ties set the 1970 draft order. Later draft orders come from that timeline's own standings. Expansion clubs follow the rookie-draft slot rule on their expansion event; that rule changes order only, not the shared class.

---

## Expansion

The Federation Gridiron begins with 24 franchises in 1970. Four expansion franchises enter for the 1976 season, and four more enter for the 1996 season. From 1996 through 2029 the league has 32 active franchises unless a later decision explicitly adds a departure, a relocation, or another expansion. No departure is scheduled.

| Seasons | Active franchises | What changes |
|---------|-------------------|--------------|
| 1970–1975 | 24 | Founding league |
| 1976–1995 | 28 | 4 clubs enter in 1976 |
| 1996–2029 | 32 | 4 clubs enter in 1996 |

Placeholders, not permanent identities:

| Placeholder slug | First season | Display name until a real name is chosen |
|------------------|--------------|------------------------------------------|
| `founding-01` … `founding-24` | 1970 | Founding franchise 01–24 |
| `expansion-1976-01` … `expansion-1976-04` | 1976 | 1976 expansion franchise 01–04 |
| `expansion-1996-01` … `expansion-1996-04` | 1996 | 1996 expansion franchise 01–04 |

City, nickname, and final slug stay empty until chosen. Every timeline on the release uses these same placeholders and the same entry years.

`N(year)` is the number of franchises whose `first_active_season` is less than or equal to `year` and whose `final_active_season` is null or greater than or equal to `year`.

```text
N(1970) = 24
E(year) = number of franchises with first_active_season = year
N(year) = N(year - 1) + E(year) − D(year)
```

`D(year)` is the number of franchises whose `final_active_season` is `year - 1`. No contraction is scheduled, so `D(year) = 0`. `E(1976) = 4`, `E(1996) = 4`, and `E(year) = 0` otherwise. Franchise-seasons across the 60 years are `6 × 24 + 20 × 28 + 34 × 32 = 1,792`.

### Conference and division alignment

Conferences stay `conference-1` and `conference-2` for the whole timeline. Division slugs change when the league size changes, because 24, 28, and 32 do not share one equal division size. Membership is shared. Timelines do not realign on their own.

| Seasons | Shape | Slots |
|---------|--------|-------|
| 1970–1975 | 2 conferences × 3 divisions × 4 teams | `c1-d1`, `c1-d2`, `c1-d3`, `c2-d1`, `c2-d2`, `c2-d3` |
| 1976–1995 | 2 conferences × 2 divisions × 7 teams | `c1-d1`, `c1-d2`, `c2-d1`, `c2-d2` |
| 1996–2029 | 2 conferences × 4 divisions × 4 teams | `c1-d1` … `c1-d4` and `c2-d1` … `c2-d4` |

No geographic names are assigned. `c1-d3` means the third division of conference 1, not a region. `slot_order` is an integer on the membership row. It is the order in the tables below. It is not a city, and after 1970 it is not alphabetical order of the slug.

**1970 through 1975.** Twelve founding clubs in each conference, three divisions of four.

| Division | Order 1 | Order 2 | Order 3 | Order 4 |
|----------|---------|---------|---------|---------|
| `c1-d1` | founding-01 | founding-02 | founding-03 | founding-04 |
| `c1-d2` | founding-05 | founding-06 | founding-07 | founding-08 |
| `c1-d3` | founding-09 | founding-10 | founding-11 | founding-12 |
| `c2-d1` | founding-13 | founding-14 | founding-15 | founding-16 |
| `c2-d2` | founding-17 | founding-18 | founding-19 | founding-20 |
| `c2-d3` | founding-21 | founding-22 | founding-23 | founding-24 |

**1976 realignment.** `c1-d3` and `c2-d3` are retired. Each conference keeps its 12 founding clubs and adds 2 expansion clubs. Orders 1 and 2 of the retired division join that conference's first division as orders 5 and 6. Orders 3 and 4 join the second division as orders 5 and 6. One expansion club joins each surviving division as order 7. Every founding club stays in its original conference. Each surviving division then has 6 founding clubs and 1 expansion club.

| Division | Orders 1–4, unchanged | Order 5 | Order 6 | Order 7 |
|----------|----------------------|---------|---------|---------|
| `c1-d1` | founding-01 … founding-04 | founding-09 | founding-10 | expansion-1976-01 |
| `c1-d2` | founding-05 … founding-08 | founding-11 | founding-12 | expansion-1976-02 |
| `c2-d1` | founding-13 … founding-16 | founding-21 | founding-22 | expansion-1976-03 |
| `c2-d2` | founding-17 … founding-20 | founding-23 | founding-24 | expansion-1976-04 |

**1996 realignment.** Each conference has 14 continuing clubs and adds 2 expansion clubs. Orders 1–4 of each 1976 division stay in that division, which returns to 4 clubs. The mover list is the first surviving division's orders 5, 6, and 7, then the second surviving division's orders 5, 6, and 7, then that conference's two 1996 expansion clubs. The first four of that list form division 3. The last four form division 4. Continuing clubs stay in their conference. A club that moved in 1976 can move again in 1996. `founding-09` plays in `c1-d3` through 1975, `c1-d1` from 1976 through 1995, and `c1-d3` again from 1996 on.

| Division | Order 1 | Order 2 | Order 3 | Order 4 |
|----------|---------|---------|---------|---------|
| `c1-d1` | founding-01 | founding-02 | founding-03 | founding-04 |
| `c1-d2` | founding-05 | founding-06 | founding-07 | founding-08 |
| `c1-d3` | founding-09 | founding-10 | expansion-1976-01 | founding-11 |
| `c1-d4` | founding-12 | expansion-1976-02 | expansion-1996-01 | expansion-1996-02 |
| `c2-d1` | founding-13 | founding-14 | founding-15 | founding-16 |
| `c2-d2` | founding-17 | founding-18 | founding-19 | founding-20 |
| `c2-d3` | founding-21 | founding-22 | expansion-1976-03 | founding-23 |
| `c2-d4` | founding-24 | expansion-1976-04 | expansion-1996-03 | expansion-1996-04 |

The 1996 shape is 2 conferences and 8 divisions of 4, which is the shape `league_generator.py` already builds as a 32-team tree. That generator still cannot represent 1970 or 1976. This document does not change it.

The schedule is shared universe data and is versioned with the release. Every timeline on that release activates the same franchises in the same seasons, in the same conferences and divisions. A timeline cannot vote a club into the league or change `N(year)`. After publish, editing the schedule means a new release. Timelines already pinned to the old release keep the old schedule.

### Shared franchise

One row per club that will ever play in the release. All 32 placeholder franchises exist when the release is generated. Expansion clubs are not created when the first timeline reaches 1976 or 1996.

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `slug` | Shared | slug | unique in the release | Schedule author | Both |
| `name` | Shared | string | ≤ 255, name at debut | Schedule author | Display |
| `first_active_season` | Shared | smallint | 1970 for the founding 24; 1971–2029 for expansion | Schedule author | Both |
| `final_active_season` | Shared | smallint, null | null means still active through 2029; otherwise 1970–2029 | Schedule author | Both |
| `origin` | Shared | enum | `founding` or `expansion` | Schedule author | Display |
| `history_predecessor` | Shared | FK, null | another franchise in the release, for a later relocation or reorganization link | Schedule author, unused until needed | Display |

`history_predecessor` is the hook for relocation, renaming, or a club that continues another club's history. No such links are defined. A rename or city change that keeps the same franchise uses `FranchiseHistoryEvent` rather than a new slug.

### Membership by season

One shared row per franchise per season in which it exists. This is the alignment the schedule promises. It is not the result of a timeline's standings.

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `franchise` | Shared | FK | | Schedule author | Both |
| `season_year` | Shared | smallint | 1970–2029, inside the franchise's active span | Schedule author | Both |
| `conference_slug` | Shared | slug | conference for that season | Schedule author | Both |
| `division_slug` | Shared | slug | division for that season | Schedule author | Both |
| `slot_order` | Shared | smallint | 1–4 in 1970–1975 and 1996–2029; 1–7 in 1976–1995 | Schedule author, from the alignment tables | Both |
| `owner_selectable` | Shared | bool | whether a human owner may hold or apply for this club in this season | Schedule author | Both |

Founding clubs are owner-selectable in 1970. Each expansion club is owner-selectable only in its first season, which is when an existing owner may apply for it. Every other membership row sets the flag false. Realignment is a new membership row for the later season, using the alignment tables above. A timeline does not recompute conference, division, or `slot_order` from standings.

### History events

Optional shared rows. None are required for the founding 24.

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `franchise` | Shared | FK | | Schedule author | Display |
| `season_year` | Shared | smallint | 1970–2029 | Schedule author | Display |
| `kind` | Shared | enum | `rename`, `relocation`, `reorganization` | Schedule author | Display |
| `previous_name` | Shared | string | ≤ 255 | Schedule author | Display |
| `new_name` | Shared | string | ≤ 255 | Schedule author | Display |
| `city` | Shared | string | ≤ 255, empty if unchanged | Schedule author | Display |
| `notes` | Shared | text | | Schedule author | Display |

### Expansion event

Two expansion events are part of this release. The 1976 event adds `expansion-1976-01` through `expansion-1976-04`. The 1996 event adds `expansion-1996-01` through `expansion-1996-04`. A season may add more than one franchise. The numeric draft and hiring rules below are still empty. A published release cannot leave them empty.

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `season_year` | Shared | smallint | 1971–2029, the first season the new clubs play | Schedule author | Both |
| `franchises` | Shared | list of franchise FKs | the clubs with `first_active_season` equal to this year | Schedule author | Both |
| `placement` | Shared | membership rows | conference and division for each new club in `season_year` | Schedule author | Both |
| `players_protected` | Shared | smallint, null until set | how many players each existing franchise may protect | Schedule author | Sim |
| `picks_per_new_franchise` | Shared | smallint, null until set | selections in the expansion draft | Schedule author | Sim |
| `expansion_draft_order_rule` | Shared | text | how selection order among the new clubs is chosen | Schedule author | Sim |
| `roster_building_rule` | Shared | text | how a new club fills the rest of the roster after the expansion draft | Schedule author | Sim |
| `free_agent_rule` | Shared | text | which shared free-agent pool the new club may sign, and any limits | Schedule author | Sim |
| `rookie_draft_slot_rule` | Shared | text | lottery, fixed slot, or other order rule for the new clubs in that year's rookie draft | Schedule author | Sim |
| `gm_hiring_rule` | Shared | text | which shared GM identities are eligible, and whether the owner or the AI hires | Schedule author | Sim |
| `hc_hiring_rule` | Shared | text | same for head coaches | Schedule author | Sim |

Blank rule text means the schedule is not ready to publish. A published release that contains an expansion event has those rules filled in. Publishing with a blank event is refused.

### What stays shared, and what a timeline decides

The new franchise, its GM candidates, its head-coach candidates, and that year's rookie class are the same people in every timeline. The class was generated from `N(year)`, which already includes `E(year)`, when the release was built. An expansion draft does not generate players.

| Fact | Where it lives |
|------|----------------|
| Which clubs enter, and in which season | Shared expansion event |
| Conference and division | Shared membership rows |
| How many veterans an existing club may protect | Shared `players_protected` |
| Which veterans are protected | Timeline. Depends on that timeline's roster. |
| Which unprotected players the new club selects | Timeline expansion picks |
| Which eligible shared GM and head coach are hired | Timeline staff stints |
| Contracts the new club signs | Timeline contracts |
| Where the new club picks in the rookie draft | Timeline pick order, using the shared slot rule and that timeline's lottery inputs if the rule calls for them |
| Who is in the rookie class | Shared. Not affected by the lottery or by earlier timeline results. |

`TimelineExpansionPick`:

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `expansion_event` | Timeline | FK | | Shared schedule | Both |
| `pick_number` | Timeline | smallint | 1 through `picks_per_new_franchise × E(year)` | Timeline sim or owner, if the owner controls a new club | Both |
| `selecting_franchise` | Timeline | FK | one of the event's new clubs | same | Both |
| `player` | Timeline | FK | a universe player already in this timeline | same | Both |
| `from_franchise` | Timeline | FK | the club that lost the player | same | Both |

The pick updates roster stints in that timeline only. The universe player row is unchanged. Two timelines can give the same expansion club different veterans because their earlier seasons produced different rosters.

Supplemental rookies or free agents, if a rule allows them, are drawn from that year's shared draft class or from the shared free-agent pool already sized with `N(year)`. They are not a private list generated when the timeline runs the expansion draft.

### Owner access to expansion

A normal timeline still begins in 1970 on a founding franchise. In the 1976 offseason and again in the 1996 offseason, before the expansion draft, the owner makes a few choices. The GM and head coach carry out the detail.

1. **Stay or apply.** The owner keeps the current franchise, or applies for one expansion franchise. Applying is the only pursuit action. There is no separate purchase price in this design.
2. **If the owner stays,** they name up to `players_protected` core players to protect. The GM protects the rest of the allowed list and runs the existing roster.
3. **If the owner is awarded an expansion franchise,** `owned_franchise` changes. The former franchise becomes bot-controlled, with its current GM, coach, and roster. The new franchise starts with no players and no staff.
4. **Building the new franchise.** The owner sets broad priorities only: which positions to favor in the expansion draft, and the same directive enums already used for the GM and coach (`draft_approach`, `veteran_preference`, `free_agency`, `offensive_emphasis`, `defensive_emphasis`). The GM and head coach, once hired from the shared candidate pool, execute the expansion draft, free agency, the rookie draft, and the depth chart. The owner does not pick every reserve.

The new club's players, GM, and head coach are people who already exist on the release. The expansion draft only chooses among veterans already in that timeline. The rookie class that year is the shared class sized with `N(1976) = 28` or `N(1996) = 32`.

**Acceptance is not fully decided.**

| Situation | Proposed handling |
|-----------|-------------------|
| Single-player timeline, owner applies for one of the four clubs | Automatic acceptance. The other three expansion clubs are bot-run. |
| Several human owners want the same expansion club | A fair selection among the applicants: a random draw using a seed stored with the league, not first-click wins. Each human can be awarded at most one expansion club. Leftover clubs are bot-run. |
| Owner does not apply | They remain with the current franchise. Bots run every expansion club. |

Whether a separate game mode may start a timeline already in 1976 or 1996, skipping 1970, remains unresolved. The default mode does not.

---

## Data dictionary

The approval checklist, including visibility and how values change, is [Characteristic audit](#characteristic-audit). Columns on every storage table:

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
| `secondary_positions` | Shared | 0–2 enums | same position list, excluding `position` | Generator | Both |
| `handedness` | Shared | enum | `left` `right` | Generator | Display |
| `height_in` | Shared | smallint | 64–84 inches | Generator, era distribution | Both |
| `weight_lb` | Shared | smallint | 160–360 pounds, by position and era | Generator | Both |
| `cohort` | Shared | enum | `opening` `archive` `draft` | Generator | Both |
| `first_pro_year` | Shared | smallint | 1960–1969 for opening and archive players; the class year for a draftable prospect; null for the undrafted pool, whose actual debut is a timeline fact | Generator | Both |
| `origin_draft_year` | Shared | smallint | ≤ 1969 for opening/archive; class year for the draft cohort; null if the player entered as an undrafted historical free agent | Generator | Display |
| `origin_draft_round` | Shared | smallint | 1–17, or null | Generator | Display |
| `origin_draft_pick` | Shared | smallint | 1 through that historical class's draftable count, or null | Generator | Display |
| `origin_draft_franchise` | Shared | FK | catalog team, or null | Generator | Display |
| `era` | Shared | FK | origin era | Generator | Both |
| `peak_age` | Shared | smallint | 24–32 | Generator | Sim |
| `development_rate` | Shared | smallint | 1–100 | Generator | Sim |
| `decline_rate` | Shared | smallint | 1–100 | Generator | Sim |
| `board_rank` | Shared | smallint | 1–class size; draft cohort only | Generator | Both |

`origin_draft_*` on an opening or archive player is the historical class he already entered. It is not a seat in the 1970 draft. The 1970 class has its own player rows. On every 1970–2029 prospect, `origin_draft_year` is the class year and `origin_draft_round`, `origin_draft_pick`, and `origin_draft_franchise` stay null. Filling those three would bake one timeline's draft result into the shared identity. `board_rank` is the shared public board, not the result.

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
| `kick_power`, `kick_accuracy` | Kicker distance and make rate. Near a low baseline for everyone else. |
| `punt_power`, `punt_accuracy` | Punter distance and placement. Near a low baseline for everyone else. |
| `work_ethic`, `leadership` | Development and teammate effects |
| `temperament` | Penalties, durability of performance after errors |
| `clutch` | Late-game and high-leverage modifier |
| `consistency` | How wide a single-game swing is around the current ratings |
| `adaptability` | How quickly current ratings and usage recover after a scheme or position change |

Non-position skills are still stored, at a low baseline, so development code can stay position-agnostic.

### Player traits that are not ratings

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `trait_tags` | Shared | set of enum | 0–3 tags from `film_junkie`, `gym_rat`, `hothead`, `quiet_pro`, `mentor`, `big_play` | Generator | Display, plus a small sim modifier for `hothead` and `mentor` |

Injury susceptibility is `durability`, not a second tag. Scheme fit is not stored on the player. The sim derives it from `adaptability`, the skill vector, and the coach's scheme. Tags are fixed for the life of the release. `hothead` adds penalty chance. `mentor` adds a small development bonus to younger teammates. The other tags are display.

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
| `current` ratings | Timeline | 31 × smallint | 0–100, ≤ potential | Copied from `initial`, then Timeline sim | Both. Hidden as exact numbers. The owner sees `TimelineScoutComponent` until evaluation sets uncertainty to 0. |
| `seasons_played` | Timeline | smallint | 0–60, pro seasons inside this timeline only | Derived, may be cached | Display |
| `career_altering_injuries` | Timeline | smallint | 0–10 | Timeline sim | Both |

Unique `(timeline, player)`.

#### Scout report

`TimelineScoutReport` is one row per overlay. `TimelineScoutComponent` is one row per component per kind (`ability` or `potential`). The sim never reads these rows. Field-by-field visibility is in the characteristic audit. Owned-roster reports start with uncertainty 20 and reach 0 after four weeks on the roster or 100 snaps, whichever comes first. Other players start between 25 and 60. Potential often stays uncertain. When ability uncertainty hits 0, `revealed_value` is set to `current`.

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
delta = f(gap, age vs peak_age, snaps, durability, injury weeks, coaching, timeline rng)
current = clamp(current + delta, 0, potential)
```

`consistency` does not change this delta. It widens or narrows the per-game draw around `current`. `adaptability` shortens the number of games after a scheme or secondary-position change before usage recovers. `durability` changes injury frequency and `weeks_out`. Era does not edit `current` on an existing player. A career-altering injury may drop one or more components immediately, still clamped at 0 and at `potential`. The full visibility and change rules are in [Characteristic audit](#characteristic-audit).

---

### General managers

One shared row per GM (`UniverseGM`), plus a timeline overlay (`TimelineGM`) holding `current` ratings and employment.

`negotiation` is trade negotiation only. Contract length, guarantees, and cap fit are `cap_management`. `patience` is a rating on every GM. The `patient` and `impatient` traits only break ties.

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
| `negotiation` | same | smallint | 0–100. Trade negotiation only. | same | Both |
| `cap_management` | same | smallint | 0–100. Contract length, guarantees, and cap fit. | same | Both |
| `draft_strategy` | same | smallint | 0–100 | same | Both |
| `free_agency` | same | smallint | 0–100 | same | Both |
| `roster_construction` | same | smallint | 0–100 | same | Both |
| `player_development` | same | smallint | 0–100 | same | Both |
| `discipline` | same | smallint | 0–100 | same | Both |
| `leadership` | same | smallint | 0–100 | same | Both |
| `risk_tolerance` | same | smallint | 0–100 | same | Both |
| `patience` | same | smallint | 0 impatient, 100 patient | same | Both |
| `coach_evaluation` | same | smallint | 0–100 | same | Both |
| `veteran_preference` | same | smallint | 0 youth, 100 veterans | same | Both |
| `pick_trade_willingness` | same | smallint | 0–100 | same | Both |
| `reputation` | Shared `initial`; Timeline `current` | smallint | 0–100 | Generator from pre-1970 posts, then Timeline sim | Both |
| `positional_priority` | Shared | 1–2 enums | `quarterback` `skill` `offensive_line` `defensive_line` `linebacker` `defensive_back` `special_teams` | Generator | Both |

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
| `ended_by` | Shared | enum | `resigned` `fired` `promoted` `retired` `post_completed` | Generator | Display |

Timeline employment is `StaffStint`:

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `role` | Timeline | enum | `gm` | Copied from `OpeningStaffAssignment`, then Owner or Timeline sim | Both |
| `franchise` | Timeline | FK, nullable | null if unemployed | same | Both |
| `start_year`, `end_year` | Timeline | smallint | 1970–2029; `end_year` null while open | same | Both |
| `reason_ended` | Timeline | enum | `opening_replace` `fired` `retired` `hired_elsewhere` `promoted` `timeline_complete` | Owner or Timeline sim | Display |

The owner's opening replace sets `reason_ended = opening_replace` on the displaced GM and opens a stint for the hire, both before the 1970 draft. Coach stints use the same fields. Their `role` is `head_coach`, `offensive_coordinator`, `defensive_coordinator`, or `position_coach`. `promoted` closes a coordinator stint when that person becomes head coach. Wins, losses, ties, playoff appearances, and championships during a timeline stint are derived from that timeline's standings. They are not stored a second time. `TimelineStaffScoutReport` uses the player scout shape for GM and coach abilities, including reputation. AI franchises read true `current` and ignore the owner's fog.

---

### Head coaches

Coaches are one shared person table. Head coaches, offensive coordinators, defensive coordinators, and position coaches are roles on a stint, not separate identities. Key prefix `c-`.

#### Identity

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `universe_key` | Shared | string | `c-…`, unique | Generator | Both |
| `name` | Shared | string | ≤ 255 | Generator | Display |
| `birth_year` | Shared | smallint | initial head coaches about 1905–1938, so ages in 1970 are 32–65. Initial coordinators may be about 28–55. Incoming head coaches are about 40 at debut; incoming coordinators about 35. | Generator | Both |
| `debut_year` | Shared | smallint | 1970 for the initial pools; 1971–2029 for incoming classes | Generator | Both |
| `former_role` | Shared | enum | `player` `college_coach` `coordinator` `position_coach` | Generator | Display |
| `specialty` | Shared | enum | `offense` `defense` `both` | Generator | Both |

The initial 100 head coaches have `specialty = both`. Offensive coordinators are `offense`. Defensive coordinators are `defense`. Specialty limits the coordinator chair. It does not change when the person is promoted to head coach.

#### Ability

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `offensive_scheme` | Shared `initial`/`potential`; Timeline `current` | smallint | 0–100 | Generator, then Timeline sim | Both |
| `defensive_scheme` | same | smallint | 0–100 | same | Both |
| `game_management` | same | smallint | 0–100 | same | Both |
| `player_development` | same | smallint | 0–100 | same | Both |
| `motivation` | same | smallint | 0–100 | same | Both |
| `leadership` | same | smallint | 0–100. Locker-room authority, separate from week-to-week motivation. | same | Both |
| `discipline` | same | smallint | 0–100 | same | Both |
| `in_game_adjustments` | same | smallint | 0–100 | same | Both |
| `adaptability` | same | smallint | 0–100. How fast the season plan changes when it is failing. | same | Both |
| `aggressiveness` | same | smallint | 0–100 | same | Both |
| `staff_management` | same | smallint | 0–100 | same | Both |
| `clock_management` | same | smallint | 0–100 | same | Both |
| `preparation` | same | smallint | 0–100 | same | Both |
| `fatigue_management` | same | smallint | 0–100 | same | Both |
| `injury_management` | same | smallint | 0–100. Rest and return from injury. | same | Both |
| `fourth_down_aggression` | same | smallint | 0 conservative, 100 aggressive | same | Both |
| `veteran_preference` | same | smallint | 0 youth, 100 veterans | same | Both |
| `reputation` | Shared `initial`; Timeline `current` | smallint | 0–100 | Generator from pre-1970 posts, then Timeline sim | Both |
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

Opening assignment: `OpeningStaffAssignment (release, staff, franchise, role)` for the 24 employed GMs, 24 employed head coaches, 24 offensive coordinators, and 24 defensive coordinators. Unique `(release, franchise, role)` and unique `(release, staff)`.

### Coordinators

Coordinators belong in the initial database even though the first playable version only asks the owner to hire a GM and a head coach. A later firing has to promote someone who already exists. Adding coordinators in a second schema would split staff history.

Use one coaching person record for head coaches, offensive coordinators, defensive coordinators, and position coaches. The rating block is the head-coach block. `specialty` is `offense`, `defense`, or `both`. A stint's `role` is `head_coach`, `offensive_coordinator`, `defensive_coordinator`, or `position_coach`. Promotion is a new stint on the same person, not a new identity.

Recommended 1970 coordinator pool, same identities in every timeline:

| | Employed on a founding club | Unemployed |
|--|-----------------------------|------------|
| Offensive coordinator | 24 | 16 |
| Defensive coordinator | 24 | 16 |

Incoming floor, 1971–2029: `4 + E(year)` offense-specialty candidates and `4 + E(year)` defense-specialty candidates. With `E` only in 1976 and 1996, that is 244 per specialty and **488** incoming coordinators. Initial 80 plus 488 is **568** coordinator identities. GM and head-coach identities stay **1,042**. All staff identities in the recommended database are **1,610**.

Version 1 need not show coordinator hiring. The rows are still generated with the release so a head-coach firing can promote that club's coordinator by closing his stint with `promoted` and opening a `head_coach` stint on the same `universe_key`. An expansion club can hire from the shared coordinator pool. Do not generate a separate position-coach labor pool. The `position_coach` role exists so historical posts, and a later promotion, use this same table. Generating only head coaches now and adding coordinators in a second schema would split staff history.

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
| `owned_franchise` | Timeline | FK | a franchise the release marks owner-selectable for this timeline's start; in 1970 that is one of the 24 founding clubs | Owner | Both |
| `name` | Timeline | string | ≤ 255 | Owner | Display |
| `season_index` | Timeline | smallint | 1–60 | Timeline sim | Both |
| `season_year` | Timeline | smallint | 1970–2029, equals `1969 + season_index` | Derived | Both |
| `phase` | Timeline | enum | see opening sequence | Timeline sim | Both |
| `rng_seed` | Timeline | int | drawn at creation | Generator of the timeline, not the universe seed | Sim |
| `start_season_year` | Timeline | smallint | `1970`, `1976`, or `1996`. The only supported value until a product decision says otherwise is `1970`. | Owner, default 1970 | Both |

`start_season_year` is stored so a later mode can begin in an expansion year without a new table. This document does not define that mode's skipped seasons. A normal timeline uses 1970.

#### Expansion choice

`TimelineExpansionChoice`, one row per timeline per expansion year, written before that year's expansion draft.

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `season_year` | Timeline | smallint | 1976 or 1996 | The schedule | Both |
| `decision` | Timeline | enum | `stay` `apply` | Owner | Both |
| `target_franchise` | Timeline | FK, null | one expansion club that year, required when the decision is `apply` | Owner | Both |
| `protected_players` | Timeline | list of player FKs | up to `players_protected`, only when the decision is `stay` | Owner names the core. The GM fills the rest of the protection list. | Sim |
| `draft_position_priorities` | Timeline | 0–2 enums | the GM `positional_priority` vocabulary, only when the decision is `apply` | Owner | Sim |
| `outcome` | Timeline | enum | `pending` `awarded` `declined` `stayed` | Timeline sim | Both |
| `selection_method` | Timeline | enum | `not_needed` `automatic` `fair_draw` | Timeline sim | Display |

The owner does not set the rest of the roster, the contract figures, or the depth chart. The GM and head coach do. In a single-player timeline, `apply` is `automatic`. When several human owners apply for the same club, the method is unresolved; `fair_draw` is the candidate, using a seed stored with that league rather than first-click. Each human can be awarded at most one expansion club.

---

## Historical consistency

Every shared person has the same identity and the same pre-timeline history in every timeline on a release.

These values are generated once and never diverge:

| Kind of fact | Examples |
|--------------|----------|
| Identity | `universe_key`, name, birth year, birth day, college, height, weight, handedness, primary position, secondary positions |
| Original talent | `initial` and `potential` on every rating component, `peak_age`, `development_rate`, `decline_rate`, trait tags |
| Entry facts that are biography | cohort, origin era, `board_rank`, class membership, `origin_draft_year`. For opening and archive players, also `origin_draft_round`, `origin_draft_pick`, and `origin_draft_franchise`. For a 1970–2029 prospect those three stay null, because the destination is a timeline result. |
| Life before the timeline starts | College seasons, 1960–1969 pro seasons, 1960–1969 awards, 1960–1969 staff posts, the 1970 opening roster and opening contract templates, opening staff assignments, 1969 standings |
| League structure | Franchise placeholders, `N(year)`, conference, division, `slot_order`, expansion years |

`first_pro_year` on a draftable prospect is the class year he is eligible. It is the same in every timeline. Whether he is actually drafted, and the year he takes a snap, are timeline facts.

These values may diverge after timeline decisions begin:

| Kind of fact | What moves it |
|--------------|----------------|
| `current` ratings | Age against `peak_age`, snaps, coaching, career-altering injuries, and the timeline RNG. Clamped to `potential`. |
| Scout reports | Film, workouts, and roster evaluation in that timeline. The sim does not read them. |
| Assignment | Drafts, cuts, trades, free agency, the expansion draft, retirement |
| Health | Injury rows and `career_altering_injuries` |
| Money | Timeline contracts. The 1970 template is only the starting copy. |
| Counting stats, awards, development events | 1970–2029 play in that timeline |
| Staff jobs | Hiring, firing, promotion, retirement. Pre-1970 posts stay shared. |
| Reputation | Results, awards, and firings inside the timeline. The shared `initial` reputation does not change. |
| Directives and expansion choice | Owner decisions |
| Season results | Timeline RNG after kickoff, even when two owners make the same opening choices |

A particular player always enters the 1970 draft, if he is in that class, with the same name, college, body, college statistics, original talent, and scouting profile. A GM or coach always begins with the same biography, abilities, employment history, and reputation. Drafting, hiring, playing time, injuries, development, results, firings, and timeline randomness may then send those careers apart.

---

## Characteristic audit

This is the pre-implementation checklist for players, general managers, and head coaches. Storage shape is in the data dictionary. This section states, for every characteristic, the ten facts required before models are approved.

Columns used below:

| Column | Meaning |
|--------|---------|
| Meaning | Plain language |
| Type | Logical type |
| Range | Allowed values |
| Layer | `Shared` identity, or `Timeline` state that can diverge. `Derived` is computed and not stored. |
| Visibility | `Visible`, `Partial`, or `Hidden` on the owner's screen |
| Effect | `Sim`, `Display`, or `Both` |
| Generated | How the first value is produced |
| Changes | Age, experience, injuries, performance, coaching, or era |
| Owner sees | `Exact`, or a `Scout estimate` |

Shared characteristics are exact in the database. Hidden means the owner is not shown that exact cell. The sim reads the exact cell. The owner reads a scout row.

**Movement rules for `current`.** When a person enters a timeline, each `current` component is copied from shared `initial`. It then changes only in that timeline. It never exceeds shared `potential` and never drops below 0. Before `peak_age`, snaps, `work_ethic` or the staff `player_development` rating, and a scheme match can raise it, limited by `development_rate`. After `peak_age`, `decline_rate` pulls physical and skill components down. A career-altering injury can drop components immediately. Era does not rewrite an existing person. Era changes the draw frequencies of people created in that era, and it changes roster size, schedule length, injury climate, and, from 1995, the salary cap. One good or bad game does not overwrite a rating. Season statistics affect awards, playing time, and staff reputation. They affect player ratings only through snaps and injuries.

**What the match engine reads.** It reads the components that matter for the player's position, plus stamina, durability, consistency, and the coach's scheme and game-management ratings. It does not read a stored overall. A displayed overall is a position-weighted function of `current` for history, or of the scout reveal for the owner. That function is display only.

**Scout rows are not a third talent.** `TimelineScoutReport` is one row per player overlay. `TimelineScoutComponent` is one row per component per kind (`ability` or `potential`). `revealed_value` is a noisy reading. When `uncertainty` reaches 0, `revealed_value` is set equal to `current` for an ability, or left unset for potential if potential was never fully known. The noise coefficients are an open tuning value. The columns are not.

| Scout field | Meaning | Type | Range | Layer | Visibility | Effect | Generated | Changes | Owner sees |
|-------------|---------|------|-------|-------|------------|--------|-----------|---------|------------|
| `reported_tags` | Trait tags the owner has been told | set | 0–3 of the trait enum | Timeline | Partial | Display | Noisy copy of the true tags at entry | Replaced by the true tags when the player is evaluated | Scout estimate until then, then Exact |
| `tag_uncertainty` | How wrong the tag report may be | smallint | 0–100 | Timeline | Hidden | Display | 20 on the owned roster, 25–60 otherwise | Falls to 0 on evaluation | Not shown. It controls the report. |
| `component` | Which rating the row describes | enum | the 31 components | Timeline | Visible as a label | Display | Created with the overlay | The label does not change | Exact label |
| `kind` | Current ability or potential | enum | `ability` `potential` | Timeline | Visible | Display | Both kinds are created | Does not change | Exact label |
| `revealed_value` | The number shown to the owner | smallint | 0–100 | Timeline | Visible | Display | HMAC noise around the true value, width from uncertainty | Moves when the report is refreshed. Equals `current` at uncertainty 0. | Scout estimate, then Exact |
| `uncertainty` | Remaining fog | smallint | 0 exact, 100 unknown | Timeline | Partial, shown as a confidence band | Display | Owned roster starts at 20. Other players start at 25–60. | Falls to 0 after 4 weeks on the owned roster or 100 snaps, whichever comes first. That threshold is tunable. Potential often stays above 0. | The band is the estimate. The true rating stays hidden until 0. |

`TimelineStaffScoutReport` uses the same shape for a GM or coach ability, including reputation. Public win-loss is not in it. A candidate's report stays partial. During the first season that person works for the owned franchise, uncertainty falls to 0. AI franchises read true `current` and do not use the owner's fog.

### Player identity and biography

Age and experience are not stored. `age = season_year − birth_year`. Pro experience is completed shared seasons before 1970 plus timeline `seasons_played`. Both are visible and exact.

| Field | Meaning | Type | Range | Layer | Visibility | Effect | Generated | Changes | Owner sees |
|-------|---------|------|-------|-------|------------|--------|-----------|---------|------------|
| `universe_key` | Permanent identity | string | ≤ 64 | Shared | Hidden | Both | HMAC from the release seed | Never | Exact internally |
| `name` | Display name | string | ≤ 255 | Shared | Visible | Display | Fictional name lists | Never | Exact |
| `birth_year` | Year of birth | smallint | 1920–2008 | Shared | Visible | Both | Drawn from cohort age, then `birth_year = anchor − age` | Never | Exact |
| `birth_day` | Day of year, for ordering ages | smallint | 1–365 | Shared | Visible | Display | Uniform draw | Never | Exact |
| `college` | School | FK | college slug | Shared | Visible | Display | `EraCollegeWeight` | Never | Exact |
| `position` | Primary position | enum | `QB` `RB` `FB` `WR` `TE` `OT` `OG` `C` `DT` `DE` `LB` `CB` `S` `K` `P` | Shared | Visible | Both | Roster quota or class mix | Never | Exact |
| `secondary_positions` | Other positions he can play | 0–2 enums | same list, excluding primary | Shared | Visible | Both | Generator. Cross-group values are rare. | Never | Exact |
| `handedness` | Throwing or kicking hand | enum | `left` `right` | Shared | Visible | Display. A small accuracy interaction for `QB`, `K`, and `P`. | Generator | Never | Exact |
| `height_in` | Height | smallint | 64–84 | Shared | Visible | Both | Position and era distribution | Never | Exact |
| `weight_lb` | Weight | smallint | 160–360, by position and era | Shared | Visible | Both | Position and era distribution | Never in this design | Exact |
| `cohort` | Opening veteran, archive, or prospect | enum | `opening` `archive` `draft` | Shared | Visible | Both | Which pool he was generated into | Never | Exact |
| `first_pro_year` | Eligibility or historical debut | smallint or null | 1960–1969, or the class year; null for an undrafted prospect | Shared | Visible | Display | Generator | Never. Actual debut of an undrafted prospect is the timeline stint. | Exact |
| `origin_draft_year` | Class he was eligible for, or his historical class | smallint or null | ≤ 1969 for opening and archive; class year for a prospect; null for a historical undrafted free agent | Shared | Visible | Display | Generator | Never | Exact |
| `origin_draft_round` | Historical round | smallint or null | 1–17, or null | Shared | Visible | Display | Generator for opening and archive only | Never. Null for every 1970–2029 prospect. | Exact |
| `origin_draft_pick` | Historical overall pick | smallint or null | 1 through that historical class's draftable count, or null | Shared | Visible | Display | Generator for opening and archive only | Never. Null for every 1970–2029 prospect. | Exact |
| `origin_draft_franchise` | Historical drafting club | FK or null | a franchise, or null | Shared | Visible | Display | Generator for opening and archive only | Never. Null for every 1970–2029 prospect. | Exact |
| `era` | Origin era that shaped the draw | FK | era key | Shared | Visible | Display | Cohort year | Never. The season era is a separate lookup. | Exact |
| `board_rank` | Public board order inside the class | smallint | 1 through class size; draft cohort only | Shared | Visible | Both as the public board. Not true talent. | Generator | Never | Exact |
| `age` | Age that season | derived | `season_year − birth_year` | Derived | Visible | Both | Formula | +1 each season | Exact |
| `experience` | Completed pro seasons | derived | shared seasons before 1970, plus timeline seasons | Derived | Visible | Both | Formula | +1 for each pro season played | Exact |

### Original talent, growth, and the 31 components

`initial` and `potential` exist for every component. They are shared, hidden, exact, generated by HMAC draws under the origin era's frequencies, and they never change. Effect is Sim. `initial` also seeds `current`. The owner never sees these cells. The owner sees the scout component row.

`peak_age` (24–32), `development_rate` (1–100), and `decline_rate` (1–100) are the same: shared, hidden, exact, sim only, never change. They are not shown as numbers. Their effect appears inside the scout band on potential.

| Component | Meaning | What moves `current` |
|-----------|---------|----------------------|
| `speed` | Top-end footspeed | Age, snaps, injury |
| `acceleration` | First-step burst | Age, snaps, injury |
| `agility` | Change of direction | Age, snaps, injury |
| `strength` | Play strength | Age, snaps, injury |
| `stamina` | How little he fades late in a game | Age, snaps, injury |
| `durability` | Injury frequency and recovery. This is injury susceptibility. | Mostly fixed. A career-altering injury can lower it. |
| `awareness` | Pre-snap recognition. Part of football intelligence. | Snaps, coaching, age |
| `processing` | Post-snap decision speed. Part of football intelligence. | Snaps, coaching, age |
| `discipline` | Assignment reliability and penalty avoidance. Part of football intelligence. | Snaps, coaching, age |
| `throw_power` | Velocity and deep range | Age, snaps, injury. Near a low baseline off `QB`. |
| `throw_accuracy` | Placement | Snaps, coaching, age. Near a low baseline off `QB`. |
| `catching` | Hands | Snaps, coaching |
| `route_running` | Route detail and separation from skill rather than speed | Snaps, coaching |
| `carrying` | Ball security and contact balance | Snaps, injury |
| `elusiveness` | Missed tackles and yards after contact | Age, snaps, injury |
| `run_blocking` | Run-game blocking | Snaps, coaching, strength decline |
| `pass_blocking` | Pass protection | Snaps, coaching, strength decline |
| `tackling` | Secure tackles | Snaps, coaching |
| `coverage` | Man and zone coverage | Snaps, coaching, age |
| `pass_rush` | Pressure and sacks | Age, snaps, injury |
| `run_defense` | Fit, shed, and edge set | Snaps, coaching |
| `kick_power` | Field-goal and kickoff distance | Age, injury. Near a low baseline off `K`. |
| `kick_accuracy` | Make rate separate from distance | Snaps, coaching, age. Near a low baseline off `K`. |
| `punt_power` | Punt distance | Age, injury. Near a low baseline off `P`. |
| `punt_accuracy` | Placement, including inside the 20 | Snaps, coaching. Near a low baseline off `P`. |
| `work_ethic` | Practice and development effort | Slow. Age and a career-altering injury can move it. |
| `leadership` | Teammate effect | Slow. Same. |
| `temperament` | Composure after errors | Slow. A career-altering injury or a long injury year can lower it. |
| `clutch` | High-leverage modifier | Slow. |
| `consistency` | Width of a single-game swing around `current`. It does not change the season mean. | Slow. |
| `adaptability` | How fast usage recovers after a scheme change or a game at a secondary position | Slow. |

There is no stored `overall`, no stored `scheme_fit`, and no stored `football_iq`. Football intelligence is `awareness`, `processing`, and `discipline`. Scheme fit is derived at sim time from `adaptability`, the skill vector, and the coach's scheme. The owner sees that fit as `poor`, `fair`, or `good`: partial until evaluation, then an exact label. The label is not stored.

### How positions use the components

Every position stores the same 31 components. Low baselines keep non-position skills from dominating. The sim weights the rows below. Those weights are season configuration, not player columns. Secondary positions use the same ratings, with `adaptability` reducing the penalty for playing off the primary position.

| Position | Components that decide play | Position-specific season record |
|----------|-----------------------------|----------------------------------|
| `QB` | `throw_power`, `throw_accuracy`, `awareness`, `processing`, `clutch`, `consistency`, plus mobility from `speed` and `elusiveness` | Passing line and quarterback rushing |
| `RB` | `speed`, `acceleration`, `agility`, `carrying`, `elusiveness`, `catching`, `stamina` | Rushing and receiving |
| `FB` | `strength`, `run_blocking`, `carrying`, `catching` | Rushing, receiving, `run_block_snaps` |
| `WR` | `speed`, `acceleration`, `agility`, `catching`, `route_running` | Receiving, a small rushing line |
| `TE` | `catching`, `route_running`, `run_blocking`, `pass_blocking`, `strength` | Receiving and block snaps |
| `OT`, `OG`, `C` | `strength`, `run_blocking`, `pass_blocking`, `awareness`, `discipline` | Block snaps, `sacks_allowed`. The position enum picks alignment. There is no separate center-snap rating. |
| `DT`, `DE` | `strength`, `pass_rush`, `run_defense`, `speed` | Tackles, sacks in tenths, hits, forced fumbles |
| `LB` | `tackling`, `coverage`, `run_defense`, `awareness`, `speed` | Defensive line columns plus coverage |
| `CB` | `speed`, `agility`, `coverage`, `awareness` | Coverage, interceptions, passes defended |
| `S` | `coverage`, `tackling`, `awareness`, `speed`, `run_defense` | Coverage and tackles |
| `K` | `kick_power`, `kick_accuracy`, `clutch`, `consistency` | Field goals, extra points, touchbacks |
| `P` | `punt_power`, `punt_accuracy`, `consistency` | Punts, inside-20, touchbacks |

`handedness` is stored for every player and is relevant to `QB`, `K`, and `P`.

### Trait tags

| Field | Meaning | Type | Range | Layer | Visibility | Effect | Generated | Changes | Owner sees |
|-------|---------|------|-------|-------|------------|--------|-----------|---------|------------|
| `trait_tags` | Named personality or habit | set of enum | 0–3 of `film_junkie`, `gym_rat`, `hothead`, `quiet_pro`, `mentor`, `big_play` | Shared | Hidden | `hothead` and `mentor` affect Sim. The other four are Display. | Generator | Never | Scout `reported_tags` until evaluation, then Exact |

### Timeline playing state

| Field | Meaning | Type | Range | Layer | Visibility | Effect | Generated | Changes | Owner sees |
|-------|---------|------|-------|-------|------------|--------|-----------|---------|------------|
| `current` (31) | Ability right now in this timeline | smallint | 0–100, ≤ potential | Timeline | Hidden | Both. The sim reads this exact vector. | Copied from `initial` at entry | Age, snaps, coaching, injury. Not a direct function of one game's box score. | Scout estimate until evaluation, then Exact |
| `status` | Roster category | enum | `prospect` `active` `taxi` `reserve` `free_agent` `retired` | Timeline | Visible | Both | Opening copy, then sim | Cuts, signings, injuries, retirement | Exact |
| `seasons_played` | Pro seasons inside this timeline | smallint | 0–60 | Timeline | Visible | Display | Cached count of timeline seasons | +1 per season played | Exact |
| `career_altering_injuries` | Count of injuries that changed talent | smallint | 0–10 | Timeline | Partial | Both | Sim | +1 when an injury is career-altering | Exact count on the owned roster. Hidden on other clubs, where only the public injury report is shown. |

### Roster stint

A stint is a transaction. Trades, cuts, and signings close one stint and open another. Retirement is `status = retired` plus a closed stint. There is no second transaction ledger.

| Field | Meaning | Type | Range | Layer | Visibility | Effect | Generated | Changes | Owner sees |
|-------|---------|------|-------|-------|------------|--------|-----------|---------|------------|
| `team` | Club, or empty if a free agent | FK or null | a franchise | Timeline | Visible | Both | Opening assignment, then sim | Each transaction | Exact |
| `role` | Depth role | enum | `starter` `backup` `taxi` `reserve` `free_agent` | Timeline | Visible | Both | same | Depth-chart moves | Exact |
| `start_year`, `start_week` | When the stint opened | smallint | year 1970–2029; week 0 is preseason | Timeline | Visible | Both | same | Written once | Exact |
| `end_year`, `end_week` | When it closed | smallint or null | ≥ start; null while open | Timeline | Visible | Both | Sim | Set when the stint ends | Exact |

### Contract

Opening veterans have a shared template. The timeline copy is the living contract. Free agents have no opening template.

| Field | Meaning | Type | Range | Layer | Visibility | Effect | Generated | Changes | Owner sees |
|-------|---------|------|-------|-------|------------|--------|-----------|---------|------------|
| `years` | Seasons on the deal | smallint | template 1–5; timeline deal 1–7 | Shared template, then Timeline | Visible | Both | Generator, then GM negotiation | Extensions and new deals | Exact. Contracts are public. |
| `salary_per_year` | Annual pay | int | 1970 template 20,000–250,000; later deals in nominal dollars of the signing year | same | Visible | Display before 1995. Both from the 1995 cap. | same | New contract | Exact |
| `guaranteed` | Guaranteed dollars | int | 0 through salary × years | same | Visible | same | same | New contract | Exact |
| `signing_bonus` | Bonus | int | dollars of the signing year | same | Visible | Display. Sim from 1995 through cap proration. | same | New contract | Exact |
| `start_year`, `end_year` | Span | smallint | timeline end year may run through 2035 | same | Visible | Both | same | Cuts set status, not a rewritten start | Exact |
| `status` | Living state | enum | `active` `expired` `cut` `retired` | Timeline | Visible | Both | Sim | Cut, expiry, retirement | Exact |
| `franchise` | Club that holds the deal | FK | a franchise | Timeline | Visible | Both | Signing | A new deal on a trade or free-agent signing | Exact |
| `is_rookie_deal` | Created by a draft signing | bool | | Timeline | Visible | Display | Sim | Never on that row | Exact |

The cap limit lives on the season era, not on the player.

### Injuries

| Field | Meaning | Type | Range | Layer | Visibility | Effect | Generated | Changes | Owner sees |
|-------|---------|------|-------|-------|------------|--------|-----------|---------|------------|
| `season_year`, `week` | When it happened | smallint | 1970–2029; week 0 is camp, otherwise 1 through the schedule | Timeline | Visible | Both | Sim, weighted by era and `durability` | Written once | Exact |
| `kind` | Injury class | enum | `sprain` `break` `concussion` `tear` `illness` `wear` | Timeline | Visible | Both | Sim | Written once | Exact |
| `body_part` | Where | enum | `head` `shoulder` `arm` `hand` `back` `hip` `thigh` `knee` `ankle` `foot` | Timeline | Visible | Both | Sim | Written once | Exact |
| `weeks_out` | Expected absence | smallint | 0–36 | Timeline | Visible | Sim | Sim | Written once | Exact on the public injury report |
| `season_ending` | Out for the year | bool | | Timeline | Visible | Both | Sim | Written once | Exact |
| `career_altering` | This injury also drops `current` | bool | | Timeline | Partial | Both | Sim | Written once | Exact on the owned roster. Hidden elsewhere. The rating drop is never on the public report. |

### Draft result, awards, and development trail

The shared `board_rank` is the public board. The timeline pick is what this timeline did.

| Field | Meaning | Type | Range | Layer | Visibility | Effect | Generated | Changes | Owner sees |
|-------|---------|------|-------|-------|------------|--------|-----------|---------|------------|
| `season_year` | Class year | smallint | 1970–2029 | Timeline | Visible | Both | The draft | Written once | Exact |
| `round` | Round | smallint | 1 through that era's round count | Timeline | Visible | Both | Owner for his club, sim for the others | Written once | Exact |
| `pick_number` | Overall pick | smallint | 1 through `draftable(year)` | Timeline | Visible | Both | same | Written once | Exact |
| `franchise` | Club that picked | FK | a franchise | Timeline | Visible | Both | same | Written once | Exact |
| `prospect` | Shared player | FK | a draftable prospect | Timeline | Visible | Both | same | Written once | Exact |
| Award `kind` | Honor | enum | `mvp` `opoy` `dpoy` `oroy` `droy` `all_league_1` `all_league_2` `all_star` `coach_of_year` `executive_of_year` | Shared before 1970, Timeline from 1970 | Visible | Shared: Display. Timeline: Display, and Sim for the next offseason's reputation and development. | Generator, or timeline sim | Written once | Exact |
| Award `slot` | All-league position | enum | a position, only for all-league rows | same | Visible | Display | same | Written once | Exact |
| `component`, `delta`, `reason` | Development audit | enum, smallint, enum | component is one of the 31; delta −20 to +20; reason `age` `snaps` `injury` `coaching` | Timeline | Hidden | Display | Sim | Append-only | Hidden. The owner sees the refreshed scout number, not the delta log. |

Undrafted signings create a contract and a stint and no pick row. Expansion-draft picks are `TimelineExpansionPick`, not a second copy of the player.

### College history

`UniverseCollegeSeason`. Planning default is four seasons, the four years before `first_pro_year` or the class year. Display only. The sim reads `board_rank` and the rating vector, not these counting stats. Every column is shared, visible, exact, generated once, and never changes.

| Field | Meaning | Type | Range |
|-------|---------|------|-------|
| `season_year` | College season | smallint | the four years before the anchor |
| `class_standing` | Year in school | enum | `freshman` `sophomore` `junior` `senior` |
| `games`, `starts` | Games and starts | smallint | 0–12 |
| Passing, rushing, receiving, defense, kicking, punting columns | Same names as the pro season, for the player's position group | same types | college-sized ranges of the pro columns |

### Pro season statistics

Two stores, one column set. `UniverseProSeason` is 1960–1969, shared, visible, exact, display only, generated to agree with `initial`, and never changes. `TimelineSeason` is 1970–2029, visible, exact, written by the match engine, and kept. Timeline rows are Both: awards, development, and AI read them. Unused columns stay 0. Passer rating, yards per punt, and net average are derived, visible, exact, and display only.

Common columns, every position:

| Field | Meaning | Type | Range |
|-------|---------|------|-------|
| `games` | Games played | smallint | 0 through that season's schedule length (14, 16, or 17) |
| `starts` | Starts | smallint | 0–`games` |
| `offensive_snaps` | Offensive snaps | smallint | 0–`games` × 90 |
| `defensive_snaps` | Defensive snaps | smallint | 0–`games` × 90 |
| `special_teams_snaps` | Special-teams snaps | smallint | 0–`games` × 30 |
| `penalties` | Penalties | smallint | 0–30 |
| `penalty_yards` | Penalty yards | smallint | 0–300. Display only, even on a timeline row. |
| `fumbles` | Fumbles | smallint | 0–20 |
| `fumbles_lost` | Fumbles lost | smallint | 0–`fumbles` |

| Position | Field | Meaning | Type | Range |
|----------|-------|---------|------|-------|
| `QB` | `pass_attempts` | Throws | smallint | 0–800 |
| `QB` | `completions` | Completions | smallint | 0–`pass_attempts` |
| `QB` | `pass_yards` | Passing yards | int | −200–6,000 |
| `QB` | `pass_touchdowns` | Passing touchdowns | smallint | 0–60 |
| `QB` | `interceptions_thrown` | Interceptions thrown | smallint | 0–40 |
| `QB` | `sacks_taken` | Sacks taken | smallint | 0–80 |
| `QB` | `sack_yards` | Sack yards | smallint | 0–600 |
| `QB` | `rush_attempts` | Rushes | smallint | 0–200 |
| `QB` | `rush_yards` | Rushing yards | int | −100–1,500 |
| `QB` | `rush_touchdowns` | Rushing touchdowns | smallint | 0–20 |
| `RB`, `FB` | `rush_attempts` | Rushes | smallint | 0–450 |
| `RB`, `FB` | `rush_yards` | Rushing yards | int | −50–2,500 |
| `RB`, `FB` | `rush_touchdowns` | Rushing touchdowns | smallint | 0–30 |
| `RB`, `FB` | `targets` | Targets | smallint | 0–150 |
| `RB`, `FB` | `receptions` | Receptions | smallint | 0–`targets` |
| `RB`, `FB` | `receiving_yards` | Receiving yards | int | −20–1,200 |
| `RB`, `FB` | `receiving_touchdowns` | Receiving touchdowns | smallint | 0–15 |
| `RB`, `FB` | `drops` | Drops | smallint | 0–`targets` − `receptions` |
| `RB`, `FB` | `run_block_snaps` | Run-block snaps | smallint | 0–offensive snaps. Both for fullbacks. Display for tailbacks. |
| `WR`, `TE` | `targets` | Targets | smallint | 0–220 |
| `WR`, `TE` | `receptions` | Receptions | smallint | 0–`targets` |
| `WR`, `TE` | `receiving_yards` | Receiving yards | int | −20–2,000 |
| `WR`, `TE` | `receiving_touchdowns` | Receiving touchdowns | smallint | 0–25 |
| `WR`, `TE` | `drops` | Drops | smallint | 0–30 |
| `WR`, `TE` | `rush_attempts` | Rushes | smallint | 0–40 |
| `WR`, `TE` | `rush_yards` | Rushing yards | int | −10–400 |
| `WR`, `TE` | `rush_touchdowns` | Rushing touchdowns | smallint | 0–8 |
| `WR`, `TE` | `run_block_snaps` | Run-block snaps | smallint | 0–`offensive_snaps` |
| `WR`, `TE` | `pass_block_snaps` | Pass-block snaps | smallint | 0–offensive snaps |
| `OT`, `OG`, `C` | `run_block_snaps` | Run-block snaps | smallint | 0–`offensive_snaps` |
| `OT`, `OG`, `C` | `pass_block_snaps` | Pass-block snaps | smallint | 0–`offensive_snaps` |
| `OT`, `OG`, `C` | `sacks_allowed` | Sacks allowed | smallint | 0–20 |
| `DT`, `DE`, `LB` | `tackles` | Tackles | smallint | 0–120 on the line; 0–200 for linebackers |
| `DT`, `DE`, `LB` | `tackles_for_loss` | Tackles for loss | smallint | 0–30 |
| `DT`, `DE`, `LB` | `sacks` | Sacks in tenths | smallint | 0–250. 75 means 7.5. |
| `DT`, `DE`, `LB` | `quarterback_hits` | Hits on the quarterback | smallint | 0–40 |
| `DT`, `DE`, `LB` | `forced_fumbles` | Forced fumbles | smallint | 0–10 |
| `DT`, `DE`, `LB` | `fumble_recoveries` | Fumble recoveries | smallint | 0–8 |
| `DT`, `DE`, `LB` | `passes_defended` | Passes defended | smallint | 0–15 |
| `DT`, `DE`, `LB` | `defensive_touchdowns` | Defensive touchdowns | smallint | 0–4 |
| `LB` | `coverage_snaps` | Snaps in coverage | smallint | 0–`defensive_snaps` |
| `LB` | `targets_in_coverage` | Targets allowed | smallint | 0–80 |
| `LB` | `completions_allowed` | Completions allowed | smallint | 0–`targets_in_coverage` |
| `LB` | `interceptions` | Interceptions | smallint | 0–10 |
| `CB`, `S` | `tackles` | Tackles | smallint | 0–140 |
| `CB`, `S` | `interceptions` | Interceptions | smallint | 0–15 |
| `CB`, `S` | `passes_defended` | Passes defended | smallint | 0–30 |
| `CB`, `S` | `targets_in_coverage` | Targets in coverage | smallint | 0–140 |
| `CB`, `S` | `completions_allowed` | Completions allowed | smallint | 0–`targets_in_coverage` |
| `CB`, `S` | `yards_allowed` | Yards allowed | int | 0–1,500 |
| `CB`, `S` | `forced_fumbles` | Forced fumbles | smallint | 0–8 |
| `CB`, `S` | `defensive_touchdowns` | Defensive touchdowns | smallint | 0–4 |
| `K` | `fg_made` | Field goals made | smallint | 0–45 |
| `K` | `fg_attempts` | Field goals attempted | smallint | 0–50, ≥ made |
| `K` | `fg_long` | Longest field goal | smallint | 0–65 yards; 0 if none made |
| `K` | `xp_made` | Extra points made | smallint | 0–70 |
| `K` | `xp_attempts` | Extra points attempted | smallint | 0–75, ≥ made |
| `K` | `kickoff_touchbacks` | Kickoff touchbacks | smallint | 0–90 |
| `P` | `punts` | Punts | smallint | 0–120 |
| `P` | `punt_yards` | Gross punt yards | int | 0–6,000 |
| `P` | `punts_inside_20` | Inside the 20 | smallint | 0–`punts` |
| `P` | `punt_touchbacks` | Touchbacks | smallint | 0–`punts` |
| `P` | `punt_long` | Longest punt | smallint | 0–90 yards |

### General managers

Ability ratings other than `reputation` have shared `initial` and `potential` and timeline `current`. `reputation` has shared `initial` and timeline `current` only. It can fall. It is not capped by a potential. True ability cells are hidden and exact. The owner sees a staff scout report: partial while the GM is a candidate, exact after one season with the owned franchise. The sim always uses the true values. Strengths, the weakness, traits, and `positional_priority` are shared and never change. They are partial on the hiring screen (the report may omit one tag) and exact once he is employed by the owner.

`current` abilities drift toward `potential` with years on the job. They do not reset when he is fired. Owner directives constrain decisions and do not edit the ratings. Incoming classes after 1995 are drawn with more weight on `cap_management`. A GM already in the world is not rewritten by the cap era; he can still develop `cap_management` up to his potential.

| Field | Meaning | Type | Range | Layer | Visibility | Effect | Generated | Changes | Owner sees |
|-------|---------|------|-------|-------|------------|--------|-----------|---------|------------|
| `universe_key` | Permanent identity | string | `g-…`, ≤ 64 | Shared | Hidden | Both | HMAC | Never | Exact internally |
| `name` | Display name | string | ≤ 255 | Shared | Visible | Display | Name lists | Never | Exact |
| `birth_year` | Year of birth | smallint | about 1905–1938 for the initial 100; later classes are about 40 at debut | Shared | Visible | Both | Generator | Never | Exact |
| `debut_year` | First year he can be hired | smallint | 1970 for the initial 100; 1971–2029 incoming | Shared | Visible | Both | Generator | Never | Exact |
| `former_role` | Path before this career | enum | `player` `scout` `college_coach` `personnel` `cap_analyst` | Shared | Visible | Display | Generator | Never | Exact |
| `age` | Age that season | derived | `season_year − birth_year` | Derived | Visible | Display | Formula | +1 per season | Exact |
| `talent_evaluation` | Player evaluation | smallint | 0–100 | Shared initial and potential; Timeline current | Hidden | Both | Generator, then slow job experience | Toward potential with experience | Scout estimate, then Exact |
| `scheme_fit` | How hard he insists on players who match the coach's scheme | smallint | 0 ignores scheme, 100 refuses a poor fit | same | Hidden | Both | same | Toward potential | Scout estimate, then Exact |
| `negotiation` | Trade negotiation | smallint | 0–100 | same | Hidden | Both | same | Toward potential | Scout estimate, then Exact |
| `cap_management` | Contract length, guarantees, and cap fit | smallint | 0–100 | same | Hidden | Both | same. Higher draws after 1995 for new GMs. | Toward potential | Scout estimate, then Exact |
| `draft_strategy` | Draft ability: board discipline and round value | smallint | 0–100 | same | Hidden | Both | same | Toward potential | Scout estimate, then Exact |
| `free_agency` | Free-agent recruiting | smallint | 0–100 | same | Hidden | Both | same | Toward potential | Scout estimate, then Exact |
| `roster_construction` | Building a roster, not only one position | smallint | 0–100 | same | Hidden | Both | same | Toward potential | Scout estimate, then Exact |
| `player_development` | How much young players improve under this organization | smallint | 0–100 | same | Hidden | Both | same | Toward potential | Scout estimate, then Exact |
| `discipline` | Tolerance for character and penalty risk | smallint | 0–100 | same | Hidden | Both | same | Toward potential | Scout estimate, then Exact |
| `leadership` | Authority inside the building | smallint | 0–100 | same | Hidden | Both | same | Toward potential | Scout estimate, then Exact |
| `risk_tolerance` | Appetite for risky trades, contracts, and prospects | smallint | 0–100 | same | Hidden | Both | same | Toward potential | Scout estimate, then Exact |
| `patience` | How long a plan runs before he changes course | smallint | 0 impatient, 100 patient | same | Hidden | Both | same | Toward potential | Scout estimate, then Exact |
| `coach_evaluation` | Ability to choose and keep a compatible coach | smallint | 0–100 | same | Hidden | Both | same | Toward potential | Scout estimate, then Exact |
| `veteran_preference` | Veterans versus younger players | smallint | 0 youth, 100 veterans | same | Hidden | Both | same | Slow drift inside potential | Scout estimate, then Exact |
| `pick_trade_willingness` | Willingness to trade picks | smallint | 0–100 | same | Hidden | Both | same | Toward potential | Scout estimate, then Exact |
| `reputation` | Standing around the league | smallint | 0–100 | Shared initial; Timeline current | Hidden as the true cell | Both | From pre-1970 posts, then results | Wins, playoffs, awards, and firings. Can fall. No potential cap. | Scout estimate, then Exact. The public record of wins and firings is always exact. |
| `positional_priority` | Groups he overvalues | 1–2 enums | `quarterback` `skill` `offensive_line` `defensive_line` `linebacker` `defensive_back` `special_teams` | Shared | Partial until hired | Both | Generator | Never | Scout estimate, then Exact |
| `strengths` | Two decision bonuses | 2 enums | `quarterback_eye`, `lineman_eye`, `finds_speed`, `finds_size`, `trade_market`, `cap_discipline`, `loyalty`, `medical_caution`, `late_round`, `character` | Shared | Partial until hired | Both | Generator. Unique with weakness, traits, and career signature. | Never | Scout estimate, then Exact |
| `weakness` | One decision penalty | enum | same vocabulary, not equal to either strength | Shared | Partial until hired | Both | Generator | Never | Scout estimate, then Exact |
| `traits` | Tie-breakers, including a public face and patience flavor | 2 enums | `patient`, `impatient`, `secretive`, `public`, `delegator`, `hands_on`, `stubborn`, `adaptable`, `gambler`, `conservative` | Shared | Partial until hired | Both | Generator | Never | Scout estimate, then Exact |

`patience` the rating is on every GM. The `patient` and `impatient` traits only break ties, and a GM might have neither trait.

Job stability is not a stored personality stat. Firing probability comes from results, `leadership`, `reputation`, and the owner's patience with the club. The outcome is `reason_ended = fired` on the stint.

Career history before he is hireable is `UniverseStaffPost`: `role` (`gm`, `personnel_director`, `scout`, `college_coach`), franchise or null, years, wins, losses, ties, playoff appearances, championships, and `ended_by` (`resigned`, `fired`, `promoted`, `retired`, `post_completed`). Those rows are shared, visible, exact, display only, generated to agree with the ratings, and they never change.

Timeline employment is `StaffStint`: role `gm`, franchise or null, start and end year, `reason_ended` (`opening_replace`, `fired`, `retired`, `hired_elsewhere`, `promoted`, `timeline_complete`). The stint is timeline state, visible, exact, and both sim and display. Wins, losses, ties, playoff appearances, and championships during the stint are derived from that timeline's standings. They are not stored twice. They are visible and exact.

### Head coaches

The same identity rules as GMs apply, with key prefix `c-`. `specialty` is `offense`, `defense`, or `both`. The initial 100 head coaches are `both`. Offensive coordinators are `offense`. Defensive coordinators are `defense`. Specialty does not change on promotion. `former_role` is `player`, `college_coach`, `coordinator`, or `position_coach`.

Ability movement matches the GM rules. `preferred_offense` and `preferred_defense` never change. Scheme ratings can rise inside potential. A 1970 `power_run` coach does not become an `air_raid` coach. `reputation` moves with season results, playoff results, awards, and firings.

| Field | Meaning | Type | Range | Layer | Visibility | Effect | Generated | Changes | Owner sees |
|-------|---------|------|-------|-------|------------|--------|-----------|---------|------------|
| `universe_key` | Permanent identity | string | `c-…` | Shared | Hidden | Both | HMAC | Never | Exact internally |
| `name` | Display name | string | ≤ 255 | Shared | Visible | Display | Name lists | Never | Exact |
| `birth_year` | Year of birth | smallint | head coaches about 32–65 in 1970; coordinators may be younger | Shared | Visible | Both | Generator | Never | Exact |
| `debut_year` | First year he can be hired | smallint | 1970, or 1971–2029 | Shared | Visible | Both | Generator | Never | Exact |
| `former_role` | Path into this career | enum | `player` `college_coach` `coordinator` `position_coach` | Shared | Visible | Display | Generator | Never | Exact |
| `specialty` | Which coordinator chair he may hold | enum | `offense` `defense` `both` | Shared | Visible | Both | Generator | Never, including after promotion to head coach | Exact |
| `age` | Age that season | derived | `season_year − birth_year` | Derived | Visible | Display | Formula | +1 per season | Exact |
| `offensive_scheme` | Quality of his offense inside `preferred_offense` | smallint | 0–100 | Shared initial and potential; Timeline current | Hidden | Both | Generator, then experience | Toward potential. The enum does not change. | Scout estimate, then Exact |
| `defensive_scheme` | Quality of his defense inside `preferred_defense` | smallint | 0–100 | same | Hidden | Both | same | Toward potential | Scout estimate, then Exact |
| `preferred_offense` | Offensive philosophy | enum | `power_run` `option` `pro_style`; `west_coast` from 1987 debuts; `air_raid` from 2004 debuts | Shared | Partial until hired | Both | Generator from debut era | Never | Scout estimate, then Exact |
| `preferred_defense` | Defensive philosophy | enum | `stop_run` `balanced` `pressure_quarterback` | Shared | Partial until hired | Both | Generator | Never | Scout estimate, then Exact |
| `game_management` | In-game decisions other than the clock and fourth down | smallint | 0–100 | same as scheme ratings | Hidden | Both | Generator, then experience | Toward potential | Scout estimate, then Exact |
| `clock_management` | Clock decisions | smallint | 0–100 | same | Hidden | Both | same | Toward potential | Scout estimate, then Exact |
| `fourth_down_aggression` | Fourth-down appetite | smallint | 0 conservative, 100 aggressive | same | Hidden | Both | same | Slow drift inside potential | Scout estimate, then Exact |
| `aggressiveness` | General risk in calls, including fake kicks and pressure | smallint | 0–100 | same | Hidden | Both | same | Toward potential | Scout estimate, then Exact |
| `in_game_adjustments` | Changes inside a game | smallint | 0–100 | same | Hidden | Both | same | Toward potential | Scout estimate, then Exact |
| `adaptability` | How fast the plan changes across a season when it is failing | smallint | 0–100 | same | Hidden | Both | same | Toward potential | Scout estimate, then Exact |
| `player_development` | Player development | smallint | 0–100 | same | Hidden | Both | same | Toward potential with experience | Scout estimate, then Exact |
| `preparation` | Weekly preparation | smallint | 0–100 | same | Hidden | Both | same | Toward potential | Scout estimate, then Exact |
| `motivation` | Week-to-week effort he gets from the roster | smallint | 0–100 | same | Hidden | Both | same | Toward potential | Scout estimate, then Exact |
| `leadership` | Locker-room authority, separate from week-to-week motivation | smallint | 0–100 | same | Hidden | Both | same | Toward potential | Scout estimate, then Exact |
| `discipline` | Practice rules and on-field penalties | smallint | 0–100 | same | Hidden | Both | same | Toward potential | Scout estimate, then Exact |
| `fatigue_management` | Snap counts and late-game freshness | smallint | 0–100 | same | Hidden | Both | same | Toward potential | Scout estimate, then Exact |
| `injury_management` | Resting players and bringing them back from injury | smallint | 0–100 | same | Hidden | Both | same | Toward potential | Scout estimate, then Exact |
| `staff_management` | Use of coordinators | smallint | 0–100 | same | Hidden | Both | same | Toward potential | Scout estimate, then Exact |
| `veteran_preference` | Veterans versus younger players in the lineup | smallint | 0 youth, 100 veterans | same | Hidden | Both | same | Slow drift inside potential | Scout estimate, then Exact |
| `reputation` | Standing | smallint | 0–100 | Shared initial; Timeline current | Hidden as the true cell | Both | Pre-1970 posts, then results | Wins, playoffs, awards, firings. Can fall. | Scout estimate, then Exact. Public record stays exact. |
| `strengths` | Two on-field bonuses | 2 enums | `run_game`, `passing_game`, `red_zone`, `defense`, `special_teams`, `develops_quarterbacks`, `develops_linemen`, `halftime_adjustments`, `discipline`, `closer` | Shared | Partial until hired | Both | Generator. Unique with weakness, traits, and career signature. | Never | Scout estimate, then Exact |
| `weakness` | One on-field penalty | enum | same vocabulary | Shared | Partial until hired | Both | Generator | Never | Scout estimate, then Exact |
| `traits` | Personality on the sideline | 2 enums | `players_coach`, `disciplinarian`, `innovator`, `conservative`, `volatile`, `even_keel`, `workaholic`, `delegator` | Shared | Partial until hired | Both | Generator | Never | Scout estimate, then Exact |

Roster fit is not a second stored rating. The sim combines `preferred_offense`, `preferred_defense`, `adaptability`, and the GM's `scheme_fit` when the roster is built and when playing time is assigned. The owner sees that as a fit label on a player, from the player audit.

Coach posts before the debut use the GM post shape with `role` in `head_coach`, `offensive_coordinator`, `defensive_coordinator`, `position_coach`, `college_head_coach`. They are shared, visible, exact, and display only. Timeline stints use role `head_coach`, `offensive_coordinator`, `defensive_coordinator`, or `position_coach`. Season record, playoff appearances, and championships during a stint are derived from timeline standings. Firings, awards, and reputation after 1970 are timeline facts. The pre-1970 record never changes.

### What this audit added

| Addition | Why |
|----------|-----|
| `consistency`, `adaptability` | Game-to-game variance and scheme changes were not in the 27-component list. |
| `secondary_positions` | Position flexibility had no field. |
| `kick_power`, `kick_accuracy`, `punt_power`, `punt_accuracy` | One `kicking` number and one `punting` number could not separate distance from accuracy. |
| `cap_management`, `patience` | Trade negotiation was doing two jobs, and patience existed only as a trait some GMs never drew. |
| `coach_evaluation`, `veteran_preference`, `pick_trade_willingness`, `positional_priority`, `reputation` on the GM | The prior GM list could not separate coach hiring, youth versus veterans, pick trading, positional bias, or a reputation that moves with results. |
| `preparation`, `fatigue_management`, `injury_management`, `fourth_down_aggression`, `adaptability`, `leadership`, `veteran_preference`, `reputation` on the coach | Clock management was present, but fourth down, weekly preparation, fatigue, injury return, season-to-season adaptability, and locker-room leadership were missing or collapsed into `motivation`. |
| `specialty`, coach `former_role` | Required once coordinators share the coach table. |
| `ended_by` on shared posts, and `promoted` on timeline stints | Firings and promotions need a recorded ending. |
| `TimelineScoutReport` and `TimelineScoutComponent` | Original ratings, current ratings, and the owner's estimate are three different facts. |
| `slot_order` and the explicit 24-, 28-, and 32-team assignments | Realignment has to be identical in every timeline without inventing cities. |
| `Timeline.start_season_year` | A later mode that starts in 1976 or 1996 must not need a new table. The only supported value until that product decision is 1970. |
| `TimelineExpansionChoice` | The owner's stay-or-apply decision, protect list, and priorities. |

### What this audit removed, combined, or renamed

| Change | Why |
|--------|-----|
| Removed `injury_prone_label` | It duplicated `durability`. The sim reads `durability`. |
| Removed `scheme_specific` | Scheme fit is derived from `adaptability`, skills, and the coach's scheme. A tag would have been a second, conflicting answer. |
| Split `kicking` into `kick_power` and `kick_accuracy` | Distance and make rate were one number. |
| Split `punting` into `punt_power` and `punt_accuracy` | Distance and placement were one number. |
| Narrowed `negotiation` to trade negotiation | Salary and cap work moved to `cap_management`. |
| Did not add `overall` as a column | It stays a derived display value. Game outcomes use the components. |
| Did not add `football_iq` | It is `awareness` + `processing` + `discipline`. |
| Did not add stored player `scheme_fit` | It is derived at sim time. |
| Did not add stored `experience` or `age` | Both are formulas. |
| Did not add GM or coach `job_security` | Job security is the firing outcome of results, leadership, and reputation. |
| Did not add a coach `roster_fit` rating | Roster fit is `preferred_*` plus `adaptability` plus the GM's `scheme_fit`. |
| Did not add a second transaction table | Stints, contracts, draft picks, expansion picks, injuries, and awards are the transactions. |

The component count is 31. The previous draft of this file used 27 and treated overall as a derived display value, which is unchanged: overall is still not a sim input.

---


## Population and row counts

The playable clock is 60 seasons, 1970 through 2029 inclusive. `N(year)` is exact:

| Seasons | Years | N(year) | Franchise-seasons |
|---------|-------|---------|-------------------|
| 1970–1975 | 6 | 24 | 144 |
| 1976–1995 | 20 | 28 | 560 |
| 1996–2029 | 34 | 32 | 1,088 |
| **Total** | **60** | | **1,792** |

`slots(year)` and `rounds(year)` are still the era defaults (40+8 and 17 rounds in 1970–1977, then the era table). Undrafted prospects are 2 per active franchise. Opening free agents are 5 per founding franchise. Those defaults are not locked. Every player total below is exact only while they hold. Franchise-seasons do not depend on them.

### Exact from the schedule

| Fact | Value |
|------|--------|
| Founding franchises | 24 |
| 1976 expansion franchises | 4 |
| 1996 expansion franchises | 4 |
| Active clubs, 1996–2029 | 32 |
| Franchise-seasons, 1970–2029 | 1,792 |
| Shared people created by an expansion draft | 0 |

### Totals that use the era defaults

| Slice | Why it can still move | Rows if defaults hold |
|-------|----------------------|------------------------|
| 1970 opening rosters | 40 active + 8 taxi | 1,152 |
| 1970 free agents | 5 per founding club | 120 |
| Opening cohort | sum of the two rows above | 1,272 |
| Draftable prospects | round counts × N(year) | 16,028 |
| Undrafted prospects | 2 per active club | 3,584 |
| Draft prospects | draftable + undrafted | 19,612 |
| Playable player identities | 1,272 + 19,612 | **20,884** |
| Archive players | 5-year mean inside 11,520 historical lines | 1,032 |
| All player identities | 20,884 + 1,032 | **21,916** |
| Shared pro lines, 1960–1969 | 24 × 48 × 10, before expansion | 11,520 |
| College season lines | 4 per identity | 87,664 |
| Head coaches | floor of 8 + E(year) | 580 |
| GMs | floor of 6 + E(year) | 462 |
| GM + head-coach identities | | **1,042** |
| Coordinators, if generated now | floor in the coordinator section | 568 |
| All staff identities with coordinators | 1,042 + 568 | **1,610** |

Roster-slot seasons, using the era roster defaults and `N(year)`:

| Years | N | Slots per team | Slot-seasons |
|-------|---|----------------|--------------|
| 1970–1975 | 24 | 48 | 6,912 |
| 1976–1977 | 28 | 48 | 2,688 |
| 1978–1986 | 28 | 53 | 13,356 |
| 1987–1994 | 28 | 55 | 12,320 |
| 1995 | 28 | 61 | 1,708 |
| 1996–2003 | 32 | 61 | 15,616 |
| 2004–2013 | 32 | 61 | 19,520 |
| 2014–2029 | 32 | 61 | 31,232 |
| **Total** | | | **103,352** |

| Timeline store on one finished run | Depends on | Rows if defaults hold |
|------------------------------------|------------|------------------------|
| Player overlays after 2029 | class sizes | 20,884 |
| Player overlays before the 1970 season, after that draft | 1970 class of 456 | 1,728 |
| Season lines, one per roster slot | 103,352 slot-seasons | 103,352 |
| Draft picks | draftable seats | 16,028 |
| Injuries | 30 per franchise-season | 53,760 |
| Contracts | one per two slot-seasons | 51,676 |
| Staff stints | 4-year mean tenure. GM and head coach only are 2 roles. Seating coordinators as well is 4 roles. | 896 for GM and head coach; 1,792 if coordinators are seated |
| Awards | a fixed annual honor list, not N(year) | about 3,500 |
| Expansion-draft picks | `players_protected` and picks per new club, still unset | not knowable yet |

Changing the 1978 roster from 53 to another number changes only the years that use that era. Changing 1996–2029 from 32 teams is a new schedule, not a tuning of these defaults.

### Obsolete 32-team estimate

An earlier draft reported **28,384** universe player rows for 32 teams in every year and drafts from 1971 through 2049 (1,536 roster slots + 160 free agents + 26,688 draft prospects). That number is retired. A later no-expansion sketch of **17,424** playable players assumed 24 teams in every season through 2029. That sketch is also retired. The schedule in this section is the one to implement, and **20,884** playable players is the total only while the era roster and round defaults hold.

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
11. A published release rejects edits to an existing `universe_key` and rejects edits to a published expansion schedule. A schedule change is a new release.
12. Two timelines on one release have the same `N(year)`, the same expansion franchises, and the same draft-class keys for every year from 1970 through 2029.
13. An expansion draft inserts timeline picks only. It does not change the universe player count.
14. Every timeline on a release has the same conference slug, division slug, and `slot_order` for each franchise-season, matching the 24-, 28-, and 32-team tables in this document.
15. A player's `universe_key`, name, birth, college, body, position, `initial` ratings, `potential`, traits, and pre-1970 history are identical on every timeline. `current` ratings and 1970–2029 results are not.

---

## Fit with the current project

- External ids stay natural keys: `universe_key`, college slug, existing team slug.
- Generation is a management command. The 32-team admin view is the right pattern for a few dozen catalog rows and the wrong pattern for a full player release. If the era defaults hold, one release is 21,916 player identities and, with coordinators included, 1,610 staff identities.
- `League.clean()` and the single active tier-1 rule stay. Timelines reference that tier-1 league and do not become leagues.
- No new Django setting is required. The generator seed lives on the release. Production settings stay out of this work.
- The owner FK should use `settings.AUTH_USER_MODEL` when the model is eventually added. This design does not set `AUTH_USER_MODEL`.
- The live generator's `TEAM_COUNT = 32` is neither the 1970 founding size nor a legal stand-in for `N(year)`. A release cannot be fingerprinted against that tree. Changing the generator is a later code change, not part of this document. The replacement must not hard-code 24 for every season.

Suggested order when implementation is allowed:

1. Represent founding and expansion franchises without a fixed team-count constant (separate work). Then generate and publish one universe release: the 32 placeholder franchises, the alignment in this document, the opening cohort, the archive, 1970–2029 classes sized by `N(year)`, staff classes including coordinators, and the 1976 and 1996 expansion events with their numeric rules filled in.
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

**Treat 24 as the league size in every season from 1970 through 2029.** Rejected. Four clubs enter in 1976 and four enter in 1996. Class size, roster-slot seasons, and incoming staff use that `N(year)`. The expansion draft redistributes players already in the timeline and does not mint a second player pool.

---

## Unresolved decisions

These do not change the shared-universe versus per-timeline split. The expansion schedule, the three alignment shapes, the stay-or-apply owner flow, and the characteristic list in the audit are decided as written. What follows can still change row counts or block a publishable release.

The playable horizon is 60 seasons, 1970 through 2029 inclusive. No contraction, relocation, or further expansion is scheduled. Confirming that empty set is part of publishing the release.

| Decision | Working value | If it changes |
|----------|----------------|---------------|
| 1970 roster and later era limits | 40 + 8, then the era table, per active franchise. Later-era position quotas are not filled in yet. | Opening cohort and `slots(year)` scale directly. Quotas must sum to the era limit before publish. |
| Free-agent pool and undrafted pool | 5 per founding franchise in 1970, and 2 undrafted prospects per active franchise per draft. | Linear in those per-team constants and in `N(year)`. |
| Incoming staff floor | 8 head coaches and 6 GMs per year, plus at least one of each per expansion franchise. Coordinators: 4 per specialty, plus one per expansion franchise. | A larger candidate pool adds shared identities only. |
| Opening hire pool | The 76 unemployed head coaches and 76 unemployed GMs. | Poaching before the draft would change a second franchise's staff in that timeline only. |
| Rating draw distributions | Era tables say which traits are more common. They do not give the numeric distribution. | Required before generation. Not a new column. |
| Scout noise and the evaluation threshold | Uncertainty 20 on the owned roster, 25–60 otherwise, clearing after 4 weeks or 100 snaps. | Changes the owner display. The sim still reads `current`. |
| Development function | The formula is sketched. Coefficients are not set. | Changes timeline `current`. It does not change `initial` or `potential`. |
| Identical decisions, then the 1970 season | The season sim may use the timeline RNG, so results can diverge after kickoff. | A fully deterministic season would require the sim to ignore that seed when decisions match. |
| Dollar scale and the cap | Amounts are nominal dollars of the contract year. The 1970 veteran range is 20,000–250,000. The 1995 cap number is not set. | The cap number is required before generating or simulating a cap season. It is an era field, not a person field. |
| College counting stats | Stored, 4 seasons per player, display only. About 48–80 schools. | Dropping them removes the college rows. The school count is a generation choice. |
| Archive mean career | 5 years inside 1960–1969, implying 1,032 archive players. | The generator should emit the real headcount. The 11,520 historical season lines stay fixed. |
| Injury planning rate | 30 per franchise-season, used only as a row estimate. | Does not change the injury columns. |
| Position-weight table | The audit names which components matter by position. The numeric weights are not set. | Required before the match engine. Not a player column. |
| Weight and height | Fixed on the shared person for life. | A timeline weight column would be a schema change. This design does not add one. |

### Expansion decisions still required

| Decision | What this document already fixes | Still open |
|----------|----------------------------------|------------|
| Which seasons add clubs | 1976 adds 4. 1996 adds 4. `N` is 24, then 28, then 32 through 2029. | Nothing on the year list, unless a later decision adds a departure, a relocation, or another expansion. |
| Names, cities, and permanent slugs | Placeholders `founding-01` … `founding-24`, `expansion-1976-01` … `04`, `expansion-1996-01` … `04`. | The real identities. Placeholders are enough to generate a release. |
| Alignment | The 24-, 28-, and 32-team tables, including `slot_order`. Shared. Not standings-based. | Nothing, unless the placeholder assignment itself is rejected. |
| Owner access | Stay or apply. Awarding a club bots the old franchise. The new club starts empty. The owner sets priorities and, if staying, names core players to protect. Single-player apply is automatic. | The multi-human rule when several owners want the same club. `fair_draw` is the candidate. Whether a separate mode may start in 1976 or 1996. `start_season_year` already exists and defaults to 1970. |
| Expansion-draft integers | The event has fields for protection count, picks per new club, order, roster rules, free-agent rules, rookie slot, and hiring. | The values. Publishing with those fields empty is refused. |

### Not a reason to rebuild the person tables later

Coordinator identities share the coach table. Scout estimates are their own timeline rows. Original ratings stay on the universe person, and `current` stays on the timeline overlay. Overall is not a column. Accepting or revising this audit is the schema decision. The numeric rows in the tables above can be locked as the working defaults without adding fields.

---

## Document status

- Describes `leagues` as it exists on `main`, including the 32-team generator. This design does not change that generator.
- Confirms the shared schedule: 24 franchises for 1970–1975, 28 for 1976–1995, and 32 for 1996–2029. Names and cities stay placeholders.
- Confirms the playable timeline as 60 seasons, 1970 through 2029 inclusive. Franchise-seasons are exactly 1,792.
- If the era roster and round defaults hold: 20,884 playable player identities, 21,916 including the archive assumption, 16,028 draftable prospects, 103,352 roster-slot seasons, 1,042 GM and head-coach identities, and 1,610 staff identities with coordinators included. Those player and staff totals move if the defaults move.
- The retired totals of 28,384 and 17,424 stay labeled obsolete.
- Audits every player, GM, and head-coach characteristic, including scout estimates, and recommends generating coordinators on the coach table now.
- Adds no application code, database models, migrations, fixtures, settings, generated people, or database rows.
