# Federation Gridiron — Shared Fictional Player Universe

This document specifies the shared fictional people of Federation Gridiron and the per-timeline state that diverges from them.

This revision replaces the earlier draft of this file. That draft assumed 32 franchises, a first draft in 1971, classes generated through 2049, and 28,384 universe player rows. Those figures are obsolete. The playable timeline is **60 seasons, 1970 through 2029 inclusive**. The league has 26 franchises in 1970–1975, 28 in 1976–1994, 30 in 1995–1998, 31 in 1999–2001, and 32 in 2002–2029. That schedule is shared. Totals that still depend on roster size or draft-round defaults are marked as such in [Population and row counts](#population-and-row-counts). The retired 32-team, through-2049 arithmetic stays under [Obsolete 32-team estimate](#obsolete-32-team-estimate).

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

The confirmed schedule is 26 franchises for 1970–1975, 28 for 1976–1994, 30 for 1995–1998, 31 for 1999–2001, and 32 for 2002–2029. Permanent slugs and catalog links are not chosen. The release uses placeholder slugs until they are. `fg-founding-1` remains the shipped 24-club founding generator. `fg-founding-2` writes the 26-club founding catalog, the 1970–2029 memberships, the four expansion events, the 26 opening seats, and one provisional city and nickname for each of the 32 placeholder slugs. This document does not hard-code 26 as the size of every season.

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
| The game starts in 1970 with 26 franchises; 2 clubs enter in 1976 (28), 2 enter in 1995 (30), 1 enters in 1999 (31), and 1 enters in 2002 (32) | Shared expansion schedule. `N(1970–1975) = 26`, `N(1976–1994) = 28`, `N(1995–1998) = 30`, `N(1999–2001) = 31`, `N(2002–2029) = 32`. |
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
| **Opening cohort** | Professionals already in the league before the 1970 rookie draft: the 26 founding rosters plus that year's free-agent pool. They are not members of the 1970 draft class. |
| **1970 rookie class** | The shared draft class used before the first season. |
| **Archive cohort** | Fictional players who appear in 1960–1969 league history and are gone before the 1970 draft. Shared, so the record book is the same in every timeline. They do not play in 1970. |
| **Initial staff** | 100 GMs and 100 head coaches available at the 1970 decision point. |
| **Incoming staff class** | Shared GM and head-coach candidates who become hireable in a later offseason. Same identities in every timeline. |
| **`N(year)`** | Active franchises that season. 26 for 1970–1975, 28 for 1976–1994, 30 for 1995–1998, 31 for 1999–2001, and 32 for 2002–2029. |
| **Founding franchise** | One of the 26 clubs whose first active season is 1970. |
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

2030 would be `season_index` 61 (`2030 − 1970 + 1 = 61`). It is not a playable season. Class size for each of the 60 years is `rounds(year) × N(year)` plus that year's undrafted pool. `N(year)` comes from the shared schedule, so the 60 classes stay identical across timelines even when some years have more than 26 teams.

Pre-1970 history for the confirmed release uses years **1960–1969**, the ten seasons immediately before 1970. Those years are outside the 60. Timeline stat tables for that release use `season_year` 1970–2029. `fg-founding-2` stores no shared `HistoricalStanding` rows and does not invent 1960–1969 wins, losses, ties, or a schedule. The 1970 opening draft order is the deterministic neutral order from the release seed, the release slug, and founding franchise slugs `founding-01` through `founding-26`. A historical standing, when a later release stores one, is still validated as one of the ten seasons before that release's first playable season. The table does not hardcode those calendar years, and it does not supply the 1970 board.

The football clock is this integer year plus a phase. `TIME_ZONE` does not define it.

---

## Opening sequence

A new timeline is pinned to a published release and starts **before** the 1970 season, with the shared 1969 world still intact.

1. **Choose a franchise.** One of the 26 founding clubs. Stored as an open `TimelineFranchiseControl` for the timeline's one human participant. The other franchises are bots and have no participant row. A normal timeline starts in the release's first season, 1970 for the confirmed release. In 1976, 1995, 1999, and 2002 the owner may stay or apply for an expansion club, as described in [Owner access to expansion](#owner-access-to-expansion). A later multiplayer timeline can add participants. Phase 4 does not add invitations or a commissioner.
2. **Keep or replace the GM, then the head coach.** The shared snapshot already employs one of each on every founding franchise. The other 76 in each role are the only legal opening hires. The default is to keep both. Replacing writes timeline staff stints only.
3. **Set team direction, retention priorities, and trade preference.** These are the broad instructions in [Owner experience](#owner-experience). They replace a screen full of separate toggles.
4. **Rookie draft.** Materialize the shared 1970 class. The 1970 opening order is the approved neutral order. Its HMAC-SHA256 key is the UTF-8 decimal release seed. The message for each founding franchise is `opening-draft-order:{release_slug}:{franchise_slug}`. The full digest sorts the 26 clubs, and the franchise slug is the equal-digest tie breaker. That same order is picks 1 through 26 in every round of the 17-round 1970 draft. Every timeline on the release receives it. Later draft orders come from that timeline's own standings. The owner may make the owned club's picks or hand the draft to the GM. AI GMs draft for the other clubs from their shared tendencies. Picks are timeline rows. The class list is not modified.
5. **Confirm the last instructions** and let the GMs cut each roster to 40 active and 8 taxi. The shared opening roster remains the pre-draft roster.
6. **Simulate the 1970 season** (14 games under profile `y1970`). Later offseasons use the same seven-step flow, then the next shared class, through 2029. An expansion offseason runs the expansion event before that year's draft. After 2029 the phase is `complete`. No 2030 draft is generated.

Phases on the timeline: `choose_franchise`, `staff`, `direction`, `draft`, `instructions`, `roster_cut`, `regular`, `playoffs`, `offseason`, `complete`.

The shared snapshot is identical until step 1 writes the first timeline decision. Steps 1–4 are owner decisions, and several of them have a one-choice default. The 1970 games are the first simulated events. Random draws come from the universe-release seed and the game's own coordinates, as specified under [Deterministic scouting and results](#deterministic-scouting-and-results). They do not include the timeline id or a hash of the owner's decisions. Identical states and choices produce identical results. A different choice changes outcomes only by changing rosters, ratings, probabilities, or the sequence of events.

---

## Owner experience

The database holds the full rating vector, the histories, and the era rules. The human player is the owner. The owner hires or fires the GM and the head coach, gives each of them a few broad instructions, takes part in the rookie draft at the level they choose, and then sees the season. The owner does not call plays, set every depth-chart spot, write every contract, or approve every transaction, practice decision, or play. The normal screen does not ask the owner to set the 33 components. Detail is one step away. Exact truth stays hidden.

### What the owner sees immediately

- Name, age, primary position, and experience.
- Contract status: years left, and from 1995 the cap percent. Before 1995, a short label such as inexpensive, ordinary, or expensive, derived from ledger units.
- Recent statistics, the last season and the career line in summary.
- Health and availability: healthy, week-to-week, or out, plus weeks remaining when known.
- One overall scouting grade, derived from the revealed components that matter at his position. It is a grade, not the simulator's input.
- A potential range, taken from the potential scout band. It is a range, not the exact potential.
- Scheme fit: poor, fair, or good.
- A few strengths, a few weaknesses, and a short personality note from the revealed tags.

### What is available on request

- The component scout estimates, each with its confidence band.
- College seasons and professional season lines.
- Transaction history: drafts, stints, trades, and free-agent moves.
- Injury history.
- Awards.
- Development as a sentence, such as "his pass rush improved after the coaching change." The delta log stays hidden.
- Full contract terms in ledger units, and in cap percent once a cap exists.
- A comparison against other players at the position.

### What stays hidden

- Exact `initial`, `current`, and `potential` ratings.
- Exact potential as a single number.
- Simulator coefficients, including special-teams score weights and position weights.
- Seeds and fingerprints.
- The development formula and its intermediate numbers.
- Hidden AI decision weights for other clubs.

### Offseason flow

A normal offseason is seven decisions. Each one has a default. The GM and head coach then run the roster.

1. **GM.** Keep, or replace from the eligible pool. Default: keep.
2. **Head coach.** Keep, or replace. Default: keep. Coordinator hiring stays with them.
3. **Team direction.** One of compete, balanced, or rebuild. That single choice sets veteran preference, free-agency posture, and playing-time preference together.
4. **Retention or protection.** Up to three position groups to prioritize. In 1976, 1995, 1999, and 2002 this is also where a staying owner states what to protect, and an expansion owner states what to chase. The GM names the players.
5. **Trade preference.** Low, medium, or high. Future picks move only as far as that year's `future_pick_years` allows. The owner does not pick the partner or the contract.
6. **Rookie draft.** The owner makes the club's picks, or hands the draft to the GM. The default is to hand it over. The owner can take the draft back for one round.
7. **Final instructions.** Offensive emphasis, defensive emphasis, and fourth-down tendency. Then the season simulates.

Team direction sets the GM's default veteran-versus-youth preference: compete leans veteran, rebuild leans youth, and balanced stays in the middle. The owner may override that preference. It does not add a separate free-agency slider. The GM turns direction into contracts, cuts, signings, trade construction, expansion selections, undrafted signings, and roster balance. The head coach turns the three tendencies into the depth chart, the weekly lineup, special teams, fatigue and injury decisions, coordinator use, and the detailed game plan. An optional playing-time preference, veterans or youth, is stored only when the owner sets one.

An optional involved mode, off by default, lets the owner pin up to five named players to protect or to target in an expansion year. It does not open the full rating editor or the full depth chart.

---

## Sharing rules

1. Name, birth, college, position, body, traits, initial ratings, potential, pre-1970 statistics, pre-1970 employment, and draft-class membership are stored once, on the release.
2. A timeline stores the foreign key to that person plus what diverges after the opening decisions: draft picks, assignments, injuries, current ratings, contracts, 1970–2029 statistics, awards, and career events inside the timeline.
3. Timeline services write timeline tables only.
4. The generator writes universe tables only.
5. A future draft class or incoming staff class stays a universe row until that timeline's clock reaches the class year. Creating a timeline does not insert overlay rows for the 1988 class.
6. The 1970 class is materialized during the opening draft, before the 1970 season, because that draft is part of setup rather than a later offseason.
7. Publishing freezes the release. A correction is a new release. Existing timelines stay pinned, so a retune cannot change the 1970 roster or a class a timeline has already seen.
8. Opening contracts and opening staff assignments stay shared templates. The implemented `start_timeline` copies opening player assignments into timeline rows and leaves contracts and staff on the release. The 26 opening GM seats and the 26 opening head-coach seats remain `OpeningStaffAssignment` rows. Later cuts, extensions, and firings belong to a later service.
9. Pre-1970 counting stats are the shared record book. They are display. Starting ratings and traits are what the 1970 simulator reads. The generator builds the counting stats so they agree with those ratings; the sim does not replay 1960–1969.
10. Franchise identity, first and last active seasons, per-season conference and division membership, and expansion events are stored once on the release. Every timeline on that release introduces the same franchises in the same seasons.
11. A year's draft class is fixed by the release and by `N(year)` on that schedule. Standings inside a timeline change pick order. They do not add or remove prospects.
12. An expansion draft assigns people who are already in that timeline. It does not create a universe player, GM, or coach. The active roster of a new club is guaranteed by shared veterans, shared drafted rookies, and shared undrafted prospects. Free agents may replace an undrafted signing. A shortage of them cannot stop the season.

---

## Universe release and identity

| Field | Purpose |
|-------|---------|
| `slug` | Stable id, unique. Example: `fg-1970-1`. |
| `generator_version` | Algorithm id. Example: `fg-player-gen-2`. |
| `seed` | Integer stored on the release row. Not a Django setting. |
| `root_league` | FK to the tier-1 `League`. `on_delete=PROTECT`. |
| `founding_franchise_count` | 26 for the confirmed release. This is the 1970 size, not the size of later seasons. `fg-founding-1` still stores 24 on the draft release it writes. |
| `expansion_schedule_version` | Integer on the release. Filling in expansion years, clubs, or rules bumps the version. A published release does not change its schedule in place. |
| `catalog_fingerprint` | SHA-256 of the franchise list (slug, first season, last season) plus each season's conference and division membership plus each expansion event. A timeline stays bound to that fingerprint. |
| `validation_fingerprint` | SHA-256 of the publication checks: quota sums, the expansion active-roster arithmetic, staff-signature uniqueness, and the population totals this release claims. The expansion check uses the shared veteran, rookie, and undrafted pools only. It does not count timeline free agents. Publish is refused when any check fails. Taxi capacity is not part of the expansion minimum. An expansion year may use an undrafted multiplier other than 2. This release uses 2 in every year. |
| `first_season_year` | 1970. |
| `season_count` | 60. |
| `last_season_year` | 2029. |
| `status` | `draft` or `published`. |
| timestamps | Same pattern as `leagues`. |

`start_timeline` requires a published release. `N(year)` is computed from the schedule. It is not a settings constant and it is not `TEAM_COUNT`.

The implemented `start_timeline` starts one human 1970 campaign on a published `fg-founding-2` release. It does not publish a release and it does not create a web page. Inputs are the release slug, the expected seed, the expected generator version, the Django user id, the campaign name, and the founding franchise slug. The service locks that `UniverseRelease` first and holds the lock through the identity lookup, the creation, and the commit. Lock order after that row is the `Timeline`, the `TimelineSeason`, the opening-state writes, then the `OwnerDirective`.

One user may start more than one timeline on the same release. While the release lock is held, the service loads timelines on that release where the user is `created_by` or a participant, then compares each stored `name` with the requested name using Python `==`. The name is not part of the SQL filter. The comparison does not trim, casefold, or Unicode-normalize. Zero matches create a distinct timeline. One match returns unchanged, and writes nothing, only when the timeline, participant, control, season, directive, and opening overlay all match the request. A partial match, including a matching name with no participant, fails closed. More than one exact match fails closed and reports those timeline ids. A different name may use any legal founding franchise, including one this user controls on another timeline. There is no billing limit and no uniqueness migration in this service. A writer that never takes the release lock can still insert a second Python-equal name, because no database unique covers release, user, and name. Whether to add that constraint is a later decision.

The created campaign is one active `Timeline` in phase `direction`, year 1970, season index 1; one active `TimelineParticipant`; one open `TimelineFranchiseControl`; one `TimelineSeason` on `y1970`, also in phase `direction`, with `expansion_gate` `not_expansion` and the progress flags false; and one unlocked `OwnerDirective` at the model defaults. `initialize_opening_state` then writes 1,378 `TimelinePlayer` rows, 1,378 `PlayerSeasonRating` rows, and 1,378 open week-0 `RosterMembership` rows, all available. That call runs only when the entire opening overlay is absent. The counts are 494 starters, 546 backups, 208 taxi players, and 130 free agents: 1,248 rostered memberships and 130 free-agent memberships with a null franchise. Current ratings equal the shared initial ratings. Missing franchise-control rows are the AI clubs. The service checks the approved neutral 26-club order in memory and does not store it. It does not call `materialize_draft_class`. It does not create prospect overlays, draft picks, a board, games, standings, contracts, timeline staff, development events, expansion choices, pins, kickoff rows, or audit rows. The management command is `start_timeline`. It requires every input, calls only the service, and prints `created` or `unchanged` with the five ids and the opening counts.

### Publication

`evaluate_release_publication` is read-only. By default it compares the fingerprints stored on the release with freshly computed `catalog-sha256-v1` and `validation-sha256-v1` digests. It does not save the release or any child. An optional `candidate_fingerprints` argument compares those candidates with a fresh computation and still does not write.

`publish_universe_release` is the only writer of the publication fields. It runs in `transaction.atomic()`, locks the release with `select_for_update()` through the shared release lock, and checks the supplied slug, seed, generator version, and league before it evaluates. It computes both digests from the locked draft and evaluates every gate against those candidates before any publication field is saved. Fingerprints are stored only when every required gate passes, in the same transaction as `status = published` and a timezone-aware `published_at`. The write touches only `catalog_fingerprint`, `validation_fingerprint`, `status`, and `published_at`. A failed gate or any exception rolls the transaction back, so placeholder or blank fingerprints stay as they were.

Publication is irreversible. The service has no unpublish path. An identical rerun of a published release returns unchanged only when the supplied identity matches, `published_at` is present, both stored fingerprints are lowercase 64-character hex, recomputation matches those stored digests, and every read-only gate still passes. That path preserves `published_at` and both fingerprints. Any other result on a published release fails closed and does not rewrite the row. Timeline creation still requires `status = published`.

Production publication freezes franchise `display_name` values and provisional city and nickname values, including rows whose city or nickname status is still `provisional`. After `status` is `published`, an ordinary save or delete of `UniverseFranchise` or `ProvisionalFranchiseName` is refused, so those stored strings stay as published. `start_timeline` does not change them. The catalog fingerprint does not include `display_name`. The validation fingerprint includes franchise names and provisional names. Tests of publication and of timeline start use disposable published fixtures. They do not publish the hosted release.

The management command is `publish_universe_release`. It takes `--league-id`, `--slug`, `--seed`, and `--generator`, calls the service, and prints `published` or `unchanged`. It has no force, skip-validation, repair, unpublish, or fingerprint-only option.

An ordinary `save()` or instance `delete()` of a release-owned row opens `transaction.atomic()` on the database alias that `save()` or `delete()` would use, locks that row's persisted `UniverseRelease` with `select_for_update()`, and reads `status` from the locked database row. An existing row resolves its owner from the stored primary key and the stored relationship chain. The helper locks that release, re-reads the stored owner, and refuses when the in-memory owner differs. It does not lock the proposed release and it does not move the row. A new row locks the release it proposes to join. Validation and the child write finish before that transaction ends. The held-lock record is the database alias plus the release primary key. `UniverseRelease` is the first application lock for release content. Publication, the sanctioned generators, provisional replacement, and these child writes all take that lock and do not lock a child row first. A nested save locks the same release on the same alias again. A second release, or the same primary key on another alias, is refused before any SELECT or row lock, and the held-lock record stays as it was.

`QuerySet.update()`, `bulk_create()`, `bulk_update()`, `QuerySet.delete()`, and SQL from outside this application skip model hooks. The sanctioned bulk writers already hold the release lock and call `assert_release_lock_held` before those operations. They stay bulk operations. An unsanctioned queryset bypass or arbitrary external SQL is outside this guarantee. This design adds no database trigger. SQLite tests record the `select_for_update()` call and the following statement. They do not prove that a second connection waits. A disposable MariaDB concurrency smoke test is required after merge and is not run here.

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

Rules are fictional Federation Gridiron rules. They borrow the broad shape of football history. They are not a description of the NFL or any real league.

| Layer | Question | Effect on an existing person |
|-------|----------|------------------------------|
| **Origin era** | What kind of footballer or coach was created for this cohort? | Stored on the person and left in place. |
| **Season rule profile** | What roster, draft, contract, waiver, cap, schedule, and playoff rules apply this year? | The person is unchanged. The sim reads `EraRuleProfile` for the timeline's current year. |

`EraRuleProfile` is shared, versioned data on the universe release. One row covers every season in a contiguous span where the full rule tuple is constant. A timeline looks up the row for its `season_year`. It does not invent a local rule. Two timelines on one release play under the same profile in the same year.

### Market eras

Player movement uses three markets. Roster size and schedule length still change inside a market. Those operational changes are separate profile rows below.

| Market | Seasons | How a veteran changes teams |
|--------|---------|------------------------------|
| `controlled` | 1970–1986 | The current club keeps veteran rights when a contract ends. Trades, releases, waivers, and the draft move players. There is no unrestricted free agency and no salary cap. |
| `transitional` | 1987–1994 | Limited free agency exists. Restrictions and draft-pick compensation still favor the current club. There is no salary cap. |
| `modern` | 1995–2029 | Unrestricted free agency begins for veterans who have enough accrued seasons. A salary cap begins. Cap management becomes a simulation input. |

| Rule | 1970–1986 controlled | 1987–1994 transitional | 1995–2029 modern | Status |
|------|----------------------|------------------------|------------------|--------|
| Active roster | 40 through 1977, then 45 | 47 | 53 | Working design |
| Taxi squad | 8 | 8 | 8 | Working design |
| Injured reserve | Maximum 2. Minimum stay 8 weeks. No return the same season. Does not count against the active limit. | Maximum 3. Minimum stay 4 weeks. One player may return after the minimum. | Maximum 4. Minimum stay 4 weeks. Up to two may return. | Needs approval |
| Draft rounds | 17 through 1977, then 12 | 8 | 7 | Working design |
| Rookie contracts | 1–3 years. The club controls the player. No draft-slot wage scale. | 2–3 years. Still club-controlled. | 4 years. Pay is ordered by draft slot as a percent of that year's cap. Undrafted rookies get 1 year at the minimum. No fifth-year option. | The lengths are the recommendation. The slot-percent table needs approval. |
| Veteran contracts | 1–5 years. The club keeps the player's rights at the end of the deal. | 1–5 years. Fewer than 4 accrued seasons: club-controlled. 4 or more: restricted free agency with a tender. | 1–5 years, up to 7 on the timeline copy. 4 accrued seasons: unrestricted. Fewer than 4: restricted. | The 4-season threshold needs approval. |
| Trade deadline | Through the Tuesday after week 8. | Same | Same | Week 8 needs approval. Trades themselves are required in every era. |
| Future draft picks | May be traded, at most 1 year ahead. | At most 2 years ahead. | At most 3 years ahead. | The year limits need approval. |
| Waivers | Every release passes through waivers. Claim order is the inverse of the prior standings. The claim takes the existing contract. | Players under 4 accrued seasons always hit waivers. Older players hit waivers only before the trade deadline. | Players under 4 accrued seasons hit waivers. Older players are unrestricted if released after the roster-reduction date. | Follows the 4-season threshold. |
| Free-agency type | Veteran reserve. No unrestricted class. | Restricted free agency with compensation. | Unrestricted plus restricted. | Working design for the type. |
| Retention | Veteran reserve. The club tenders a qualifying offer to keep rights. | Restricted tender. The original club may match. | Restricted tender below 4 seasons. One franchise tag per club per season. | Tag formula needs approval. |
| Compensation for a lost free agent | None, because the player does not leave as a free agent. | One draft pick in round 1, 2, or 3, set by the tender. | None for an unrestricted player. A draft pick for a restricted player. A tagged player does not leave. | Tender rounds need approval. |
| Salary cap | Off. | Off. | On. The amount is not set in this document. | The on/off switch is the working design. The amount needs approval. |
| Minimum salary | A ledger-unit floor. The number is not set. | Same | Once a cap exists, the recommended shape is a small percent of that cap. The percent is not set. | Needs approval. |
| Franchise tag | Not used. | Not used. | One per club. Recommended salary: the average of the five highest charges at that position from the prior season. | The formula needs approval. It does not set the cap. |
| Playoff field | 1970–1975: 8 teams, 3 division winners and 1 wild card per conference. 1976–1994: 8 teams, 2 division winners and 2 wild cards per conference. | 1976–1994 continues: 8 teams, 2 division winners and 2 wild cards per conference. | 1995–1998: 8 teams, 3 division winners and 1 wild card per conference. 1999–2001 uses the approved `twelve_team_fill_to_six` value: 12 teams, 6 per conference, every division winner, wild cards fill that conference to 6, top two seeds bye, first round 3 vs 6 and 4 vs 5. Conference-1 supplies 4 winners and 2 wild cards. Conference-2 supplies 3 and 3. From 2002 the documented field is 4 winners and 2 wild cards in each conference. | The 1999–2001 stored value is approved. Later playoff card fields remain unresolved. Order inside either group is still open. |
| Schedule length | 14 games through 1977, then 16. | 16 | 16 through 2020, then 17. | Working design |
| Overtime | 1970–1973: regular season may end tied. 1974–1986: one 15-minute sudden-death period, then a tie. Playoffs continue until there is a winner. | Sudden death, same as 1974. | 1995–2011: sudden death. 2012–2029: each team may possess the ball once unless the first team scores a touchdown. One period in the regular season. Playoffs continue. | The 1974 and 2012 changes need approval. |
| Statistics | Sacks are recorded in every year. Leaderboards treat them as unofficial through 1986. | Sacks are an official statistic. | Same | Working design |
| Style of play | `run-first` through 1977, then `passing-rules`. Higher injury cost early. Fullbacks are common. | `timing-specialists`. Pass rush and nickel coverage become ordinary. | `cap-and-size` through 2003, `spread-influence` through 2013, then `modern-passing`. | The style keys on the 12 spans are approved. Numeric sim weights need approval. |

Trades of players and of draft picks are legal in every market, before that year's deadline, and only as far into the future as that market allows. A trade is a timeline transaction. It does not create a person.

### Money

Contract figures are integer **ledger units**, not a 60-year series of nominal dollars. The old 20,000–250,000 opening range is withdrawn.

Before 1995 the cap is off, so units are a negotiation scale only. From 1995 the owner interface shows each contract as a percent of that season's cap. The stored number stays in units. This document does not choose the cap total or the minimum. A 1995-or-later profile cannot be published while `cap_limit` is empty. The recommended shape, which still needs approval, is to set the minimum as a small percent of whatever cap is approved, so the owner never has to read an inflated dollar figure.

### EraRuleProfile fields

| Field | What it stores |
|-------|----------------|
| `profile_key` | Stable id, unique on the release. |
| `first_year`, `last_year` | Inclusive span. Spans do not overlap. |
| `market` | `controlled`, `transitional`, or `modern`. |
| `style_key` | Generation and sim emphasis: `run-first`, `passing-rules`, `timing-specialists`, `cap-and-size`, `spread-influence`, `modern-passing`. |
| `active_limit`, `taxi_limit` | Roster caps. |
| `active_quota`, `taxi_quota` | A count for each of the 19 positions. Each map must sum to its limit. Publish is refused otherwise. |
| `ir_max`, `ir_min_weeks`, `ir_returns_allowed` | Injured-reserve rule. |
| `draft_rounds` | Rookie-draft length. |
| `rookie_contract_years_min`, `rookie_contract_years_max` | Rookie deal length. |
| `rookie_slot_scale` | Null until 1995. Then an ordered table of cap percents by pick. Empty until approved. |
| `veteran_contract_years_min`, `veteran_contract_years_max` | Veteran deal length. |
| `accrued_seasons_for_ufa` | Null in the controlled market. The transitional market uses this as the restricted threshold. The modern market uses it as the unrestricted threshold. |
| `trade_deadline_week` | Last week in which a trade is legal. |
| `future_pick_years` | How many future drafts may be traded. |
| `waiver_rule` | `all_releases`, `veterans_until_deadline`, or `veterans_until_cutdown`. |
| `free_agency_type` | `reserve`, `restricted`, or `unrestricted_and_restricted`. |
| `fa_compensation` | `none`, `tender_pick`, or `restricted_only`. |
| `cap_status` | `off` or `on`. |
| `cap_limit` | Null while the cap is off, and null until approved while the cap is on. |
| `minimum_salary` | Ledger units. Null until approved. |
| `franchise_tag` | `off`, or `top_five_position_average`. |
| `playoff_format` | `eight_team_3_and_1`, `eight_team_2_and_2`, `twelve_team_fill_to_six`, or `twelve_team_4_and_2`. `twelve_team_fill_to_six` is the approved stored value for 1999–2001: 12 teams, 6 qualifiers per conference, every division winner, wild cards filling that conference to 6, byes for the top two seeds in each conference, and a first round of 3 vs 6 and 4 vs 5. Conference-1 supplies 4 division winners and 2 wild cards. Conference-2 supplies 3 division winners and 3 wild cards. Order inside either group is outside the value. `twelve_team_4_and_2` is the documented field from 2002, once both conferences have four divisions. |
| `schedule_length` | Regular-season games each franchise plays, such as 14, 16, or 17. This is not the highest calendar week. A 17-game season can span 18 weeks when one of them is a bye. |
| `overtime_rule` | `tie`, `sudden_death`, or `both_teams_possess`. |
| `sack_leaderboard` | `unofficial` or `official`. |

### Profile calendar

The profile key, the season span, and the style key on each row are approved. `y1970` is the approved card. On every later row, market, roster limits, rounds, games, overtime, cap status, and the playoff description other than the approved `twelve_team_fill_to_six` value for 1999–2001 stay documented and unresolved. Cap amounts stay unset. No seeding order is stored on these rows.

| Profile | Years | Style | Market | Active | Taxi | Rounds | Games | Playoff | Overtime | Cap | Status |
|---------|-------|-------|--------|--------|------|--------|-------|---------|----------|-----|--------|
| `y1970` | 1970–1973 | `run-first` | controlled | 40 | 8 | 17 | 14 | 8, three winners and one wild card | Tie allowed | Off | Approved card |
| `y1974` | 1974–1975 | `run-first` | controlled | 40 | 8 | 17 | 14 | 8, three winners and one wild card | Sudden death | Off | Span and style approved. Other fields unresolved |
| `y1976` | 1976–1977 | `run-first` | controlled | 40 | 8 | 17 | 14 | 8, two winners and two wild cards | Sudden death | Off | Span and style approved. Other fields unresolved |
| `y1978` | 1978–1986 | `passing-rules` | controlled | 45 | 8 | 12 | 16 | 8, two winners and two wild cards | Sudden death | Off | Span and style approved. Other fields unresolved |
| `y1987` | 1987–1994 | `timing-specialists` | transitional | 47 | 8 | 8 | 16 | 8, two winners and two wild cards | Sudden death | Off | Span and style approved. Other fields unresolved |
| `y1995` | 1995–1998 | `cap-and-size` | modern | 53 | 8 | 7 | 16 | 8, three winners and one wild card | Sudden death | On, amount unset | Span and style approved. Other fields unresolved |
| `y1999` | 1999–2001 | `cap-and-size` | modern | 53 | 8 | 7 | 16 | `twelve_team_fill_to_six` | Sudden death | On, amount unset | Span, style, and playoff format approved. Other fields unresolved |
| `y2002` | 2002–2003 | `cap-and-size` | modern | 53 | 8 | 7 | 16 | 12, four winners and two wild cards | Sudden death | On, amount unset | Span and style approved. Other fields unresolved |
| `y2004` | 2004–2011 | `spread-influence` | modern | 53 | 8 | 7 | 16 | 12, four winners and two wild cards | Sudden death | On, amount unset | Span and style approved. Other fields unresolved |
| `y2012` | 2012–2013 | `spread-influence` | modern | 53 | 8 | 7 | 16 | 12, four winners and two wild cards | Both teams may possess | On, amount unset | Span and style approved. Other fields unresolved |
| `y2014` | 2014–2020 | `modern-passing` | modern | 53 | 8 | 7 | 16 | 12, four winners and two wild cards | Both teams may possess | On, amount unset | Span and style approved. Other fields unresolved |
| `y2021` | 2021–2029 | `modern-passing` | modern | 53 | 8 | 7 | 17 | 12, four winners and two wild cards | Both teams may possess | On, amount unset | Span and style approved. Other fields unresolved |

The approved style keys are `run-first` for 1970–1977, `passing-rules` for 1978–1986, `timing-specialists` for 1987–1994, `cap-and-size` for 1995–2003, `spread-influence` for 2004–2013, and `modern-passing` for 2014–2029. Each span holds one of those keys. A season with more franchises uses the same per-team quota and the same round count, multiplied by `N(year)`.

`twelve_team_fill_to_six` is the approved stored value for 1999–2001: 12 teams, 6 from each conference, every division winner, wild cards filling that conference to 6, byes for the top two seeds in each conference, and a first round of 3 vs 6 and 4 vs 5. Conference-1 supplies 4 division winners and 2 wild cards. Conference-2 supplies 3 division winners and 3 wild cards. Order inside either group is outside that value. From 2002 the documented field is `twelve_team_4_and_2`: conference-2 gains its fourth division, so both conferences supply 4 winners and 2 wild cards. The field size, the byes, and the first-round pairings stay. 1995–1998 have three divisions in each conference, so they use the 8-team field of 3 winners and 1 wild card. 1976–1994 keep 2 winners and 2 wild cards. 1970–1975 keep 3 winners and 1 wild card. Those later playoff descriptions, other than the approved 1999–2001 value, remain unresolved card fields. The corrected alignment gives each conference three divisions in 1976–1994, and it gives conference-1 three divisions in 1999–2001. `eight_team_2_and_2` and `twelve_team_fill_to_six` stay the stored values, so their winner counts are not rewritten from those division sizes. `schedule_length` stays 16 for 1982 and 1987.

### Positions

There are **19** primary positions:

`QB`, `HB`, `FB`, `WR`, `TE`, `LT`, `LG`, `C`, `RG`, `RT`, `DE`, `DT`, `ILB`, `OLB`, `CB`, `FS`, `SS`, `K`, `P`.

Left and right offensive-line positions are separate. A hole at left tackle is not filled by a right tackle unless that player lists the other side as a secondary position. `HB` is the halfback. The earlier labels `RB`, `OT`, `OG`, `LB`, and `S` are retired.

These are roster roles, not primary positions. A player may hold several at once. Holding one does not change `position`.

`kick_returner`, `punt_returner`, `long_snapper`, `kick_coverage`, `punt_coverage`, `emergency_kicker`, `emergency_punter`.

### Position quotas

Every profile stores an active quota and a taxi quota. The generator refuses the release unless the active counts sum to `active_limit` and the taxi counts sum to `taxi_limit`. Future profiles use that same check. They do not get a free-form depth chart that can drift off the limit.

The 1970 opening roster, profiles `y1970`, `y1974`, and `y1976`, active limit 40:

| Position | Active |
|----------|--------|
| QB | 2 |
| HB | 2 |
| FB | 2 |
| WR | 4 |
| TE | 2 |
| LT | 2 |
| LG | 1 |
| C | 2 |
| RG | 1 |
| RT | 2 |
| DE | 3 |
| DT | 4 |
| OLB | 4 |
| ILB | 2 |
| CB | 3 |
| FS | 1 |
| SS | 1 |
| K | 1 |
| P | 1 |

Those 19 counts sum to 40. Taxi for every profile in this design, summing to 8: QB 1, HB 1, WR 1, LG 1, RG 1, DE 1, ILB 1, FS 1. On the 1970 organization, LT, LG, C, RG, and RT each have two players once taxi is included. FS has one active player and one taxi player. SS has one active player and no taxi player. A corner or strong safety who lists `FS` as a secondary position is the emergency free safety.

Recommended quotas for the later limits, same taxi eight:

| Position | 45 active, 1978–1986 | 47 active, 1987–1994 | 53 active, 1995–2029 |
|----------|----------------------|----------------------|----------------------|
| QB | 2 | 2 | 2 |
| HB | 2 | 2 | 2 |
| FB | 2 | 1 | 1 |
| WR | 6 | 7 | 9 |
| TE | 2 | 2 | 2 |
| LT | 2 | 2 | 2 |
| LG | 2 | 2 | 2 |
| C | 2 | 2 | 2 |
| RG | 1 | 2 | 2 |
| RT | 2 | 2 | 2 |
| DE | 3 | 3 | 4 |
| DT | 4 | 4 | 4 |
| OLB | 4 | 4 | 4 |
| ILB | 2 | 2 | 3 |
| CB | 4 | 5 | 6 |
| FS | 1 | 1 | 2 |
| SS | 2 | 2 | 2 |
| K | 1 | 1 | 1 |
| P | 1 | 1 | 1 |

The 45-man row sums to 45, the 47-man row to 47, and the 53-man row to 53. Direction: fullbacks decline, receivers and corners rise, LG gains a second active player in 1978, and RG gains one in 1987. These three rows are the recommended fill. Rejecting an active limit means rewriting that row and rechecking the sum, and it also changes the expansion arithmetic below.

The 1970 rookies are not in the opening quota. They enter through the draft, and the post-draft cut returns each franchise to that year's active limit plus taxi.

### Special-teams selection

`return_vision` and `special_teams` are stored components, not derived scores.

`return_vision` is the ability to find a lane and to choose a return or a fair catch. Speed and elusiveness still decide what happens after the choice. Awareness is ordinary football intelligence, and it does not by itself make a returner.

`special_teams` is coverage, blocking, and lane discipline. Deriving it from speed, tackling, and discipline would force every fast tackler to be a good gunner and would make a dedicated special-teams player impossible. Both are new components. The vocabulary is **33** integers.

The head coach assigns roles each week from healthy active players. The assignment is deterministic from the ratings, the injury list, and the coach's fatigue and injury-management ratings. The owner does not pick the units in the normal interface.

| Role | Who is eligible | Recommended score | Depth |
|------|-----------------|-------------------|-------|
| Kick returner | `HB`, `WR`, `CB`, `FS`, `SS` | `0.40 return_vision + 0.25 speed + 0.20 elusiveness + 0.15 agility`, plus 5 if the primary position is `HB` or `WR` | 2 |
| Punt returner | `WR`, `CB`, `HB`, `FS`, `SS` | `0.35 return_vision + 0.25 catching + 0.20 agility + 0.20 speed` | 2 |
| Long snapper | `C`, `TE`, `ILB`, `OLB` | `0.50 special_teams + 0.30 discipline + 0.20 awareness`, plus 15 if the primary position is `C`. From 1970 through 1986, the starting center snaps when he is within 10 points of the leader. From 1987 on, the highest score wins, so the starting center can stay an offensive player. | 1 |
| Kick coverage and punt coverage | Anyone except the kicker or punter, the returner, and the snapper | `0.40 special_teams + 0.25 speed + 0.20 tackling + 0.15 discipline`. A high `fatigue_management` or `injury_management` lowers the score of current starters so they are less often on the unit. | 10 each |
| Emergency kicker | Any healthy player when no healthy `K` is on the roster | `kick_power + kick_accuracy`. Ties prefer `P`, then `QB`. | 1 for the weeks the kicker is out |
| Emergency punter | Any healthy player when no healthy `P` is on the roster | `punt_power + punt_accuracy`. Ties prefer `K`, then `QB`. | 1 for the weeks the punter is out |

The score weights are sim coefficients. They sit with the other unset numeric weights and need approval. The eligible positions, the role list, and the rule that a role does not change primary position are the working design.

`TimelineSpecialTeamsRole` stores the result: player, season, week, role, and depth. One player may have several rows in the same week.

### Ratings vocabulary

One component set for every era: **33** integers, 0–100, higher is better. Eras change how often high values are drawn. They do not add a column per decade. A displayed overall is derived from the components that matter for the player's position. The sim does not read that overall, and overall is not stored.

| Group | Components |
|-------|------------|
| Physical | `speed`, `acceleration`, `agility`, `strength`, `stamina`, `durability` |
| Mental | `awareness`, `discipline`, `processing` |
| Offense | `throw_power`, `throw_accuracy`, `catching`, `route_running`, `carrying`, `elusiveness`, `run_blocking`, `pass_blocking` |
| Defense | `tackling`, `coverage`, `pass_rush`, `run_defense` |
| Special teams | `kick_power`, `kick_accuracy`, `punt_power`, `punt_accuracy`, `return_vision`, `special_teams` |
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
N(1970) = 26
rostered = N(1970) × (40 + 8) = 1,248
free agents = 5 × N(1970) = 130
opening cohort = 1,378
```

That 1970 arithmetic is fixed, because the league starts with 26 clubs. It is not reused as `N(year)` for 1971–2029.

Every opening player has at least one prior pro season. Age is drawn first (starters mostly 24–30, backups 22–32, taxi 21–24, free agents slightly older). `birth_year = 1970 − age` at the 1970 offseason. Rookies born around 1948 are in the draft class, not on these rosters.

`OpeningAssignment`: one per opening player. A free agent has a null franchise and a null depth. Position comes from `UniversePlayer`, not from the assignment. Starters and backups share the active depth chart for each franchise and position. Taxi players use a separate taxi depth chart. Each chart may begin at depth 1. The publication key is release, franchise, player position, bucket, and depth. Starter and backup map to the active bucket. Taxi maps to the taxi bucket. Free agents are outside that key. There is no stored bucket column.

`OpeningContract`: one shared template per rostered veteran (1,248). Free agents have none. Copied into the timeline at start.

### 1970–2029 draft classes

`DraftClass` is unique on `(release, year)` for each year from 1970 through 2029.

```text
draftable(year) = era(year).draft_rounds × N(year)
undrafted(year) = undrafted_multiplier(year) × N(year)
class size(year) = draftable(year) + undrafted(year)
```

`undrafted_multiplier` is 2 in every year, including 1976, 1995, 1999, and 2002. The earlier one-year multiplier of 3 is removed. It existed so four 1996 clubs could reserve 80 undrafted prospects when `2 × 32` was 64. None of the reserved counts below exceeds `2 × N(year)`. `N(year)` is the shared schedule, so every timeline on the release receives the same class. Round counts and the multiplier are design defaults. The row counts below are exact only while those defaults hold. Class size is the size of one year. Prospects are that size times the years in the row.

| Years | N | Rounds | Draftable | Undrafted | Class size | Years | Prospects |
|-------|---|--------|-----------|-----------|------------|-------|-----------|
| 1970–1975 | 26 | 17 | 2,652 | 312 | 494 | 6 | 2,964 |
| 1976–1977 | 28 | 17 | 952 | 112 | 532 | 2 | 1,064 |
| 1978–1986 | 28 | 12 | 3,024 | 504 | 392 | 9 | 3,528 |
| 1987–1994 | 28 | 8 | 1,792 | 448 | 280 | 8 | 2,240 |
| 1995 | 30 | 7 | 210 | 60 | 270 | 1 | 270 |
| 1996–1998 | 30 | 7 | 630 | 180 | 270 | 3 | 810 |
| 1999–2001 | 31 | 7 | 651 | 186 | 279 | 3 | 837 |
| 2002–2003 | 32 | 7 | 448 | 128 | 288 | 2 | 576 |
| 2004–2013 | 32 | 7 | 2,240 | 640 | 288 | 10 | 2,880 |
| 2014–2029 | 32 | 7 | 3,584 | 1,024 | 288 | 16 | 4,608 |
| **1970–2029** | | | **16,183** | **3,594** | | **60** | **19,777** |

A prospect stores a shared `board_rank` and a pool of `draftable` or `undrafted`. The prospect does not store a destination team. Round, pick, and franchise are timeline facts. True ratings stay on the universe player. Any scout fog is a timeline report, never a column on the shared prospect.

`materialize_draft_class` is idempotent on `(timeline, universe_player)`.

### Archive cohort (record book only)

Shared pro history covers 1960–1969 at the same 26 × 48 roster:

```text
historical roster-seasons = 26 × 48 × 10 = 12,480
```

Planning assumption: a mean stay of 5 seasons inside that window, so distinct historical players = `12,480 / 5 = 2,496`. The 1,378 opening players are part of that set. The rest are the archive:

```text
archive ≈ 2,496 − 1,378 = 1,118
```

Archive players have universe identities, pre-1970 stats, and awards. They are not on 1970 rosters, not in the free-agent pool, and not placed on timelines as active players. The generator's actual archive count is whatever headcount fills 12,480 lines once the opening cohort is seated in 1969. 1,118 is the planning figure tied to the 5-year mean. The mean itself is still an assumption.

### Staff

**Initial, hireable before the 1970 draft:** 100 GMs and 100 head coaches.

| | Employed on a 1970 franchise | Unemployed, available to hire |
|--|------------------------------|-------------------------------|
| GM | 26 | 74 |
| Head coach | 26 | 74 |

Each of the 100 in a role has a distinct combination of strength tags, weakness tags, trait tags, and career-history signature. Ages in 1970 run about 32–65. Employed head coaches skew older. The unemployed 74 are head-coach candidates. Some of them held coordinator posts before 1970. They are not the coordinator pool. Coordinators are additional people, specified under [Coordinators](#coordinators). `fg-founding-1` still seats 24 of each role. The 26 seats belong to the later generator version.

**Incoming GM and head-coach classes, 1971–2029.** `E(1976) = 2`, `E(1995) = 2`, `E(1999) = 1`, `E(2002) = 1`, and `E(year) = 0` in every other year. The floor is:

```text
incoming_hc(year) >= 8 + E(year)
incoming_gm(year) >= 6 + E(year)
```

That is 8 head coaches and 6 GMs in the 55 years with no new club. The four expansion years add the new clubs: 10 and 8 in 1976, 10 and 8 in 1995, 9 and 7 in 1999, and 9 and 7 in 2002.

```text
future head coaches = 59 × 8 + (2 + 2 + 1 + 1) = 478
future GMs = 59 × 6 + (2 + 2 + 1 + 1) = 360
all head coaches = 100 + 478 = 578
all GMs = 100 + 360 = 460
GM and head-coach identities = 1,038
```

The 8 and 6 are still a staffing assumption. The `E(year)` addend is the minimum so each new club has a shared candidate. Adding more candidates above the floor increases the shared staff count and does not change the franchise schedule.

The implemented `start_timeline` does not create staff overlay rows. Opening GM and head-coach seats stay on the shared `OpeningStaffAssignment` rows. A later service may create overlay rows for the initial unemployed staff when the owner can hire before the season. An incoming staff class is materialized in the offseason of its debut year, the same way a draft class is. New staff are not invented inside one timeline, so two timelines are hiring from the same people.

Initial staff draw schemes that exist in 1970: `power_run`, `option`, `pro_style` on offense, and `stop_run` or `balanced` on defense. A coach who debuts in a later class may draw that era's schemes (`west_coast` from 1987, `air_raid` from the spread era).

### 1970 opening draft order

`fg-founding-2` has no shared pre-1970 standings and no `HistoricalStanding` rows. The 1970 order is the approved deterministic neutral order: HMAC-SHA256 of the UTF-8 decimal release seed and `opening-draft-order:{release_slug}:{franchise_slug}`, sorted by the full digest and then the franchise slug. That same 26-club sequence is used in every round. Later draft orders come from each timeline's actual standings. Expansion clubs follow the rookie-draft slot rule on their expansion event; that rule changes order only, not the shared class.

---

## Expansion

The Federation Gridiron begins with 26 franchises in 1970. Two expansion franchises enter for 1976, two for 1995, one for 1999, and one for 2002. From 2002 through 2029 the league has 32 active franchises unless a later decision explicitly adds a departure, a relocation, or another expansion. No departure is scheduled.

| Seasons | Active franchises | What changes |
|---------|-------------------|--------------|
| 1970–1975 | 26 | Founding league |
| 1976–1994 | 28 | 2 clubs enter in 1976 |
| 1995–1998 | 30 | 2 clubs enter in 1995 |
| 1999–2001 | 31 | 1 club enters in 1999 |
| 2002–2029 | 32 | 1 club enters in 2002 |

Placeholders, not permanent identities. There is no `expansion-1976-03`, `expansion-1976-04`, or `expansion-1996` slug.

| Placeholder slug | First season | Display name until a real name is chosen |
|------------------|--------------|------------------------------------------|
| `founding-01` … `founding-26` | 1970 | Founding franchise 01–26 |
| `expansion-1976-01`, `expansion-1976-02` | 1976 | 1976 expansion franchise 01–02 |
| `expansion-1995-01`, `expansion-1995-02` | 1995 | 1995 expansion franchise 01–02 |
| `expansion-1999-01` | 1999 | 1999 expansion franchise 01 |
| `expansion-2002-01` | 2002 | 2002 expansion franchise 01 |

Permanent slugs and catalog links stay empty until chosen. Every timeline on the release uses these same placeholder slugs and the same entry years. The display-name column above is what `fg-founding-1` writes. `fg-founding-1` does not read or write provisional names.

`fg-founding-2` writes one `ProvisionalFranchiseName` row for each of the 32 placeholder slugs when the release is generated, including clubs that have not debuted. The row is unique on the release and `placeholder_slug`. It does not point at `UniverseFranchise`, so recreating a franchise row cannot erase it. `city` and `nickname` are fictional and each has its own status, `provisional` or `replaced`. The first write is provisional. Later naming may change a field only while that field is still provisional, and it then marks that field replaced. A replaced value is kept when the seed changes and when the franchise row is rebuilt. A city or nickname replacement is rejected when that exact, case-sensitive value is already used by another franchise in the same release. `display_name` is the city, one space, and the nickname. A new draw walks the expansion slugs in lexicographic order, then `founding-01` through `founding-26` in numeric order. The frozen lists hold 40 fictional cities and 40 fictional nicknames, in the approved order. Real NFL cities and NFL team nicknames are not in those lists. Final franchise identities stay unresolved.

`N(year)` is the number of franchises whose `first_active_season` is less than or equal to `year` and whose `final_active_season` is null or greater than or equal to `year`.

```text
N(1970) = 26
E(year) = number of franchises with first_active_season = year
N(year) = N(year - 1) + E(year) − D(year)
```

`D(year)` is the number of franchises whose `final_active_season` is `year - 1`. No contraction is scheduled, so `D(year) = 0`. `E(1976) = 2`, `E(1995) = 2`, `E(1999) = 1`, `E(2002) = 1`, and `E(year) = 0` otherwise. Franchise-seasons across the 60 years are `6 × 26 + 19 × 28 + 4 × 30 + 3 × 31 + 28 × 32 = 1,797`.

### Conference and division alignment

Conferences stay `conference-1` and `conference-2` for the whole timeline. Twenty-six clubs are 13 in each conference. Thirteen is prime, so the 1970 divisions are 5, 4, and 4. The five-club division in each conference is `c1-d3` and `c2-d3`. Thirty-one clubs cannot split into two equal conferences: 1999–2001 are 16 in conference-1 and 15 in conference-2. Membership is shared. Timelines do not realign on their own.

| Seasons | Shape | Slots |
|---------|--------|-------|
| 1970–1975 | 2 conferences × (5 + 4 + 4) | `c1-d1`, `c1-d2`, `c1-d3`, `c2-d1`, `c2-d2`, `c2-d3` |
| 1976 | each conference is `d1` 4, `d2` 5, `d3` 5 | `c1-d1` … `c1-d3` and `c2-d1` … `c2-d3` |
| 1977–1994 | conference-1 is `d1` 4, `d2` 5, `d3` 5; conference-2 is `d1` 5, `d2` 4, `d3` 5 | the same six divisions |
| 1995–1998 | 2 conferences × 3 divisions × 5 teams | `c1-d1` … `c1-d3` and `c2-d1` … `c2-d3` |
| 1999–2001 | conference-1 is `d1` 6, `d2` 5, `d3` 5; conference-2 stays 3 × 5 | `c1-d1` … `c1-d3` and `c2-d1` … `c2-d3` |
| 2002–2029 | 2 conferences × 4 divisions × 4 teams | `c1-d1` … `c1-d4` and `c2-d1` … `c2-d4` |

No geographic names are assigned, and no placeholder slug is a historical NFL club. `c1-d3` means the third division of conference 1, not a region. For 1976–1994 and for conference-1 in 1999–2001, `d1` is the Central-sized role, `d2` is the West-sized role, and `d3` is the East-sized role. Those are size roles for the stored slugs. They do not rename a franchise. `slot_order` is an integer on the membership row. It is the order in the tables below. It is not a city, and after 1970 it is not alphabetical order of the slug. The corrected 1976, 1977–1994, and 1999–2001 seats are a new catalog. A release already published with the previous seats keeps those rows. This generator does not edit a published release.

**1970 through 1975.** Thirteen founding clubs in each conference. `founding-01` through `founding-24` keep the seats from the earlier four-club table. `founding-25` is order 5 of `c1-d3`. `founding-26` is order 5 of `c2-d3`.

| Division | Order 1 | Order 2 | Order 3 | Order 4 | Order 5 |
|----------|---------|---------|---------|---------|---------|
| `c1-d1` | founding-01 | founding-02 | founding-03 | founding-04 | |
| `c1-d2` | founding-05 | founding-06 | founding-07 | founding-08 | |
| `c1-d3` | founding-09 | founding-10 | founding-11 | founding-12 | founding-25 |
| `c2-d1` | founding-13 | founding-14 | founding-15 | founding-16 | |
| `c2-d2` | founding-17 | founding-18 | founding-19 | founding-20 | |
| `c2-d3` | founding-21 | founding-22 | founding-23 | founding-24 | founding-26 |

**1976.** Each conference is a four-club `d1`, a five-club `d2`, and the five-club `d3` carried forward from 1970–1975. `expansion-1976-01` is order 5 of `c2-d2`. `expansion-1976-02` is order 5 of `c1-d2`. Founding clubs stay in the conference they held in 1975. The two expansion slugs are not Seattle and Tampa Bay. Their conferences are the approved path, and that path reverses in 1977.

| Division | Order 1 | Order 2 | Order 3 | Order 4 | Order 5 |
|----------|---------|---------|---------|---------|---------|
| `c1-d1` | founding-01 | founding-02 | founding-03 | founding-04 | |
| `c1-d2` | founding-05 | founding-06 | founding-07 | founding-08 | expansion-1976-02 |
| `c1-d3` | founding-09 | founding-10 | founding-11 | founding-12 | founding-25 |
| `c2-d1` | founding-13 | founding-14 | founding-15 | founding-16 | |
| `c2-d2` | founding-17 | founding-18 | founding-19 | founding-20 | expansion-1976-01 |
| `c2-d3` | founding-21 | founding-22 | founding-23 | founding-24 | founding-26 |

**1977 through 1994.** The same founding seats stay. `expansion-1976-01` moves to order 5 of `c1-d2` and stays there through 1994. `expansion-1976-02` moves to order 5 of `c2-d1` and stays there through 1994. Conference-1 remains 4, 5, and 5. Conference-2 is 5, 4, and 5.

| Division | Order 1 | Order 2 | Order 3 | Order 4 | Order 5 |
|----------|---------|---------|---------|---------|---------|
| `c1-d1` | founding-01 | founding-02 | founding-03 | founding-04 | |
| `c1-d2` | founding-05 | founding-06 | founding-07 | founding-08 | expansion-1976-01 |
| `c1-d3` | founding-09 | founding-10 | founding-11 | founding-12 | founding-25 |
| `c2-d1` | founding-13 | founding-14 | founding-15 | founding-16 | expansion-1976-02 |
| `c2-d2` | founding-17 | founding-18 | founding-19 | founding-20 | |
| `c2-d3` | founding-21 | founding-22 | founding-23 | founding-24 | founding-26 |

**1995 realignment.** Each conference has 14 continuing clubs and adds one expansion club, which is three divisions of five. These seats are the kept 1995–1998 table. They are not produced by splitting the 1976 or 1977 divisions above.

| Division | Order 1 | Order 2 | Order 3 | Order 4 | Order 5 |
|----------|---------|---------|---------|---------|---------|
| `c1-d1` | founding-01 | founding-02 | founding-03 | founding-04 | founding-09 |
| `c1-d2` | founding-05 | founding-06 | founding-07 | founding-08 | founding-11 |
| `c1-d3` | founding-10 | expansion-1976-01 | founding-12 | founding-25 | expansion-1995-01 |
| `c2-d1` | founding-13 | founding-14 | founding-15 | founding-16 | founding-21 |
| `c2-d2` | founding-17 | founding-18 | founding-19 | founding-20 | founding-23 |
| `c2-d3` | founding-22 | expansion-1976-02 | founding-24 | founding-26 | expansion-1995-02 |

**1999 realignment.** `expansion-1999-01` joins conference-1. That conference becomes 16 clubs in three divisions: `d1` has six, `d2` has five, and `d3` has five. There is no `c1-d4` in 1999–2001. `expansion-1999-01` is order 6 of `c1-d1`. It is the club the 1999 expansion event adds. It is not a historical franchise. Conference-2 stays 15 and is unchanged from the 1995 table.

| Division | Order 1 | Order 2 | Order 3 | Order 4 | Order 5 | Order 6 |
|----------|---------|---------|---------|---------|---------|---------|
| `c1-d1` | founding-01 | founding-02 | founding-03 | founding-04 | expansion-1995-01 | expansion-1999-01 |
| `c1-d2` | founding-05 | founding-06 | founding-07 | founding-08 | expansion-1976-01 | |
| `c1-d3` | founding-09 | founding-10 | founding-11 | founding-12 | founding-25 | |

Conference-2 in 1999–2001:

| Division | Order 1 | Order 2 | Order 3 | Order 4 | Order 5 |
|----------|---------|---------|---------|---------|---------|
| `c2-d1` | founding-13 | founding-14 | founding-15 | founding-16 | founding-21 |
| `c2-d2` | founding-17 | founding-18 | founding-19 | founding-20 | founding-23 |
| `c2-d3` | founding-22 | expansion-1976-02 | founding-24 | founding-26 | expansion-1995-02 |

**2002 realignment.** `expansion-2002-01` joins conference-2. Both conferences have 16 clubs in four divisions of four. Conference-1’s 2002 seats are the kept four-division table. They are not the 1999–2001 seats. Conference-2 is the kept conversion below.

| Division | Order 1 | Order 2 | Order 3 | Order 4 |
|----------|---------|---------|---------|---------|
| `c1-d1` | founding-01 | founding-02 | founding-03 | founding-04 |
| `c1-d2` | founding-05 | founding-06 | founding-07 | founding-08 |
| `c1-d3` | founding-10 | expansion-1976-01 | founding-12 | founding-25 |
| `c1-d4` | founding-09 | founding-11 | expansion-1995-01 | expansion-1999-01 |

Conference-2 in 2002–2029:

| Division | Order 1 | Order 2 | Order 3 | Order 4 |
|----------|---------|---------|---------|---------|
| `c2-d1` | founding-13 | founding-14 | founding-15 | founding-16 |
| `c2-d2` | founding-17 | founding-18 | founding-19 | founding-20 |
| `c2-d3` | founding-22 | expansion-1976-02 | founding-24 | founding-26 |
| `c2-d4` | founding-21 | founding-23 | expansion-1995-02 | expansion-2002-01 |

`founding-09` plays in `c1-d3` slot 1 for 1970–1994 and again for 1999–2001. It plays in `c1-d1` slot 5 for 1995–1998 and in `c1-d4` slot 1 from 2002 on. Founding clubs stay in their original conference. `expansion-1976-01` changes conference in 1976 and again in 1977, then stays in conference-1 through 1994. `expansion-1976-02` does the reverse. A club that moved in one year can move again in a later one. The 2002 shape is 2 conferences and 8 divisions of 4, which is the shape `league_generator.py` already builds as a 32-team tree. That generator still cannot represent 1970, 1976, 1995, or 1999. This document does not change it.

### 1970–1977 opponent pairings

These pairings are a project approximation. They are built from the corrected memberships, the East/Central/West roles (`d3`, `d1`, and `d2`), `slot_order`, the season, and that season's stored `schedule_length`. The NFL assigned non-division opponents, the extra interconference game, the removed 1976–1977 games, and single-game sites to named clubs. No placeholder stands for one of those clubs, and these seat rules are not that chart.

The stored length for 1970–1977 is 14. Division rivals play home and away. One Central or West seat in each conference takes the extra interconference game, rotating Central 1, West 1, Central 2, West 2, Central 3, West 3, Central 4, West 4 from 1970 through 1977, and those two clubs play each other. The 13 non-swing clubs are numbered East 1–5, Central 1–4, West 1–4, positions 0–12. Ordinary interconference games are defined once, from conference 1 to conference 2. 1970–1971 use offsets +1, +2, and +3. 1973 uses +4, +5, and +6. 1974–1975 use +7, +8, and +9. 1972 uses +4, a special matching, and +6. In that special matching, East positions 0–4 play the same position, positions 5–7 use +5, and positions 8–12 use +10. That matching is what gives every East club its same-position opponent once across the eight seasons. 1976–1977 start from +10, +11, and +12, then remove the +10 matching in 1976 and the +11 matching in 1977. The extra-game pairing is added after that list and is not one of the removed games.

In 1976 and 1977 the expansion club seated at slot 5 of Central or West is the swing club. It plays the other 13 clubs in its conference once and the other swing club once. Conference 1 hosts that swing-club meeting in both seasons. Each non-swing club adds its own conference's swing club in place of the removed interconference game.

The non-swing 13×13 matrix has 27 games in each of 1976 and 1977. The swing-club meeting is outside that matrix, so total interconference games are 28 in each of those seasons. Season totals are 88 division + 54 intraconference + 40 interconference = 182 in 1970–1975, and 88 division + 54 intraconference + 27 matrix interconference + 26 games against the home-conference swing club + 1 swing-club game = 196 in 1976–1977. Across 1970–1977 the matrix has 294 games: 125 pairs twice and 44 pairs once. Every club plays 14 and finishes 7 home and 7 away. Single-game sites come from the deterministic home-repair rule. This pairing writer does not assign weeks, byes, kickoffs, or scores. It does not write seasons after 1977. The 1978–1989 pairings are a separate project rule.

### 1978–1989 opponent pairings

These pairings are a project schedule for the stored 1977–1994 seats. They are not an NFL opponent chart, and they do not extend the 1970–1977 matrix. One rule covers every season from 1978 through 1989. There is no 1987 split. The `y1978` and `y1987` calendar `games` fields stay unresolved. This writer does not edit those cards and does not treat the population table as approval of either field. It reads the stored `schedule_length` and accepts only 16.

Each conference is numbered on its own: East (`d3`) slots in order, then Central (`d1`) slots in order, then West (`d2`) slots in order. Conference-1 is 5, 4, and 5 clubs. Conference-2 is 5, 5, and 4. Those seats are positions 0–13.

Every club plays each of the other 13 clubs in its conference once. Division games are single games, not home-and-home. A club in a 4-team division plays 3 division games and 10 other conference games. A club in a 5-team division plays 4 and 9. Every club also plays three clubs in the other conference. In 1978, conference-1 position `p` plays conference-2 positions `p`, `p+2`, and `p+4`, modulo 14. Each later season adds 1 to all three offsets. Across these twelve seasons, 84 cross-conference pairs meet twice and 112 meet three times. None is missing.

A season has 52 division games, 130 other conference games, and 42 `interconference` games, which is 224. `interconference` is not `matrix_interconference`. Every club plays 16 and finishes 8 home and 8 away. The first host is the lower seat key: conference, then East before Central before West, then slot. The same home-repair search used for 1970–1977 then runs with no pinned games and a home quota of 8.

The writer leaves 1970–1977 pairings and week assignments untouched. An empty 1978–1989 span is written in one transaction. An exact rerun writes nothing. A partial, extra, or mismatched set in that span is left as it was. This writer does not assign weeks, byes, kickoffs, scores, standings, or simulation, and it does not schedule seasons after 1989. Twelve seasons store 2,688 rows. These rows are not part of either release fingerprint.

### 1990–1994 opponent pairings

These seasons keep the 1977–1994 seats and the 1978 formula. `t` is the season minus 1978. Conference-1 position `p` plays conference-2 positions `p+t`, `p+t+2`, and `p+t+4`, modulo 14. The seat order is still East (`d3`), then Central (`d1`), then West (`d2`). Each club still plays the other 13 clubs in its conference once. Division games stay single games. Home repair is the same unpinned search with a quota of 8.

The rotation modulus is 14, so 1992 repeats the 1978 opponent set, 1993 repeats 1979, and 1994 repeats 1980. The same seats make those home sites repeat as well. Those repeated 1992–1994 opponent sets and home sites are the approved continuation of the 14-year cycle. They are not a new chart. 1990 uses offsets 12, 0, and 2. 1991 uses 13, 1, and 3. Those two offset triples do not occur in 1978–1989.

The writer reads the stored `schedule_length` and accepts only 16. That does not approve the unresolved `y1987` calendar `games` field, and it does not edit that card. `y1978` does not cover these seasons. A season still has 224 games. Five seasons store 1,120 rows. The writer leaves 1970–1989 pairings and 1970–1977 week assignments untouched. It does not assign weeks, byes, kickoffs, scores, standings, or simulation. The 16-week calendar is a separate project rule. Seasons after 1994 are not generated here. These rows are not part of either release fingerprint.

### 1978–1994 week assignment

Each of these seasons has 16 calendar weeks and no byes. Each club plays exactly one of its 16 stored games in every week. `schedule_length` 16 counts those games. This writer reads that stored length and does not treat it as the definition of the calendar. It does not approve or edit the unresolved `y1978` or `y1987` calendar `games` fields.

Seats are numbered East (`d3`), then Central (`d1`), then West (`d2`), slots ascending, as positions 0–13 in each conference. Weeks 1–13 play the same circle round in both conferences. Position 0 stays fixed. The other positions start as 1 through 13, and each later round moves the last of those seats to the front. Index `i` is paired with index `13 - i`. Round 0, stored as week 1, pairs 0–13, 1–12, 2–11, 3–10, 4–9, 5–8, and 6–7.

Weeks 14–16 are the cross-conference offsets `t`, `t+2`, and `t+4` modulo 14, in that formula order. `t` is the season minus 1978. Conference-1 position `p` plays conference-2 position `p` plus that offset. In 1990 the three weeks use offsets 12, then 0, then 2. The same stored seats give 1992 the 1978 weeks, 1993 the 1979 weeks, and 1994 the 1980 weeks.

The writer reads the stored pairings and does not change their clubs, home site, kind, identifier, or timestamp. One `OpponentWeekAssignment` points at each pairing. An empty 1978–1994 week span is written in one transaction. An exact rerun writes nothing. Missing, partial, duplicated, or mismatched pairings store nothing. A partial or different week set is left in place. 1970–1977 week rows are not read or replaced. Seventeen seasons store 3,808 rows. The column allows weeks 1 through 16. These rows are not part of either release fingerprint. Kickoff dates and times, scores, standings, and simulation stay out of this writer.

### 1970–1977 week assignment

Each of these seasons has exactly 14 regular-season weeks and no byes. Each club plays exactly one of its 14 stored games in every week. `OpponentWeekAssignment` stores that week on its own row and points at the existing `OpponentPairing`. Generating weeks does not change the pairing's clubs, kind, identifier, or timestamp. This is a valid project schedule for these pairings. It is not the historical NFL week chart, and it stores no kickoff date or time. The week column allows 1 through 16 so the 1978–1994 assignment can be stored. This writer still assigns only weeks 1 through 14.

The search is a complete 1-factorization, not a sequential greedy pass. It forces a game that has one legal week and a club-week that has one legal game, then branches on the club-week with the fewest legal games. Equal counts break by club slug, then by week number. The games tried for that club-week are in home-slug, away-slug order. The first complete assignment is stored. If every branch fails, the writer reports that the season cannot be scheduled and stores nothing. The same pairing set produces the same weeks. An exact rerun writes nothing. A partial or different stored set is left as it is.

These rows are not part of either release fingerprint. Kickoff dates and times, scoring, standings, and simulation stay out of this writer. The 1978–1994 weeks are a separate project rule.

### 2001 finishes and 2002 scheduling ranks

A played 2001 place stays on the stored 2001 division. `TimelinePriorFinish` stores that place with the club's wins, losses, and ties. Conference-1 `d1` uses places 1 through 6. Each other 2001 division uses places 1 through 5. The service copies the conference and division from the 2001 membership. `expansion-2002-01` has no 2001 membership and no 2001 finish.

The records are accepted only after the verified-result service has completed the 2001 season and set `TimelineSeason.regular_season_simulated`. This slice does not implement results, and it does not set that flag. If the 2001 season row is missing or the flag is false, the writer stores nothing. Every club's wins, losses, and ties must equal the stored 2001 `schedule_length`. The whole league is one atomic set: every 2001 membership once, and each division's places exactly 1 through that division's size. One new row cannot be saved on its own. An exact rerun writes nothing. A partial or different stored set is left as it is.

Winning percentage orders the played clubs. A tie counts as half a win, compared as a fraction. A strictly better percentage has the better 2001 place. Equal percentages may be filed in either place order, because the historical 2001 tiebreak service is outside this slice. Inside one 2001 division, equal percentages keep that resolved place order when the 2002 rank is derived.

`TimelineSchedulingRank` is the derived rank, 1 through 4, inside each stored 2002 division. It requires the complete immutable 2001 finish set. Clubs are ordered by exact 2001 winning percentage. Equal percentages from the same 2001 division keep place order. Equal percentages from different 2001 divisions use one uniform interleaving draw over those place-ordered chains. `expansion-2002-01` ranks after every club that played in 2001. The derivation does not change the finishes, the memberships, franchise identity, or `slot_order`.

Each cross-division tie that needs a draw stores one `TimelineSchedulingDraw` on the timeline, in the same transaction as the ranks. The row records the 2002 conference and division, the draw step, the ordered candidate chains, the coordinate, the SHA-256 digest, the chosen chain and franchise, and the resulting order. The coordinate is `scheduling-rank-2002:{2002 conference}:{2002 division}:{step}:{sorted remaining slugs}:{attempt}` and is hashed with the release seed. The timeline id is not part of that input, so identical inputs under one release produce the same transition ranks. The database row still belongs to the timeline. An exact rerun reads the stored draw, checks the digest and the chosen outcome, and does not sample or insert another draw. A partial, missing, or mismatched draw or rank set is left as it is.

These rows are not part of either release fingerprint. `OpponentPairing` and `OpponentWeekAssignment` stay as they are.

### 2002 timeline fixtures

`ScheduleGame` is one timeline's 2002 regular-season fixture. It stores the two franchises and one category: `division`, `conference`, `interconference`, or `same_place`. It does not store a week, a bye, a kickoff, or a score. One timeline and one season own the row. The shared `OpponentPairing` and `OpponentWeekAssignment` rows stay untouched, and so do franchise identity, membership, `slot_order`, and both release fingerprints.

The 2002 formula is the reviewed NFL rotation. Each club plays six home-and-away division games, four games against the rotating same-conference division, four games against the rotating other-conference division, and two same-place games. Stored seats `d1`, `d2`, `d3`, and `d4` are the rotation positions East, North, South, and West. `conference-1` takes the AFC side of that chart and `conference-2` takes the NFC side. Those words are rotation labels, not geography.

`k3 = (year - 2002) mod 3` pairs the same-conference divisions: 0 is East–West and North–South; 1 is East–South and North–West; 2 is East–North and South–West. `k4 = (year - 2002) mod 4` pairs the conferences. The AFC-side positions East, North, South, and West play NFC-side North, South, East, West in 2002, then East, West, South, North, then West, East, North, South, then South, North, West, East. The other conference is that matching read in reverse. Home sites for a four-game set use `slot_order`. Slots 1–2 are one pair and slots 3–4 are the other. On the first meeting, pair 1–2 hosts both clubs in the other division's pair 1–2 and visits both clubs in pair 3–4. Each later meeting of that division pairing flips every site. The intraconference period is 3 and the interconference period is 4. Same-place games give every division one home division and one away division. On the first meeting, `d1` hosts the lower-numbered of its two remaining opponents, and the other three sites are the only orientation that still gives every division one home and one away.

This writer stores 2002, 2003, 2004, and 2005 after the checks in the following sections. Each of those seasons is 256 games, 16 for every club, 8 home and 8 away. The rotation above also describes 2006–2020, but those seasons are not generated. A request for one of them is refused before any insert. Those seasons need the prior timeline season's verified results and its division places. The schedule writer does not store scores or division places. The closes below do that. The 2002 close does not generate 2003, the 2003 close does not generate 2004, and the 2004 close does not generate 2005.

### 2003 timeline fixtures

The 2003 writer is the same schedule service. It does not keep a second copy of the rotation. It stores a season only when the release is published, the timeline is active, the timeline includes 2002, and both the 2002 and 2003 `TimelineSeason` rows already exist. It does not create or advance the 2003 season. A timeline whose `start_season_year` is 2003 or later is refused. No opening 2002 scheduling-place seed has been approved for that start, and this slice does not invent one. A timeline that began in 2002 uses the played 2002 results and places after that season closes.

The 2002 completion flag is not enough. The writer locks the release, the timeline, and the 2002 and 2003 seasons, in that order, then calls the close service's read-only check. That check requires the flag, the 256 stored 2002 results on those fixtures, the 32 places, and the draws. It reruns the close's stored-draw tiebreak and accepts the rows only when the stored places match. Places 1 through 4 occur once in each 2002 division. The same-place games read those `TimelineDivisionPlace` values. They do not read `TimelineSchedulingRank`, `TimelinePriorFinish`, `slot_order` as a place, slug order, draft order, or a new random seed.

For 2003, `k3 = (2003 - 2002) mod 3 = 1` and `k4 = (2003 - 2002) mod 4 = 1`. Same-conference pairs are East–South and North–West, which are `d1`–`d3` and `d2`–`d4`, in both conferences. Interconference pairs are conference-1 `d1` with conference-2 `d1`, conference-1 `d2` with conference-2 `d4`, conference-1 `d3` with conference-2 `d3`, and conference-1 `d4` with conference-2 `d2`.

Home sites use the same rules as the 2002–2020 formula. Division opponents play once at each site. Each four-game rotating set uses slot pairs 1–2 and 3–4. The 2003 pairings are the first meeting of those division pairs, so the meeting index is 0 and no site is flipped. The reference side is the lower seat in a same-conference pair (`d1` before `d3`, and `d2` before `d4`) and conference-1 in an interconference pair. That side's slots 1–2 host the other division's slots 1–2 and visit its slots 3–4. Its slots 3–4 host slots 3–4 and visit slots 1–2. The other division takes the opposite site in each game, so every club still has two home games and two away games in the set. A later return of the same pairing flips every site. Same-place games give every division one home division and one away division. The 2003 same-place opponents are also a first meeting: `d1` hosts `d2`, the lower-numbered of its two remaining opponents, `d2` hosts `d3`, `d3` hosts `d4`, and `d4` hosts `d1`. Both conferences use that orientation. The next time this same-conference rotation returns, those four sites reverse. There is no home-repair search. Every club finishes with eight home and eight away games, including one same-place home game and one same-place away game.

The writer stores 256 `ScheduleGame` rows: 96 division, 64 rotating same-conference, 64 rotating interconference, and 32 same-place. An exact rerun of the stored 2002 or 2003 fixtures writes nothing. A partial or different 2003 set is refused and left unchanged. A failed insert rolls the 2003 rows back. The 2002 fixtures, results, draws, places, seeds, and completion flag stay as they are. Shared opponent rows, historical standings, membership, `slot_order`, franchise identity, and release fingerprints stay as they are.

Weeks, byes, kickoff dates, and times stay unassigned. The 2003 close below stores that season's results and division places. The 2004 fixtures are the schedule section after that close. The close does not generate them.

### 2004 timeline fixtures

The 2004 writer is the same schedule service. It does not keep a second copy of the rotation. It stores a season only when the release is published, the timeline is active, and both the 2003 and 2004 `TimelineSeason` rows already exist. It does not create or advance the 2004 season. It does not set `regular_season_simulated`.

The 2003 completion flag is not enough. The writer locks the release, the timeline, the 2003 season, and the 2004 season, in that order, then calls `verify_played_2003_places`. That check requires the completed 2003 season, the 256 stored results on those fixtures, the 32 places, and the draws. It reruns the close's stored-draw tiebreak and accepts the rows only when the stored places match. Places 1 through 4 occur once in each 2003 division. An empty or partial 2003 table is refused before any 2004 insert. The same-place games read those `TimelineDivisionPlace` values. They do not read `TimelineSchedulingRank`, `TimelinePriorFinish`, `slot_order` as a place, slug order, draft order, or a new random seed.

For 2004, `k3 = (2004 - 2002) mod 3 = 2` and `k4 = (2004 - 2002) mod 4 = 2`. Same-conference pairs are East–North and South–West, which are `d1`–`d2` and `d3`–`d4`, in both conferences. Interconference pairs are conference-1 `d1`, `d2`, `d3`, and `d4` with conference-2 `d4`, `d1`, `d2`, and `d3`. That is the West, East, North, South row.

These 2004 pairings are the first meeting of those division pairs. The meeting index is `(2004 - 2004) // period = 0`, so no site is flipped. The second meeting of the same pairs flips every site: 2007 for the intraconference and same-place pairs, whose period is 3, and 2008 for the interconference pairs, whose period is 4. The reference side is the lower seat in a same-conference pair (`d1` before `d2`, and `d3` before `d4`) and conference-1 in an interconference pair. That side's slots 1–2 host the other division's slots 1–2 and visit its slots 3–4. Its slots 3–4 host slots 3–4 and visit slots 1–2. Same-place games give every division one home division and one away division. On this first meeting, `d1` hosts `d3`, the lower-numbered of its two remaining opponents, `d2` hosts `d4`, `d3` hosts `d2`, and `d4` hosts `d1`. Both conferences use that orientation. The 2007 return of this same-conference rotation reverses those four sites.

The writer stores 256 `ScheduleGame` rows: 96 division, 64 rotating same-conference, 64 rotating interconference, and 32 same-place. An exact rerun writes nothing. A partial or different 2004 set is refused and left unchanged. A failed insert rolls the 2004 rows back. The 2002 and 2003 fixtures, results, draws, places, seeds, and completion flags stay as they are. Shared opponent rows, historical standings, membership, `slot_order`, franchise identity, and release fingerprints stay as they are.

The schedule writer does not close or simulate 2004, assign weeks, byes, kickoff dates, or times, or add postseason standings, playoff seeding, or draft order. Closing 2004 is the later section. The 2005 fixtures are the schedule section after that close. The close does not generate them.

### 2005 timeline fixtures

The 2005 writer is the same schedule service. It does not keep a second copy of the rotation. It stores a season only when the release is published, the timeline is active, and both the 2004 and 2005 `TimelineSeason` rows already exist. It does not create or advance the 2005 season. It does not set `regular_season_simulated`.

The 2004 completion flag is not enough. The writer locks the release, the timeline, the 2004 season, and the 2005 season, in that order, then calls `verify_played_2004_places`. That check requires the completed 2004 season, the 256 stored results on those fixtures, the 32 places, and the draws. It reruns the close's stored-draw tiebreak and accepts the rows only when the stored places match. Places 1 through 4 occur once in each 2004 division. An empty or partial 2004 table is refused before any 2005 insert. The same-place games read those `TimelineDivisionPlace` values. They do not read `TimelineSchedulingRank`, `TimelinePriorFinish`, `slot_order` as a place, slug order, draft order, or a new random seed.

For 2005, `k3 = (2005 - 2002) mod 3 = 0` and `k4 = (2005 - 2002) mod 4 = 3`. Same-conference pairs are East–West and North–South, which are `d1`–`d4` and `d2`–`d3`, in both conferences. Those are the same pairs as 2002. Interconference pairs are conference-1 `d1`, `d2`, `d3`, and `d4` with conference-2 `d3`, `d2`, `d4`, and `d1`. That is the South, North, West, East row.

The same-conference and same-place pairings are the second meeting of the 2002 rotation. The meeting index is `(2005 - 2002) // 3 = 1`, so every one of those sites flips. The reference side is still the lower seat (`d1` before `d4`, and `d2` before `d3`). On this return, that side's slots 1–2 visit the other division's slots 1–2 and host its slots 3–4. Its slots 3–4 visit slots 3–4 and host slots 1–2. The next return of this same-conference rotation, in 2008, flips those sites back to the 2002 orientation. Same-place games give every division one home division and one away division. The flipped 2005 homes are `d1` over `d3`, `d2` over `d1`, `d3` over `d4`, and `d4` over `d2`. Both conferences use that orientation.

The interconference pairing is new in 2005. Its first year is 2005, so the meeting index is 0 and no interconference site is flipped. Conference-1 is the reference side. Its slots 1–2 host the other division's slots 1–2 and visit its slots 3–4. The 2009 return of this interconference row flips every one of those sites.

The writer stores 256 `ScheduleGame` rows: 96 division, 64 rotating same-conference, 64 rotating interconference, and 32 same-place. An exact rerun writes nothing. A partial or different 2005 set is refused and left unchanged. A failed insert rolls the 2005 rows back. The 2002, 2003, and 2004 fixtures, results, draws, places, seeds, and completion flags stay as they are. Shared opponent rows, historical standings, membership, `slot_order`, franchise identity, and release fingerprints stay as they are.

This slice does not generate 2006 fixtures, close or simulate 2005, assign weeks, byes, kickoff dates, or times, or add postseason standings, playoff seeding, or draft order.

Whether 2001 was played comes from `Timeline.start_season_year`. An empty finish table is not that answer. A timeline that starts in 2001 or earlier must already have a completed 2001 `TimelineSeason`, the complete `TimelinePriorFinish` set, and the complete `TimelineSchedulingRank` set. The two same-place games read those scheduling ranks, 1 through 4. They do not read `TimelinePriorFinish.division_place`. This timeline never receives an opening schedule seed. `expansion-2002-01` keeps its scheduling rank and does not receive a 2001 finish. A missing, partial, or mismatched finish, draw, or rank blocks the schedule before any fixture insert.

A timeline that starts in 2002 or later did not play 2001. For the 2002 fixtures, the writer stores one opening permutation of each 2002 division as `TimelineScheduleSeed`. The permutation is HMAC-SHA256 of the release seed and `opening-2001-place:{conference}:{division}:{franchise slug}:{index}`. Place 1 is the lowest digest. The timeline id is not an input. The row's season is 2002, and it is a schedule seed, not a played standing. An exact rerun reads that seed and writes nothing. A partial or different seed blocks generation.

Seed rows and fixture rows are one transaction. An exact fixture rerun writes nothing. A partial or mismatched fixture set is left as it is. A forced insert failure rolls back the whole operation. The schedule service does not set `regular_season_simulated`.

### 2002 results and division places

`ScheduleGameResult` is one project result for one stored 2002 `ScheduleGame`. It is not a historical NFL score and it is not the later simulation `GameResult`. The row stores the final home and away scores, the home and away touchdown counts, and a `source_key` that names the producing simulation or result source. Touchdown counts are their own fields because a score does not determine them. Each stored touchdown counts as six points in that club's final score, so `6 × home_touchdowns` cannot exceed `home_score` and `6 × away_touchdowns` cannot exceed `away_score`. A 0–0 game cannot carry touchdowns. Six points with one touchdown is the boundary, and a higher score may include points that are not touchdowns. Field goals, safeties, and tries are not stored. Home and away clubs stay on the fixture. The result does not change that fixture. Its season equals the fixture's season, both 2002 in this slice, and the result belongs to the fixture's timeline. SQLite and MySQL check constraints cannot read the fixture row, so model validation and the close enforce that link.

The 2002–2003 Official Playing Rules (Triumph Books, ISBN 1572435135) were not available as text in this slice. The 2003 Official Playing Rules state that they govern the 2003 season, so they are not used as the 2002 overtime source. This writer does not apply an overtime procedure and does not store an overtime flag. The submitted final score is the result. The higher score is a win. An equal score is a tie.

`TimelineDivisionPlace` stores places 1 through 4 for every 2002 club. The conference and division are copied from that club's 2002 `FranchiseSeasonMembership`. The row records the step or draw that gave the club that place. It does not change franchise identity, membership, or `slot_order`. `TimelineDivisionDraw` stores a project draw when the tiebreak reaches its last step. The coordinate is `division-place:2002:{conference}:{division}:{sorted franchise keys}:{draw index}:{attempt}`. It is HMAC-SHA256 of the release seed. The timeline id is not an input. The row stores that coordinate, the digest, and the outcome.

The step names stay in the 2002 division order: head-to-head, division winning percentage, common games, conference winning percentage, strength of victory, strength of schedule, combined conference rank in points scored and points allowed, combined league rank in those same figures, net points in common games, net points in all games, net touchdowns, then the project draw in place of the coin toss. Clubs enter that list only when their overall winning percentage is tied for the next place. That order is the 2002 sequence this project keeps. The 2005 Official NFL Record & Fact Book prints the same order on its tiebreak page, and the 2010, 2012, 2014, and 2019 books keep it. This repository does not have a 2002 page that states the arithmetic below. Those formulas are project rules.

Winning percentage is `(wins + 0.5 × ties) / games`, compared by cross multiplication so the comparison does not round through floating point. Head-to-head, division, and conference percentages use that same comparison. Head-to-head counts games among the clubs still tied for the step. Division percentage counts every game against a club in the same 2002 division. Conference percentage counts every game against a club in the same 2002 conference. Membership, not the fixture category, decides which games those are.

Common games are the opponents shared by every club still in that step, excluding the clubs themselves. Each club's percentage uses every game it played against that shared set, with no minimum number of games. If the shared set is empty, the step does not separate the clubs. Net points in common games use that same set.

Strength of victory adds the final W-L-T record of each opponent the club defeated, once per win. Beating the same opponent twice includes that opponent's final record twice. Strength of schedule does the same for every scheduled appearance, including a loss, a tie, and a repeat. The opponent's final record includes every game that opponent played.

Combined conference rank and combined league rank use the later published method as a project adaptation. Points scored and points allowed are ranked separately. A higher point total ranks ahead on points scored. A lower total ranks ahead on points allowed. Tied values share the best competition rank and the next rank skips, so two clubs tied for first are both 1 and the next club is 3. The sums are added and the lower sum ranks ahead. Conference rank uses the clubs in that conference. League rank uses every club in the season.

Net points are points scored minus points allowed. Net touchdowns are touchdowns scored minus touchdowns allowed across all games. The higher figure ranks ahead.

Inside one place, a step that leaves three or more clubs sharing the best value removes the others and applies the next step to that smaller group. When the clubs still tied are reduced to exactly two, the tiebreaker restarts at step 1 of the two-club procedure, beginning again with their head-to-head percentage. It does not continue at the step after the one that eliminated the other clubs. That restart is the sourced 2002 rule: if two clubs remain tied after a third or other club is eliminated during any step, the tiebreaker reverts to step 1 of the two-club format. The December 13, 2003 published NFL procedure supports the same two-club restart, so the 2003 close uses it too. A reduction from four clubs to three does not restart the three-club procedure. That later restart was not part of the 2002 procedure or this 2003 procedure. Where the historical text names a step and does not define the calculation, the arithmetic in this section remains a project rule. After a place is assigned, the whole procedure runs again on every club that does not yet have a place. The last remaining club is `sole_remaining`. The 2002 text names the steps, says the procedure continues until a champion is determined, and says that when one club wins the tiebreaker the remaining clubs revert to step 1.

The close accepts one complete result for each of the 256 stored 2002 fixtures. It locks the release, the timeline, and the 2002 season, in that order. Directives must already be locked. It checks the full fixture set and the full result set before it writes. A stored result is kept only when its season is 2002, that season equals the linked fixture's season, and both rows belong to the same timeline. Results, any draws, and the 32 places are one transaction. After that insert, or after a later call has recomputed the tiebreak and verified the stored results, draws, and places, the close sets `regular_season_simulated` with one queryset update filtered to the locked season's primary key and to the flag still being false. The update changes only that flag and must affect exactly one row. Any other count raises and rolls the transaction back. The in-memory season is then refreshed. That queryset update is the close service's internal transition and is not a general permission to complete a season by queryset. An exact rerun of a season that is already marked complete verifies the same rows and does not write. `TimelineSeason.save()` always refuses a false-to-true change for 2002, 2003, and 2004, including when a complete result, place, and draw set is already stored, because `save()` does not recompute the tiebreak or verify the draw digests. A 2001 season, and any year outside 2002, 2003, and 2004, can still change the flag through `save()`. An exact rerun recomputes the places and verifies each stored draw's coordinate, digest, candidates, chosen club, and outcome, then returns unchanged and issues no insert, update, or delete. If the flag is already true and any result, draw, or place is missing, extra, altered, cryptographically invalid, or inconsistent with that recomputation, the close refuses and leaves the stored rows unchanged. A forced failure during result insertion, draw insertion, place insertion, or the flag update rolls the new rows and the completion flag back together. A general queryset `update` or direct SQL can still change the flag without this service, as it can for the other application guards in this design. Those paths are not a supported way to complete the 2002 season. A well-formed result for 2003 is not part of the 2002 set. A result whose season does not equal its fixture's season, or which belongs to another timeline, is refused.

The close does not generate 2003 fixtures. It does not change `ScheduleGame` opponents or sites, shared `OpponentPairing` or `OpponentWeekAssignment` rows, `HistoricalStanding`, franchise identity, membership, `slot_order`, release fingerprints, `TimelinePriorFinish`, or `TimelineSchedulingRank`.

### 2003 results and division places

`close_2003_regular_season` is the 2003 entry point on the same close service. It does not keep a second copy of the tiebreak. The supported close range is 2002 through 2004. It is not an open-ended season range. The service requires a published release, an active timeline, and a 2003 `TimelineSeason` that already exists. It does not create that season. Setup is complete, directives are locked, and `regular_season_simulated` is still false unless the call is an exact verified rerun. The timeline already has exactly 256 regular-season 2003 `ScheduleGame` rows. The caller supplies one final score, touchdown total, and `source_key` for every fixture. The service does not simulate the games.

The checks are the 2002 checks for this season. There is one result for every fixture and no missing, duplicate, extra, cross-timeline, or cross-season result. A result's `season_year` equals its `ScheduleGame.season_year`, and both rows belong to the same timeline. A well-formed 2002 result stays on the timeline and is not treated as a 2003 result. Every 2003 club appears in exactly 16 fixtures. Scores and touchdown counts stay nonnegative, and `6 × touchdowns` cannot exceed that club's final score. `source_key` is required. The rows stay immutable after they are written. The database checks that can express those rules stay in place. The season checks now allow 2002 through 2004, and they still reject every other year. The 2003 migration widened those checks from 2002 alone to 2002 and 2003. The 2004 migration widens them to 2002 through 2004. Neither migration rewrites or recreates a valid earlier row. Uniqueness stays season-aware: one timeline and one franchise can have a 2002 place and a 2003 place, each season has one result per fixture, and each season has one place per franchise.

The tiebreak is the procedure above, in the same order, with the same `resolved_by` names and the same project arithmetic. Overall winning percentage selects the initial tied group. The December 13, 2003 published procedure supports the two-club restart used here. A four-club group reduced to three continues at the next step. Places 1 through 4 occur once in every division.

A new 2003 draw uses `division-place:2003:{conference}:{division}:{sorted franchise keys}:{draw index}:{attempt}`. The timeline id is not an input. The same 2003 inputs on two timelines that share a release seed make the same choice and store it on separate timeline-owned rows. The row still stores the coordinate, candidates, digest, chosen club, outcome, draw index, and resolving step. The 2002 coordinate format and the digest of an existing 2002 draw do not change. The shared builder takes the season and, when the season is omitted, still emits the original 2002 text.

After the results, draws, and all 32 places have been inserted or exactly verified, the close sets the flag with the same locked queryset transition: the 2003 `TimelineSeason` primary key, `regular_season_simulated=false`, an update of only that flag, a required count of one, and a refresh of the in-memory season. A different count rolls the transaction back. `TimelineSeason.save()` refuses an ordinary false-to-true change for 2003. The close is the only normal completion path for that year. An exact completed rerun recomputes the standings, verifies every draw, compares every result and place, and does not insert, update, or delete. A partial, altered, extra, or inconsistent stored set is refused and left unchanged. Results, draws, places, and the flag commit or roll back together. The locks are the release, the timeline, and the 2003 season, then the stored result, draw, and place rows, in the same order as 2002.

`verify_played_2003_places` is the read-only check. It requires the completed 2003 season, verifies the fixtures and results, recomputes the standings, verifies the stored draws and places, and returns the franchise-to-place map. It does not write. The 2004 schedule writer takes that map for same-place opponents. The check does not generate fixtures, simulate games, add play-by-play, assign weeks, byes, kickoff dates, or times, or add postseason standings, playoff seeding, or draft order.

Closing 2003 does not change a 2002 fixture, result, draw, place, seed, or completion flag. It does not change a 2003 `ScheduleGame` opponent, category, or home site. `TimelinePriorFinish`, `TimelineSchedulingRank`, `OpponentPairing`, `OpponentWeekAssignment`, `HistoricalStanding`, `FranchiseSeasonMembership`, `slot_order`, franchise identity, and both release fingerprints stay as they are.

### 2004 results and division places

`close_2004_regular_season` is the 2004 entry point on the same close service. It does not keep a second copy of the tiebreak. The supported close range remains 2002 through 2004. The service requires a published release, an active timeline, and a 2004 `TimelineSeason` that already exists. It does not create that season. Setup is complete, directives are locked, and `regular_season_simulated` is still false unless the call is an exact verified rerun. The timeline already has exactly 256 regular-season 2004 `ScheduleGame` rows. The caller supplies one final score, touchdown total, and `source_key` for every fixture. The service does not simulate the games.

The checks are the same season checks. There is one result for every fixture and no missing, duplicate, extra, cross-timeline, or cross-season result. A result's `season_year` equals its `ScheduleGame.season_year`, and both rows belong to the same timeline. A well-formed 2002 or 2003 result stays on the timeline and is not treated as a 2004 result. Every 2004 club appears in exactly 16 fixtures. Scores and touchdown counts stay nonnegative, and `6 × touchdowns` cannot exceed that club's final score. `source_key` is required. The rows stay immutable after they are written.

The tiebreak is the procedure above, in the same order, with the same `resolved_by` names and the same project arithmetic. A four-club group reduced to three continues at the next step. A reduction to exactly two clubs restarts at two-club head-to-head. Places 1 through 4 occur once in every division.

A new 2004 draw uses `division-place:2004:{conference}:{division}:{sorted franchise keys}:{draw index}:{attempt}`. The timeline id is not an input. The same 2004 inputs on two timelines that share a release seed make the same choice and store it on separate timeline-owned rows. The 2002 and 2003 coordinate formats and the digests of existing draws do not change. The shared builder takes the season and, when the season is omitted, still emits the original 2002 text.

After the results, draws, and all 32 places have been inserted or exactly verified, the close sets the flag with the same locked queryset transition. `TimelineSeason.save()` refuses an ordinary false-to-true change for 2004. The close is the only normal completion path for that year. An exact completed rerun recomputes the standings, verifies every draw, compares every result and place, and does not insert, update, or delete. A partial, altered, extra, or inconsistent stored set is refused and left unchanged. Results, draws, places, and the flag commit or roll back together. The locks are the release, the timeline, and the 2004 season, then the stored result, draw, and place rows.

`verify_played_2004_places` is the read-only check. It requires the completed 2004 season, verifies the fixtures and results, recomputes the standings, verifies the stored draws and places, and returns the franchise-to-place map. It does not write. The 2005 schedule writer takes that map for same-place opponents. The check does not generate fixtures, simulate games, add play-by-play, assign weeks, byes, kickoff dates, or times, or add postseason standings, playoff seeding, or draft order.

Closing 2004 does not change a 2002 or 2003 fixture, result, draw, place, seed, or completion flag. It does not change a 2004 `ScheduleGame` opponent, category, or home site. `TimelinePriorFinish`, `TimelineSchedulingRank`, `OpponentPairing`, `OpponentWeekAssignment`, `HistoricalStanding`, `FranchiseSeasonMembership`, `slot_order`, franchise identity, and both release fingerprints stay as they are.

The schedule is shared universe data and is versioned with the release. Every timeline on that release activates the same franchises in the same seasons, in the same conferences and divisions. A timeline cannot vote a club into the league or change `N(year)`. After publish, editing the schedule means a new release. Timelines already pinned to the old release keep the old schedule.

The years, franchise counts, and alignment shapes above are the confirmed release. They are release data. The tables store `origin` and `owner_selectable` as their own columns, and they store conference and division as slugs. They do not hardcode 1970, 1976, 1995, 1999, 2002, 2029, 26 founding clubs, 60 seasons, or the era shapes as database checks. A later release with a different horizon does not need a migration to change those numbers.

Publication of the confirmed release still requires 26 founding franchises, playable seasons 1970–2029, expansion to 28 franchises in 1976, 30 in 1995, 31 in 1999, and 32 in 2002, the alignment tables above, and 1,797 franchise-season memberships. That check belongs to release publication (phase 8 in the model proposal). It is not a permanent limit on every row the database can store. The atomic publication service performs the check, as described in [Publication](#publication). `fg-founding-1` does not write this release. It remains the 24-club generator. The later version that writes `founding-01` through `founding-26` is a different generator version, because seating indexes 24 and 25 changes those two head-coach birth years.

`origin` is stored on the franchise. It is not inferred when the franchise is displayed. A founding franchise begins in the release's first playable season. An expansion franchise begins in a later season. The confirmed release therefore marks the 1970 clubs `founding` and the 1976, 1995, 1999, and 2002 clubs `expansion`.

`owner_selectable` means a human owner is permitted to control this franchise during this season. It does not mean a new timeline may begin with this franchise during this season. The column defaults to true. On the confirmed release every active franchise has `owner_selectable` true in every active season, so a human who owns a founding franchise keeps controlling it after 1970, and a human who takes an expansion franchise keeps controlling it after the expansion year. False is reserved for a future explicit rule that makes one franchise bot-only during one season. No such season is scheduled.

Model `clean()`, `save()`, and instance `delete()` refuse edits to a published release. Those are application guards. Queryset updates, bulk writes, and direct SQL can bypass them. A generator may use bulk operations only while the release is still `draft`. Published rows are changed only through an approved service. Application code must not call queryset `update` or `bulk_update` on published release data. Future admin tools are read-only for a published release. The release fingerprints are how a later check confirms the stored rows still match what was published. Database constraints enforce the single-table rules they can express, such as unique keys, positive counts, and season arithmetic. This design adds no database triggers.

### Shared franchise

One row per club that will ever play in the release. All 32 placeholder franchises exist when the release is generated. Expansion clubs are not created when the first timeline reaches 1976, 1995, 1999, or 2002.

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `slug` | Shared | slug | unique in the release | Schedule author | Both |
| `name` | Shared | string | ≤ 255, name at debut | Schedule author | Display |
| `first_active_season` | Shared | smallint | confirmed release: 1970 for the founding 26; 1976, 1995, 1999, or 2002 for expansion | Schedule author | Both |
| `final_active_season` | Shared | smallint, null | null means still active through the release's last season; the confirmed release has no final season | Schedule author | Both |
| `origin` | Shared | enum | `founding` or `expansion`, stored on the row | Schedule author | Both |
| `history_predecessor` | Shared | FK, null | another franchise in the release, for a later relocation or reorganization link | Schedule author, unused until needed | Display |

`history_predecessor` is the hook for relocation, renaming, or a club that continues another club's history. No such links are defined. A rename or city change that keeps the same franchise uses `FranchiseHistoryEvent` rather than a new slug.

### Membership by season

One shared row per franchise per season in which it exists. This is the alignment the schedule promises. It is not the result of a timeline's standings.

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `franchise` | Shared | FK | | Schedule author | Both |
| `season_year` | Shared | smallint | inside the franchise's active span and the release's playable span; 1970–2029 for the confirmed release | Schedule author | Both |
| `conference_slug` | Shared | slug | nonempty conference slug for that season; `conference-1` or `conference-2` in the confirmed release | Schedule author | Both |
| `division_slug` | Shared | slug | nonempty division slug for that season; `d1`–`d4` as the confirmed alignment requires | Schedule author | Both |
| `slot_order` | Shared | smallint | positive; 1–4, 1–5, or 1–6 in the confirmed alignment tables | Schedule author, from the alignment tables | Both |
| `owner_selectable` | Shared | bool | a human may control this franchise during this season; true for every active season in the confirmed release; not a timeline-start flag; column default true | Schedule author | Both |

Every active franchise on the confirmed release has `owner_selectable` true in every season it is active. False would mark that franchise as bot-only for that season, and no such row is scheduled. Realignment is a new membership row for the later season, using the alignment tables above. A timeline does not recompute conference, division, `slot_order`, or `owner_selectable` from standings. Eligibility to start a new timeline in 1970, 1976, 1995, 1999, or 2002 is not read from this flag. A later-start mode, if one is approved, keeps its rules in timeline-start configuration in a later phase. Phase 1 does not add that configuration.

### History events

Optional shared rows. None are required for the founding 26.

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `franchise` | Shared | FK | | Schedule author | Display |
| `season_year` | Shared | smallint | inside the release's playable span and the franchise's active span | Schedule author | Display |
| `kind` | Shared | enum | `rename`, `relocation`, `reorganization` | Schedule author | Display |
| `previous_name` | Shared | string | ≤ 255 | Schedule author | Display |
| `new_name` | Shared | string | ≤ 255 | Schedule author | Display |
| `city` | Shared | string | ≤ 255, empty if unchanged | Schedule author | Display |
| `notes` | Shared | text | | Schedule author | Display |

### Expansion event

Four expansion events are part of this release. The 1976 event adds `expansion-1976-01` and `expansion-1976-02`. The 1995 event adds `expansion-1995-01` and `expansion-1995-02`. The 1999 event adds `expansion-1999-01`. The 2002 event adds `expansion-2002-01`. A season may add more than one franchise. The numeric rules in the table are the values this release stores. Publish is refused if any of them is empty, and it is refused again if the player pools cannot fill every new club's active roster. Taxi slots do not count toward that minimum. Veterans, drafted rookies, and reserved undrafted prospects are three separate sources. The undrafted pool does not supply the veteran selections.

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `season_year` | Shared | smallint | 1971–2029, the first season the new clubs play | Schedule author | Both |
| `franchises` | Shared | list of franchise FKs | the clubs with `first_active_season` equal to this year | Schedule author | Both |
| `placement` | Shared | membership rows | conference and division for each new club in `season_year` | Schedule author | Both |
| `active_target` | Shared | smallint | 40 in 1976; 53 in 1995, 1999, and 2002. The roster that must be filled. | Schedule author | Sim |
| `taxi_capacity` | Shared | smallint | 8. Optional. Not part of the publication minimum. | Schedule author | Sim |
| `max_losses_per_existing` | Shared | smallint | 2 in 1976 and 1995; 1 in 1999 and 2002. No existing club can lose more than this. | Schedule author | Sim |
| `min_losses_per_existing` | Shared | smallint | 1 in 1976 and 1995; 0 in 1999 and 2002. A completed draft leaves every existing club at the minimum or the maximum. | Schedule author | Sim |
| `clubs_at_max_loss` | Shared | smallint | 6 in 1976, 24 in 1995, 26 in 1999, 26 in 2002 | Schedule author | Sim |
| `clubs_at_min_loss` | Shared | smallint | 20 in 1976, 4 in 1995, 4 in 1999, 5 in 2002 | Schedule author | Sim |
| `veteran_picks_per_new` | Shared | smallint | 16 in 1976; 26 in 1995, 1999, and 2002 | Schedule author | Sim |
| `veteran_picks_total` | Shared | smallint | 32 in 1976, 52 in 1995, 26 in 1999, 26 in 2002 | Schedule author | Sim |
| `rookie_picks_per_new` | Shared | smallint | 17 in 1976; 7 in 1995, 1999, and 2002, at the end of each round | Schedule author | Sim |
| `undrafted_multiplier` | Shared | smallint | 2 in every expansion year. The ordinary rule is 2. The old 1996 multiplier of 3 is not used. | Schedule author | Sim |
| `undrafted_pool` | Shared | smallint | 56 in 1976 (`2 × 28`), 60 in 1995 (`2 × 30`), 62 in 1999 (`2 × 31`), 64 in 2002 (`2 × 32`) | Schedule author | Sim |
| `undrafted_reserved_per_new` | Shared | smallint | 7 in 1976; 20 in 1995, 1999, and 2002. Taken from the shared undrafted class. Not from the veteran board and not from timeline free agents. | Schedule author | Sim |
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
| How many veterans an existing club may lose | Shared `max_losses_per_existing` |
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
| `pick_number` | Timeline | smallint | 1 through `veteran_picks_per_new × E(year)` | Timeline sim or owner, if the owner controls a new club | Both |
| `selecting_franchise` | Timeline | FK | one of the event's new clubs | same | Both |
| `player` | Timeline | FK | a universe player already in this timeline | same | Both |
| `from_franchise` | Timeline | FK | the club that lost the player | same | Both |

The pick updates roster stints in that timeline only. The universe player row is unchanged. Two timelines can give the same expansion club different veterans because their earlier seasons produced different rosters.

The players who complete an expansion active roster are already in the shared release: veterans on existing clubs, that year's drafted rookies, and that year's undrafted prospects. Free agents may be signed in place of an undrafted prospect. They are not required, and a timeline is never blocked because earlier decisions left too few free agents.

### Recommended expansion draft

Taxi slots are optional capacity. An expansion franchise must have its active roster filled before the season begins. It does not have to fill all eight taxi slots. Empty taxi spots are acceptable. An expansion club should open with less depth than an established club.

No step creates a person who is not already in the universe. Selection snakes in placeholder-slug order through that event's new clubs. Odd rounds run the lower slug first. Even rounds reverse. A year with one new club has no second club to reverse. Injured players and players on expiring contracts may be selected. The injury and the timeline contract move with the player. Each existing club's protect list includes its only quarterback, only kicker, and only punter when it has exactly one of that position. It must expose at least as many players as the maximum it is allowed to lose, and it may expose more.

The veteran board, the draftable class, and the undrafted class are different groups. "Undrafted pool" is `2 × N(year)`. It is not the list of veterans the new clubs select. Available veterans are the existing clubs' active rosters from the previous season, assuming every active slot is full. The previous season's active limit is 40 in 1975, 47 in 1994, and 53 in 1998 and 2001. Taxi may add eight names per existing club. Those spots are not required, and the publication check does not count them.

| Rule | 1976 | 1995 | 1999 | 2002 |
|------|------|------|------|------|
| Existing clubs | 26 | 28 | 30 | 31 |
| New clubs | 2 | 2 | 1 | 1 |
| Active target | 40 | 53 | 53 | 53 |
| Taxi capacity | up to 8, optional | up to 8, optional | up to 8, optional | up to 8, optional |
| Prior-season active limit | 40 | 47 | 53 | 53 |
| Active veterans available | 1,040 | 1,316 | 1,590 | 1,643 |
| Losses per existing club | 1 or 2 | 1 or 2 | 0 or 1 | 0 or 1 |
| Clubs at the maximum | 6 lose 2 | 24 lose 2 | 26 lose 1 | 26 lose 1 |
| Clubs at the minimum | 20 lose 1 | 4 lose 1 | 4 lose 0 | 5 lose 0 |
| Veteran selections, total | 32 | 52 | 26 | 26 |
| Veteran selections per new club | 16 | 26 | 26 | 26 |
| Minimum players exposed | 52 | 56 | 30 | 31 |
| Exposed and not selected | 20 | 4 | 4 | 5 |
| Optional taxi names | 208 | 224 | 240 | 248 |
| Draftable class | 476 | 210 | 217 | 224 |
| Rookie picks per new club | 17 | 7 | 7 | 7 |
| Rookies taken by new clubs | 34 | 14 | 7 | 7 |
| Draftable left for existing clubs | 442 | 196 | 210 | 217 |
| Undrafted multiplier | 2 | 2 | 2 | 2 |
| Undrafted pool | 56 | 60 | 62 | 64 |
| Reserved undrafted per new club | 7 | 20 | 20 | 20 |
| Reserved undrafted, all new clubs | 14 | 40 | 20 | 20 |
| Undrafted prospects left for ordinary signing | 42 | 20 | 42 | 44 |

1976 active roster, with no free agents counted:

16 veterans + 17 drafted rookies + 7 undrafted rookies = 40.

`16 × 2 = 32`. The balanced losses are `6 × 2 + 20 × 1 = 32`. The slack below a universal maximum of 2 is `26 × 2 − 32 = 20`, which is why twenty clubs lose 1 rather than 2. The old band of 2 or 3 does not fit: 26 clubs each losing at least 2 would be 52 veterans, and two new 40-man rosters that each take 17 rookies have 46 non-rookie spots. The undrafted pool stays `2 × 28 = 56`. The two new clubs reserve 14 of those 56. The other 42 remain available to any club. A free-agent signing may replace one of the reserved undrafted signings. Taxi spots may be filled afterward when players remain.

1995, 1999, and 2002 active roster, with no free agents counted:

26 veterans + 7 drafted rookies + 20 undrafted rookies = 53.

In 1995, `26 × 2 = 52`. The balanced losses are `24 × 2 + 4 × 1 = 52`. The slack below a universal maximum of 2 is `28 × 2 − 52 = 4`. The undrafted pool is `2 × 30 = 60`. The two new clubs reserve 40. The other 20 remain for ordinary signing. That remainder is smaller than the 28 existing clubs. The design does not promise each existing club an undrafted signing. The 52 veteran selections come from the 1,316 active players on the 1994 rosters, not from the 60 undrafted prospects.

In 1999 and 2002 one new club selects 26 veterans. That cannot levy a loss on all 30 or 31 existing clubs. The minimum is therefore 0. In 1999, 26 clubs lose 1 and 4 lose 0. In 2002, 26 clubs lose 1 and 5 lose 0. The undrafted pools are `2 × 31 = 62` and `2 × 32 = 64`. Each year reserves 20, so 42 and 44 remain. There is no guaranteed veteran free-agent reserve. A free-agent signing may replace an undrafted signing.

The old multiplier of 3 is not used in any year. It is not required for a reserved count of 14, 40, 20, or 20.

Publication check, taxi excluded and free agents excluded:

veteran selections per new club + rookie picks per new club + undrafted reserved per new club = active target

The reserved count times the number of new clubs must also fit inside that year's shared undrafted pool. 1976 is `7 × 2 = 14`, and 14 is less than 56. 1995 is `20 × 2 = 40`, and 40 is less than 60. 1999 and 2002 are `20 × 1 = 20`, and 20 is less than 62 and less than 64. Publish is refused when either equality fails or the generated undrafted class is smaller than the pool on the expansion event. The check does not look at a timeline. The veteran side is feasible when the minimum exposed board is at least the veteran total: 32 ≤ 52, 52 ≤ 56, 26 ≤ 30, and 26 ≤ 31.

When a timeline reaches the expansion offseason, the same shared classes are still there, so a short free-agent list cannot set the season to blocked and cannot create a person. An expansion club may not trade away a rookie pick, or release a reserved undrafted claim, while the three guaranteed sources would then add to less than the active target. A free agent who has not been signed does not restore that sum.

#### How the draft keeps the losses balanced

The new clubs still make meaningful selections. The engine does not pre-assign which clubs lose the maximum, and it does not pick the players for them. It only decides which existing clubs are still eligible before each selection.

Each existing club starts at 0 losses. A selection is legal only when its source club passes both tests:

1. The club is under the maximum. At the maximum it is closed.
2. If the club is still under the minimum, it stays eligible. If it is already at the minimum, one more loss would put it in the maximum group. That extra loss is allowed only while fewer than `clubs_at_max_loss` clubs are already at the maximum, and only while the picks that would remain are still enough to bring every club up to the minimum.

Early in the draft almost every club is eligible, so the selecting club can chase the exposed player it wants. Late in the draft the board narrows to the clubs that still owe a loss, which is what forces the 6-and-20 split in 1976 and the 24-and-4 split in 1995. In those two years the minimum is 1, so a completed draft does not finish with one club at the maximum and another at zero. In 1999 and 2002 the minimum is defined as 0. Four clubs in 1999 and five in 2002 finish there, because 26 veteran selections cannot reach every existing club.

Staffing order in an expansion offseason:

1. The owner stays or applies.
2. The award is recorded. A single-player application is automatic.
3. The new club hires a GM, then a head coach, from the shared pool.
4. That GM and coach hire the coordinators. The owner is not asked to.
5. The owner sets up to three position priorities and compete-or-accumulate. A staying owner's priorities are protection priorities. An expansion owner's priorities are acquisition priorities.
6. Existing GMs file the protection lists. Expansion GMs do not.
7. The GM runs the snake draft under the loss rule above. Involved mode, if the owner turned it on, lets the owner pin five names. Otherwise the GM selects.
8. The rookie draft, then the reserved undrafted signings that finish the active roster. Free agency may replace some of those undrafted signings. It is not required, and its absence does not stop the season.
9. Any players still available may fill taxi spots. The head coach sets the depth chart and the special-teams roles.

### Owner access to expansion

A normal timeline still begins in 1970 on a founding franchise. In the 1976, 1995, 1999, and 2002 offseasons, before the expansion draft, the owner makes a few choices. The GM and head coach carry out the detail.

1. **Stay or apply.** The owner keeps the current franchise, or applies for one expansion franchise. Applying is the only pursuit action. There is no separate purchase price in this design.
2. **If the owner stays,** they name up to three position groups to protect. The GM files the list, keeps the only quarterback, kicker, and punter when the club has just one, and exposes at least the maximum: 2 players in 1976 and 1995, and 1 player in 1999 and 2002. Involved mode can pin five names. The GM fills the rest.
3. **If the owner is awarded an expansion franchise,** a later service closes the open `TimelineFranchiseControl` with `expansion_switch` and opens a stint on the new franchise. The former franchise becomes bot-controlled in that timeline, with its current GM, coach, and roster. The new franchise starts with no players and no staff. This ownership change does not alter the shared membership row and does not set `owner_selectable` false. The human who takes the expansion franchise may keep controlling it in later seasons, because those shared rows stay selectable. Phase 4 stores the choice and the control history. It does not perform the award.
4. **Building the new franchise.** The owner sets up to three position groups to chase, and compete or accumulate. The GM and head coach, once hired, run the expansion draft, the rookie draft, and the reserved undrafted fill. Free agency is optional. The head coach then sets the depth chart.

The new club's players, GM, and head coach are people who already exist on the release. The expansion draft only chooses among veterans already in that timeline. The rookie class that year is the shared class sized with `N(1976) = 28`, `N(1995) = 30`, `N(1999) = 31`, or `N(2002) = 32`.

**Acceptance is not fully decided.**

| Situation | Proposed handling |
|-----------|-------------------|
| Single-player timeline, owner applies for one of that year's new clubs | Automatic acceptance. Any other new club that year is bot-run. |
| Several human owners want the same expansion club | A fair selection among the applicants. The recommended draw is HMAC-SHA256 of the universe seed, the year, the franchise slug, and the sorted applicant ids. It is reproducible. It is not first-click, and it is not an unrepeatable random call. Each human can be awarded at most one expansion club. Leftover clubs are bot-run. The choice of this method still needs approval. |
| Owner does not apply | They remain with the current franchise. Bots run every expansion club. |

Whether a separate game mode may start a timeline already in 1976, 1995, 1999, or 2002, skipping 1970, remains unresolved. The normal game begins in 1970. That later-start question is not answered by `owner_selectable`. If such a mode is implemented, its rules belong in timeline-start configuration or another later-phase structure. Phase 1 does not add that configuration.

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
| `position` | Shared | enum | `QB` `HB` `FB` `WR` `TE` `LT` `LG` `C` `RG` `RT` `DE` `DT` `ILB` `OLB` `CB` `FS` `SS` `K` `P` | Generator | Both |
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
| `return_vision` | Finding a return lane and choosing a return or a fair catch. |
| `special_teams` | Coverage, blocking, and lane discipline. |
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
| `franchise` | Shared | FK on `OpeningAssignment`, null for free agents | one of 26, or null | Generator | Both |
| `role` | Shared | enum on `OpeningAssignment` | `starter` `backup` `taxi` `free_agent` | Generator | Both |
| `depth` | Shared | smallint on `OpeningAssignment` | ≥ 1 on a team; null if free agent | Generator | Both |
| `position` | Shared | enum on `UniversePlayer` | the player's primary position; not a column on `OpeningAssignment` | Generator | Both |
| `years` | Shared template | smallint | 1–5 remaining entering 1970 | Generator | Both |
| `salary_per_year` | Shared template | int | ledger units. The scale is unset until approved. | Generator | Display before 1995; Both from the cap era on the timeline copy |
| `guaranteed` | Shared template | int | ledger units, 0–salary × years | Generator | Display before 1995; Both once the cap exists, on the timeline copy |
| `signing_bonus` | Shared template | int | ledger units | Generator | Display |
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

Exactly one of `player` or `staff` is set. All-league rows name a position as `slot`, using one of the 19 primary positions (`QB`, `HB`, and the rest).

### College history

`UniverseCollegeSeason`, one row per player per college season. Planning default: 4 seasons each. The anchor year is `first_pro_year` when set, otherwise the draft-class year. College seasons are the four years before that anchor. Origin: Generator. Use: Display. Draft `board_rank` already carries the scouting conclusion, so college counting stats are not a second sim input.

| Field | Type | Range / units | Applies to |
|-------|------|---------------|------------|
| `season_year` | smallint | the four years before the anchor year | All |
| `class_standing` | enum | `freshman` `sophomore` `junior` `senior` | All |
| `games`, `starts` | smallint | nonnegative; starts do not exceed games. No permanent season-length cap. | All |
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

#### Halfback and fullback

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

`run_block_snaps` is Both for fullbacks and Display for halfbacks.

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

#### Offensive line (`LT`, `LG`, `C`, `RG`, `RT`)

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

#### Linebacker (`ILB`, `OLB`)

Same defensive columns as the line, with higher tackle ranges (`tackles` 0–200, `sacks` usually lower). Add:

| Field | Type | Range / units |
|-------|------|---------------|
| `coverage_snaps` | smallint | 0–`defensive_snaps` |
| `targets_in_coverage` | smallint | 0–80 |
| `completions_allowed` | smallint | 0–`targets_in_coverage` |
| `interceptions` | smallint | 0–10 |

#### Defensive back (`CB`, `FS`, `SS`)

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

Created for opening-cohort players at `start_timeline`, for a draft class when that class is materialized, and never for the archive cohort. The implemented start copies the 1,378 opening players and does not materialize the 1970 class.

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `timeline` | Timeline | FK | | Owner's new timeline | Both |
| `player` | Timeline | FK | universe player | Shared template | Both |
| `status` | Timeline | enum | `prospect` `active` `taxi` `reserve` `free_agent` `retired` | Timeline sim, or the opening copy | Both |
| `current` ratings | Timeline | 33 × smallint | 0–100, ≤ potential | Copied from `initial`, then Timeline sim | Both. Hidden as exact numbers. The owner sees `TimelineScoutComponent` until evaluation sets uncertainty to 0. |
| `seasons_played` | Timeline | smallint | 0–60, pro seasons inside this timeline only | Derived, may be cached | Display |
| `career_altering_injuries` | Timeline | smallint | 0–10 | Timeline sim | Both |

Unique `(timeline, player)`.

#### Scout report

`TimelineScoutReport` is one row per overlay. `TimelineScoutComponent` is one row per component per kind (`ability` or `potential`). The sim never reads these rows. It reads `current`. Field-by-field visibility is in the characteristic audit. The first report, and every later refresh, is the deterministic function in [Deterministic scouting and results](#deterministic-scouting-and-results). Owned-roster reports start with uncertainty 20 and reach 0 after four weeks on the roster or 100 snaps, whichever comes first. Other players start between 25 and 60, tightened when the evaluating GM has a higher `talent_evaluation`. Potential often stays uncertain. When ability uncertainty hits 0, `revealed_value` is set to `current`.

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
| `salary_per_year` | Timeline | int | ledger units. Before 1995, display and negotiation flavor. From 1995, cap accounting | same | Both from 1995; Display before |
| `guaranteed` | Timeline | int | ledger units | same | same |
| `signing_bonus` | Timeline | int | ledger units | same | Display; Sim from 1995 (cap proration) |
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
delta = f(gap, age vs peak_age, snaps, durability, injury weeks, coaching, stable event draw)
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

`UniverseStaffPost` ends no later than `debut_year − 1`. A post that names a Federation franchise also ends before the release's first playable season, so shared history does not assign one franchise job after timeline play begins. A college or other external post may run through the season before debut. Use: Display. Ratings are what the sim reads; the posts are generated to agree with those ratings. Timeline employment with a Federation franchise stays on the timeline.

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `role` | Shared | enum | `gm` `personnel_director` `scout` `college_coach` | Generator | Display |
| `franchise` | Shared | FK, nullable | one employer source; a Federation franchise | Generator | Display |
| `college` | Shared | FK, nullable | one employer source; a shared college | Generator | Display |
| `organization_name` | Shared | string, blank | one employer source; another external organization, ≤ 255 | Generator | Display |
| `start_year`, `end_year` | Shared | smallint | through the year before debut; a franchise post also ends before play begins | Generator | Display |
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

The generator may give the initial 100 head coaches `specialty = both`. That pool choice is not a schema rule. Offensive coordinators are `offense` or `both`. Defensive coordinators are `defense` or `both`. `head_coach`, `position_coach`, and `college_head_coach` permit `offense`, `defense`, or `both`. Specialty describes background and orientation. It does not stop a person from becoming a head coach, and it does not change when a coordinator is promoted. A coach with specialty `offense` may become a head coach and remain `offense`. It does not mean the two scheme ratings are equal. `offensive_scheme` and `defensive_scheme` are the abilities.

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

Opening assignment: `OpeningStaffAssignment (release, staff, franchise, role)` for the 26 employed GMs, 26 employed head coaches, 26 offensive coordinators, and 26 defensive coordinators. Unique `(release, franchise, role)` and unique `(release, staff)`. `fg-founding-1` still writes 24 GM seats and 24 head-coach seats and does not write coordinators.

### Coordinators

Coordinators belong in the initial database even though the first playable version only asks the owner to hire a GM and a head coach. A later firing has to promote someone who already exists. Adding coordinators in a second schema would split staff history.

Use one coaching person record for head coaches, offensive coordinators, defensive coordinators, and position coaches. The rating block is the head-coach block. `specialty` is `offense`, `defense`, or `both`. A stint's `role` is `head_coach`, `offensive_coordinator`, `defensive_coordinator`, or `position_coach`. Promotion is a new stint on the same person, not a new identity.

Recommended 1970 coordinator pool, same identities in every timeline:

| | Employed on a founding club | Unemployed |
|--|-----------------------------|------------|
| Offensive coordinator | 26 | 14 |
| Defensive coordinator | 26 | 14 |

The initial pool stays 40 per specialty. Twenty-six of those seats are filled in 1970, so 14 in each specialty remain unemployed. Incoming floor, 1971–2029: `4 + E(year)` offense-specialty candidates and `4 + E(year)` defense-specialty candidates. With `E` of 2, 2, 1, and 1, that is 242 per specialty and **484** incoming coordinators. Initial 80 plus 484 is **564** coordinator identities. GM and head-coach identities are **1,038**. All staff identities in the recommended database are **1,602**.

Version 1 need not show coordinator hiring. The rows are still generated with the release. The same person can remain a coordinator, move to another club, be promoted after a firing, enter an offseason as a head-coach candidate, or return to coordinator work after being fired as a head coach. Each change appends a stint on the same `universe_key`. Pre-1970 posts and timeline stints stay one career. A promotion closes the coordinator stint with `promoted` and opens a `head_coach` stint. A later demotion closes the head-coach stint with `fired` and opens a coordinator stint. An expansion club hires from this shared pool. Do not generate a separate position-coach labor pool. The `position_coach` role exists so historical posts use this same table.

---

### Owner directives

One `OwnerDirective` row for one human participant and one season. Required before that season can simulate. The defaults below let the owner accept the screen. Bot franchises have no participant row. A later service may derive their tendencies from the shared staff tags. That service is not this table, and it is not a weekly stack of JSON sliders. The row locks when the season simulation begins. The owner never sees a slider for each of the 33 player components.

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `participant`, `season` | Timeline | FK | the human and that campaign year | Owner | Both |
| `team_direction` | Timeline | enum | `compete` `balanced` `rebuild`. Default `balanced`. | Owner | Sim |
| `priority_group_1`, `priority_group_2`, `priority_group_3` | Timeline | enum, null | up to three distinct groups, in order: `quarterback` `skill` `offensive_line` `defensive_line` `linebacker` `defensive_back` `special_teams`. The second requires the first. The third requires the second. | Owner | Sim |
| `trade_appetite` | Timeline | enum | `low` `medium` `high`. Default `medium`. | Owner | Sim |
| `veteran_preference` | Timeline | enum | `automatic` `youth` `balanced` `veterans`. Default `automatic`, which the GM derives from team direction. | Owner | Sim |
| `draft_participation` | Timeline | enum | `owner_selects` `delegate_to_gm`. Default `delegate_to_gm`. | Owner | Sim |
| `offensive_emphasis` | Timeline | enum | `run` `balanced` `pass`. Default `balanced`. | Owner | Sim |
| `defensive_emphasis` | Timeline | enum | `stop_run` `balanced` `pressure_quarterback`. Default `balanced`. | Owner | Sim |
| `fourth_down` | Timeline | enum | `conservative` `balanced` `aggressive`. Default `balanced`. | Owner | Sim |
| `playing_time` | Timeline | enum | `automatic` `veterans` `balanced` `develop_youth`. Default `automatic`, which the head coach derives from team direction. | Owner | Sim |
| `locked` | Timeline | bool | set when that season's simulation begins | Timeline sim | Both |

The GM still handles contract detail, cuts, signings, the shape of a trade, expansion selections, undrafted signings, and roster balance. The head coach still handles the depth chart, the weekly lineup, special teams, fatigue, injuries, coordinators, and play calls. The UI shows the current directive. The sim reads the locked row for that season.

---

### Timeline header

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `created_by` | Timeline | FK, null | `settings.AUTH_USER_MODEL`, `SET_NULL`. Not the only human the campaign can ever have. | Owner | Display |
| `release` | Timeline | FK | published release, `PROTECT`. A timeline cannot change it. | Owner | Both |
| `name` | Timeline | string | ≤ 255 | Owner | Display |
| `start_season_year` | Timeline | smallint | The creation path uses the release's first season. For the confirmed release that is 1970. The column is not fixed to 1970. | Owner | Both |
| `current_season_year`, `season_index` | Timeline | smallint | `current = start + index - 1`, inside the release | Timeline sim | Both |
| `phase` | Timeline | enum | see opening sequence | Timeline sim | Both |
| `status` | Timeline | enum | `active` `completed` `archived` | Timeline sim | Both |
| `decision_audit` | Timeline | string, optional | The audit hash named `decision_fingerprint` in earlier notes. It is not an input to random draws. | Derived | Display |

`TimelineParticipant` joins a user to the timeline with `active` or `departed`, the season they joined, and timestamps. The same user is not added twice. A single-player campaign has one active participant. Deleting that account sets the user reference null and does not delete the timeline. The participant no longer represents a playable human. The franchise is treated as bot-controlled until a later account-recovery or commissioner service reassigns it. Control history stays. Phase 4 does not reassign it.

`TimelineFranchiseControl` records the franchise, the first controlled season, an optional final season, and why control ended. `expansion_switch` is the reason when a later service moves the owner to an awarded expansion club. One participant has one open control. One franchise has one open human. Everyone else is a bot, with no participant and no control row. Past stints stay after a switch.

The normal game begins in the release's first season. Whether a later mode may begin in 1976 or 1996 remains unresolved. If that mode is implemented, store its rules in timeline-start configuration. Do not infer them from `owner_selectable`. The schema does not have to change to hold a different start year. This document does not define the skipped seasons. Phase 4 does not add billing, invitations, or a waiting room.

#### Expansion choice

`TimelineExpansionChoice`, one row per participant per expansion event, written before that year's expansion draft. Several humans may apply for the same club. Phase 4 does not resolve the collision.

| Field | Layer | Type | Range / units | Origin | Use |
|-------|-------|------|---------------|--------|-----|
| `participant` | Timeline | FK | the human making the choice | Owner | Both |
| `event` | Timeline | FK | the shared expansion event | The release | Both |
| `controlled_franchise` | Timeline | FK | the franchise that participant currently controls | Owner | Both |
| `decision` | Timeline | enum | `stay` `apply` | Owner | Both |
| `target_franchise` | Timeline | FK, null | one of that event's expansion clubs, required only for `apply` | Owner | Both |
| `resolution` | Timeline | enum | `pending` `awarded` `not_awarded` `withdrawn` | Later service | Both |
| `selection_method` | Timeline | enum | `not_needed` `automatic` `fair_draw` | Later service | Display |
| `pinned_players` | Timeline | 0–5 `ExpansionPin` rows | shared players, priority 1–5, only for `apply`, only in involved mode | Owner | Sim |

Position priorities stay on the owner directive. The GM still files the full protection list later. `ExpansionPin` does not select a player and does not protect one. In a single-player timeline, `apply` may later resolve as `automatic`. When several human owners apply for the same club, the stored method may be `fair_draw`, the HMAC described in the expansion section. Phase 4 does not run that draw. Awarding a club does not itself close the old control stint. Each human can be awarded at most one expansion club. The owner does not set the rest of the roster, the contract figures, or the depth chart.

---

## Deterministic scouting and results

True identity, original ratings, potential, college history, and pre-1970 statistics are shared. A scouting report is timeline data, because the GM who is looking changes the report. The report is still reproducible.

Scout noise and game draws use one stable sequence. The owner's decisions are not part of that sequence. Changing a directive, a draft pick, or a coach does not reroll the season. The timeline id is not part of it either.

A draw is HMAC-SHA256 of:

- the universe-release seed
- the season
- the week
- the participating franchise keys, in a fixed order
- the game identifier from the schedule
- the event or snap index inside that game
- the event type, when the draw is tied to a type of event

The same coordinates always produce the same number. Roster quality, injuries, coaching, strategy, and owner decisions change the probabilities and the choices that consume those numbers. They do not replace the sequence.

Scout reports use the same rule. The noise for a component is the draw for that player, component, kind (`ability` or `potential`), season, and week. `talent_evaluation` changes how wide a band is placed around that noise. A better GM shows a narrower band. Replacing the GM does not draw a new noise value. The simulator never reads `revealed_value` or `uncertainty`. It reads `current`.

If a changed decision makes a game take a different number of snaps, or a different order of event types, later indexes no longer refer to the same moment. That is ordinary divergence of the game state. It is not a new seed. Early snaps that still occur at the same index keep the same random numbers. Their results can still differ because the probability of each outcome changed.

A random draw and a snap outcome are different things.

- The draw is reproducible from the universe-release seed and the coordinates above. Nothing else is required. The timeline id and the owner's decisions are not inputs.
- The snap outcome also depends on the exact pre-snap state: who is on the field, their ratings, fatigue, the score, field position, the strategy in force, and the era rules. The same draw applied to a different state can produce a different outcome.
- A hash of that pre-snap state can confirm that a reconstructed state matches the original. The hash does not contain the state, so it cannot rebuild the snap by itself.
- If the complete pre-snap state was not stored, the snap outcome is recovered by replaying earlier events from a stored point: the kickoff inputs, or a later checkpoint. It is not recovered from the coordinates alone.

The practical record for the first playable version is deliberately smaller than a snapshot before every snap:

- The game keeps the deterministic coordinates.
- Kickoff stores the inputs the match needs to start: participants, availability, ratings, and fatigue. That is one snapshot per game.
- An ordered play or event log records the sequence, the participants, and the persistent result of each kept event.
- Periodic mid-game checkpoints are not required for that first version. They can be added later if a long game needs a nearer restart point.
- The final score is stored, and so are persistent events such as injuries.
- A detailed snap log, including enough state to inspect one snap without replaying, is optional and off by default.
- A pre-state or post-state hash may be stored beside an event so a replay can be checked. It is a verification value, not a substitute for the kickoff snapshot or the log.

Phase 4 stores that foundation and does not simulate it. `GameKickoffState` keeps the payload, its hash, the calendar week, and the home and away franchises. `game_key` is stable across comparable timelines. It is not derived from the timeline id, the timeline primary key, the kickoff primary key, or any other timeline-specific identifier. A later schedule service should build it from the universe release, the season, the calendar week or round, the home and away franchise keys, and the game type when that is needed. The timeline still owns the row. The key is how comparable timelines retrieve the same underlying random sequence. `schedule_length` is not used as the maximum week: it counts games played per franchise, and a later schedule model decides which weeks exist, which are byes, and how postseason weeks are numbered. `SimulationAuditRecord` keeps the ordered event for that kickoff. The random draw is still recomputed. It is not a column. Phase 4 does not implement the HMAC or the schedule service.

Two timelines with the same rosters, ratings, and choices get the same draws for the same game, so the comparison is fair. Replay value comes from a different roster, rating, or call. It does not come from shaking the season.

`current` is still copied from shared `initial` when the player enters the timeline, and it still moves only by the development rules. Those rules may consume a draw at a stated event index. They do not keep a private timeline seed.

## Historical consistency

Every shared person has the same identity and the same pre-timeline history in every timeline on a release.

These values are generated once and never diverge:

| Kind of fact | Examples |
|--------------|----------|
| Identity | `universe_key`, name, birth year, birth day, college, height, weight, handedness, primary position, secondary positions |
| Original talent | `initial` and `potential` on every rating component, `peak_age`, `development_rate`, `decline_rate`, trait tags |
| Entry facts that are biography | cohort, origin era, `board_rank`, class membership, `origin_draft_year`. For opening and archive players, also `origin_draft_round`, `origin_draft_pick`, and `origin_draft_franchise`. For a 1970–2029 prospect those three stay null, because the destination is a timeline result. |
| Life before the timeline starts | College seasons, 1960–1969 pro seasons, 1960–1969 awards, 1960–1969 staff posts, the 1970 opening roster and opening contract templates, opening staff assignments. `fg-founding-2` has no 1969 standings. The 1970 draft order is the shared neutral order. |
| League structure | Franchise placeholders, `N(year)`, conference, division, `slot_order`, expansion years |

`first_pro_year` on a draftable prospect is the class year he is eligible. It is the same in every timeline. Whether he is actually drafted, and the year he takes a snap, are timeline facts.

These values may diverge after timeline decisions begin:

| Kind of fact | What moves it |
|--------------|----------------|
| `current` ratings | Age against `peak_age`, snaps, coaching, and career-altering injuries. A development draw uses the stable event coordinates. Clamped to `potential`. |
| Scout reports | Film, workouts, and roster evaluation in that timeline. The sim does not read them. |
| Assignment | Drafts, cuts, trades, free agency, the expansion draft, retirement |
| Health | Injury rows and `career_altering_injuries` |
| Money | Timeline contracts. The 1970 template is only the starting copy. |
| Counting stats, awards, development events | 1970–2029 play in that timeline |
| Staff jobs | Hiring, firing, promotion, retirement. Pre-1970 posts stay shared. |
| Reputation | Results, awards, and firings inside the timeline. The shared `initial` reputation does not change. |
| Directives and expansion choice | Owner decisions |
| Season results | The stable draw sequence, applied to this timeline's rosters, ratings, injuries, and play calls. The same pre-snap state produces the same snap outcome. The draw itself can be recomputed without that state. |

A particular player always enters the 1970 draft, if he is in that class, with the same name, college, body, college statistics, original talent, and the same true ratings. The underlying scout noise for that player is the same in every timeline. The band around it depends on who is scouting. A GM or coach always begins with the same biography, abilities, employment history, and reputation. Drafting, hiring, playing time, injuries, development, results, and firings may then send those careers apart. The random draws stay reproducible from the release seed and the game coordinates. They are not a fresh random sequence, and they are not rerolled when an instruction changes. A full snap outcome is reproducible when the replay starts from the stored kickoff inputs, or from a checkpoint, and applies the same state. The coordinates alone do not rebuild that outcome.

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
| `component` | Which rating the row describes | enum | the 33 components | Timeline | Visible as a label | Display | Created with the overlay | The label does not change | Exact label |
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
| `position` | Primary position | enum | the 19 positions `QB` `HB` `FB` `WR` `TE` `LT` `LG` `C` `RG` `RT` `DE` `DT` `ILB` `OLB` `CB` `FS` `SS` `K` `P` | Shared | Visible | Both | Roster quota or class mix | Never | Exact |
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

### Original talent, growth, and the 33 components

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
| `return_vision` | Finding a lane and choosing a return or a fair catch | Slow. Snaps on returns can raise it. Near a low baseline for linemen. |
| `special_teams` | Coverage, blocking, and lane discipline | Snaps on special teams, coaching. |
| `work_ethic` | Practice and development effort | Slow. Age and a career-altering injury can move it. |
| `leadership` | Teammate effect | Slow. Same. |
| `temperament` | Composure after errors | Slow. A career-altering injury or a long injury year can lower it. |
| `clutch` | High-leverage modifier | Slow. |
| `consistency` | Width of a single-game swing around `current`. It does not change the season mean. | Slow. |
| `adaptability` | How fast usage recovers after a scheme change or a game at a secondary position | Slow. |

There is no stored `overall`, no stored `scheme_fit`, and no stored `football_iq`. Football intelligence is `awareness`, `processing`, and `discipline`. Scheme fit is derived at sim time from `adaptability`, the skill vector, and the coach's scheme. The owner sees that fit as `poor`, `fair`, or `good`: partial until evaluation, then an exact label. The label is not stored.

### How positions use the components

Every position stores the same 33 components. Low baselines keep non-position skills from dominating. The sim weights the rows below. Those weights are season configuration, not player columns. Secondary positions use the same ratings, with `adaptability` reducing the penalty for playing off the primary position.

| Position | Components that decide play | Position-specific season record |
|----------|-----------------------------|----------------------------------|
| `QB` | `throw_power`, `throw_accuracy`, `awareness`, `processing`, `clutch`, `consistency`, plus mobility from `speed` and `elusiveness` | Passing line and quarterback rushing |
| `HB` | `speed`, `acceleration`, `agility`, `carrying`, `elusiveness`, `catching`, `stamina`, `return_vision` | Rushing, receiving, and returns when assigned |
| `FB` | `strength`, `run_blocking`, `carrying`, `catching` | Rushing, receiving, `run_block_snaps` |
| `WR` | `speed`, `acceleration`, `agility`, `catching`, `route_running`, `return_vision` | Receiving, a small rushing line, and returns when assigned |
| `TE` | `catching`, `route_running`, `run_blocking`, `pass_blocking`, `strength` | Receiving and block snaps |
| `LT`, `LG`, `C`, `RG`, `RT` | `strength`, `run_blocking`, `pass_blocking`, `awareness`, `discipline` | Block snaps and `sacks_allowed`. The position is the side and the alignment. There is no separate center-snap rating. A center's long-snap work uses `special_teams` when he holds that role. |
| `DT`, `DE` | `strength`, `pass_rush`, `run_defense`, `speed` | Tackles, sacks in tenths, hits, forced fumbles |
| `ILB`, `OLB` | `tackling`, `coverage`, `run_defense`, `awareness`, `speed`, `special_teams` | Defensive line columns plus coverage |
| `CB` | `speed`, `agility`, `coverage`, `awareness` | Coverage, interceptions, passes defended |
| `FS`, `SS` | `coverage`, `tackling`, `awareness`, `speed`, `run_defense` | Coverage and tackles |
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
| `current` (33) | Ability right now in this timeline | smallint | 0–100, ≤ potential | Timeline | Hidden | Both. The sim reads this exact vector. | Copied from `initial` at entry | Age, snaps, coaching, injury. Not a direct function of one game's box score. | Scout estimate until evaluation, then Exact |
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
| `salary_per_year` | Annual pay | int | ledger units. Shown as a cap percent from 1995. | same | Visible | Display before 1995. Both from the 1995 cap. | same | New contract | Exact |
| `guaranteed` | Guaranteed ledger units | int | 0 through salary × years | same | Visible | same | same | New contract | Exact |
| `signing_bonus` | Bonus | int | ledger units of the signing year | same | Visible | Display. Sim from 1995 through cap proration. | same | New contract | Exact |
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
| `component`, `delta`, `reason` | Development audit | enum, smallint, enum | component is one of the 33; delta −20 to +20; reason `age` `snaps` `injury` `coaching` | Timeline | Hidden | Display | Sim | Append-only | Hidden. The owner sees a plain-language summary, not the delta log. |

Undrafted signings create a contract and a stint and no pick row. Expansion-draft picks are `TimelineExpansionPick`, not a second copy of the player.

### College history

`UniverseCollegeSeason`. Planning default is four seasons, the four years before `first_pro_year` or the class year. Display only. The sim reads `board_rank` and the rating vector, not these counting stats. Every column is shared, visible, exact, generated once, and never changes.

| Field | Meaning | Type | Range |
|-------|---------|------|-------|
| `season_year` | College season | smallint | the four years before the anchor |
| `class_standing` | Year in school | enum | `freshman` `sophomore` `junior` `senior` |
| `games`, `starts` | Games and starts | smallint | nonnegative; starts do not exceed games. Schedule length is a later rule, not a permanent 12-game cap. |
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
| `HB`, `FB` | `rush_attempts` | Rushes | smallint | 0–450 |
| `HB`, `FB` | `rush_yards` | Rushing yards | int | −50–2,500 |
| `HB`, `FB` | `rush_touchdowns` | Rushing touchdowns | smallint | 0–30 |
| `HB`, `FB` | `targets` | Targets | smallint | 0–150 |
| `HB`, `FB` | `receptions` | Receptions | smallint | 0–`targets` |
| `HB`, `FB` | `receiving_yards` | Receiving yards | int | −20–1,200 |
| `HB`, `FB` | `receiving_touchdowns` | Receiving touchdowns | smallint | 0–15 |
| `HB`, `FB` | `drops` | Drops | smallint | 0–`targets` − `receptions` |
| `HB`, `FB` | `run_block_snaps` | Run-block snaps | smallint | 0–offensive snaps. Both for fullbacks. Display for tailbacks. |
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
| `LT`, `LG`, `C`, `RG`, `RT` | `run_block_snaps` | Run-block snaps | smallint | 0–`offensive_snaps` |
| `LT`, `LG`, `C`, `RG`, `RT` | `pass_block_snaps` | Pass-block snaps | smallint | 0–`offensive_snaps` |
| `LT`, `LG`, `C`, `RG`, `RT` | `sacks_allowed` | Sacks allowed | smallint | 0–20 |
| `DT`, `DE`, `ILB`, `OLB` | `tackles` | Tackles | smallint | 0–120 on the line; 0–200 for linebackers |
| `DT`, `DE`, `ILB`, `OLB` | `tackles_for_loss` | Tackles for loss | smallint | 0–30 |
| `DT`, `DE`, `ILB`, `OLB` | `sacks` | Sacks in tenths | smallint | 0–250. 75 means 7.5. |
| `DT`, `DE`, `ILB`, `OLB` | `quarterback_hits` | Hits on the quarterback | smallint | 0–40 |
| `DT`, `DE`, `ILB`, `OLB` | `forced_fumbles` | Forced fumbles | smallint | 0–10 |
| `DT`, `DE`, `ILB`, `OLB` | `fumble_recoveries` | Fumble recoveries | smallint | 0–8 |
| `DT`, `DE`, `ILB`, `OLB` | `passes_defended` | Passes defended | smallint | 0–15 |
| `DT`, `DE`, `ILB`, `OLB` | `defensive_touchdowns` | Defensive touchdowns | smallint | 0–4 |
| `ILB`, `OLB` | `coverage_snaps` | Snaps in coverage | smallint | 0–`defensive_snaps` |
| `ILB`, `OLB` | `targets_in_coverage` | Targets allowed | smallint | 0–80 |
| `ILB`, `OLB` | `completions_allowed` | Completions allowed | smallint | 0–`targets_in_coverage` |
| `ILB`, `OLB` | `interceptions` | Interceptions | smallint | 0–10 |
| `CB`, `FS`, `SS` | `tackles` | Tackles | smallint | 0–140 |
| `CB`, `FS`, `SS` | `interceptions` | Interceptions | smallint | 0–15 |
| `CB`, `FS`, `SS` | `passes_defended` | Passes defended | smallint | 0–30 |
| `CB`, `FS`, `SS` | `targets_in_coverage` | Targets in coverage | smallint | 0–140 |
| `CB`, `FS`, `SS` | `completions_allowed` | Completions allowed | smallint | 0–`targets_in_coverage` |
| `CB`, `FS`, `SS` | `yards_allowed` | Yards allowed | int | 0–1,500 |
| `CB`, `FS`, `SS` | `forced_fumbles` | Forced fumbles | smallint | 0–8 |
| `CB`, `FS`, `SS` | `defensive_touchdowns` | Defensive touchdowns | smallint | 0–4 |
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

Career history before he is hireable is `UniverseStaffPost`: `role` (`gm`, `personnel_director`, `scout`, `college_coach`), exactly one employer (`franchise`, `college`, or `organization_name`), years, wins, losses, ties, playoff appearances, championships, and `ended_by` (`resigned`, `fired`, `promoted`, `retired`, `post_completed`). Those rows are shared, visible, exact, display only, generated to agree with the ratings, and they never change. A Federation franchise post ends before play begins. An external post may run through the year before debut.

Timeline employment is `StaffStint`: role `gm`, franchise or null, start and end year, `reason_ended` (`opening_replace`, `fired`, `retired`, `hired_elsewhere`, `promoted`, `timeline_complete`). The stint is timeline state, visible, exact, and both sim and display. Wins, losses, ties, playoff appearances, and championships during the stint are derived from that timeline's standings. They are not stored twice. They are visible and exact.

### Head coaches

The same identity rules as GMs apply, with key prefix `c-`. `specialty` is `offense`, `defense`, or `both`. The generator may still make the original 1970 head-coach pool `both`. Offensive-coordinator posts require `offense` or `both`. Defensive-coordinator posts require `defense` or `both`. Head coach, position coach, and college head coach permit any specialty. Specialty does not change on promotion. `former_role` is `player`, `college_coach`, `coordinator`, or `position_coach`.

Ability movement matches the GM rules. `preferred_offense` and `preferred_defense` never change. Scheme ratings can rise inside potential. A 1970 `power_run` coach does not become an `air_raid` coach. `reputation` moves with season results, playoff results, awards, and firings.

| Field | Meaning | Type | Range | Layer | Visibility | Effect | Generated | Changes | Owner sees |
|-------|---------|------|-------|-------|------------|--------|-----------|---------|------------|
| `universe_key` | Permanent identity | string | `c-…` | Shared | Hidden | Both | HMAC | Never | Exact internally |
| `name` | Display name | string | ≤ 255 | Shared | Visible | Display | Name lists | Never | Exact |
| `birth_year` | Year of birth | smallint | head coaches about 32–65 in 1970; coordinators may be younger | Shared | Visible | Both | Generator | Never | Exact |
| `debut_year` | First year he can be hired | smallint | 1970, or 1971–2029 | Shared | Visible | Both | Generator | Never | Exact |
| `former_role` | Path into this career | enum | `player` `college_coach` `coordinator` `position_coach` | Shared | Visible | Display | Generator | Never | Exact |
| `specialty` | Background and orientation. It limits the coordinator chair and does not restrict head coach. | enum | `offense` `defense` `both` | Shared | Visible | Both | Generator | Never, including after promotion to head coach | Exact |
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

Coach posts before debut use the GM post shape with `role` in `head_coach`, `offensive_coordinator`, `defensive_coordinator`, `position_coach`, `college_head_coach`. They are shared, visible, exact, and display only. A Federation franchise post ends before the first playable season. A college or external post may run through the year before debut. Timeline stints use role `head_coach`, `offensive_coordinator`, `defensive_coordinator`, or `position_coach`. Season record, playoff appearances, and championships during a stint are derived from timeline standings. Firings, awards, and reputation after the person is hireable are timeline facts. The shared post never changes after publication.

### What this audit added

| Addition | Why |
|----------|-----|
| `consistency`, `adaptability` | Game-to-game variance and scheme changes were not in the 27-component list. |
| `secondary_positions` | Position flexibility had no field. |
| `kick_power`, `kick_accuracy`, `punt_power`, `punt_accuracy` | One `kicking` number and one `punting` number could not separate distance from accuracy. |
| `return_vision`, `special_teams` | Return decisions and coverage play are not the same thing as speed, tackling, or awareness. Deriving them would tie those profiles together. |
| `cap_management`, `patience` | Trade negotiation was doing two jobs, and patience existed only as a trait some GMs never drew. |
| `coach_evaluation`, `veteran_preference`, `pick_trade_willingness`, `positional_priority`, `reputation` on the GM | The prior GM list could not separate coach hiring, youth versus veterans, pick trading, positional bias, or a reputation that moves with results. |
| `preparation`, `fatigue_management`, `injury_management`, `fourth_down_aggression`, `adaptability`, `leadership`, `veteran_preference`, `reputation` on the coach | Clock management was present, but fourth down, weekly preparation, fatigue, injury return, season-to-season adaptability, and locker-room leadership were missing or collapsed into `motivation`. |
| `specialty`, coach `former_role` | Required once coordinators share the coach table. |
| `ended_by` on shared posts, and `promoted` on timeline stints | Firings and promotions need a recorded ending. |
| `TimelineScoutReport` and `TimelineScoutComponent` | Original ratings, current ratings, and the owner's estimate are three different facts. |
| `slot_order` and the explicit 26-, 28-, 30-, 31-, and 32-team assignments | Realignment has to be identical in every timeline without inventing cities. |
| `Timeline.start_season_year` | The normal game begins in 1970. A later-start mode, if approved, keeps its rules in later-phase timeline-start configuration. It is not `owner_selectable`. |
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

The component count is 33. An earlier pass used 31, and the draft before that used 27. Overall remains a derived display grade. The simulator reads the components.

---


## Population and row counts

The playable clock is 60 seasons, 1970 through 2029 inclusive. `N(year)` is exact:

| Seasons | Years | N(year) | Franchise-seasons |
|---------|-------|---------|-------------------|
| 1970–1975 | 6 | 26 | 156 |
| 1976–1994 | 19 | 28 | 532 |
| 1995–1998 | 4 | 30 | 120 |
| 1999–2001 | 3 | 31 | 93 |
| 2002–2029 | 28 | 32 | 896 |
| **Total** | **60** | | **1,797** |

Regular-season games use the same lengths as the era profiles: 14 through 1977, 16 through 2020, and 17 from 2021. Games in a season are `N(year) × length / 2`. `31 × 16` is even, so 1999–2001 can be scheduled at 16 games even though the conferences are 16 and 15.

| Seasons | Years | N | Length | Games |
|---------|-------|---|--------|-------|
| 1970–1975 | 6 | 26 | 14 | 1,092 |
| 1976–1977 | 2 | 28 | 14 | 392 |
| 1978–1994 | 17 | 28 | 16 | 3,808 |
| 1995–1998 | 4 | 30 | 16 | 960 |
| 1999–2001 | 3 | 31 | 16 | 744 |
| 2002–2020 | 19 | 32 | 16 | 4,864 |
| 2021–2029 | 9 | 32 | 17 | 2,448 |
| **Total** | **60** | | | **14,308** |

Playoff games are not in the 14,308. Their count still depends on byes and the field, which this document does not turn into a row total.

`slots(year)` and `rounds(year)` are still the era defaults (40+8 and 17 rounds in 1970–1977, then the era table). Undrafted prospects are 2 per active franchise in every year. The old 1996 multiplier of 3 is removed. Opening free agents are 5 per founding franchise. Those defaults are not locked. Every player total below is exact only while they hold. Franchise-seasons and regular-season games do not depend on them.

### Exact from the schedule

| Fact | Value |
|------|--------|
| Founding franchises | 26 |
| 1976 expansion franchises | 2 |
| 1995 expansion franchises | 2 |
| 1999 expansion franchises | 1 |
| 2002 expansion franchises | 1 |
| Active clubs, 2002–2029 | 32 |
| Franchise-seasons, 1970–2029 | 1,797 |
| Regular-season games, 1970–2029 | 14,308 |
| Shared people created by an expansion draft | 0 |

### Totals that use the era defaults

| Slice | Why it can still move | Rows if defaults hold |
|-------|----------------------|------------------------|
| 1970 opening rosters | 40 active + 8 taxi | 1,248 |
| 1970 free agents | 5 per founding club | 130 |
| Opening cohort | sum of the two rows above | 1,378 |
| Draftable prospects | round counts × N(year) | 16,183 |
| Undrafted prospects | 2 per active club in every year | 3,594 |
| Draft prospects | draftable + undrafted | 19,777 |
| Playable player identities | 1,378 + 19,777 | **21,155** |
| Archive players | 5-year mean inside 12,480 historical lines | 1,118 |
| All player identities | 21,155 + 1,118 | **22,273** |
| Shared pro lines, 1960–1969 | 26 × 48 × 10, before expansion | 12,480 |
| College season lines | 4 per identity | 89,092 |
| Head coaches | floor of 8 + E(year) | 578 |
| GMs | floor of 6 + E(year) | 460 |
| GM + head-coach identities | | **1,038** |
| Coordinators, if generated now | floor in the coordinator section | 564 |
| All staff identities with coordinators | 1,038 + 564 | **1,602** |

Roster-slot seasons, using the era roster defaults and `N(year)`:

| Years | N | Slots per team | Slot-seasons |
|-------|---|----------------|--------------|
| 1970–1975 | 26 | 48 | 7,488 |
| 1976–1977 | 28 | 48 | 2,688 |
| 1978–1986 | 28 | 53 | 13,356 |
| 1987–1994 | 28 | 55 | 12,320 |
| 1995–1998 | 30 | 61 | 7,320 |
| 1999–2001 | 31 | 61 | 5,673 |
| 2002–2029 | 32 | 61 | 54,656 |
| **Total** | | | **103,501** |

The 103,501 figure is maximum roster capacity. It counts every active slot and every taxi slot in every franchise-season. It is not a count of guaranteed occupied rows. Taxi squads are optional. If a season-stat row is created only for a player who actually occupies a slot, the finished total depends on how many taxi positions that timeline fills. Expansion clubs in particular may leave taxi empty. The capacity arithmetic itself does not change.

| Timeline store on one finished run | Depends on | Rows if defaults hold |
|------------------------------------|------------|------------------------|
| Player overlays after 2029 | class sizes | 21,155 |
| Player overlays before the 1970 season, after that draft | 1970 class of 494 | 1,872 |
| Season lines, one per occupied roster slot | at most the 103,501 capacity | 103,501 only if every taxi slot is filled |
| Draft picks | draftable seats | 16,183 |
| Injuries | 30 per franchise-season | 53,910 |
| Contracts | one per two occupied slot-seasons | not an integer at full capacity; 103,501 is odd, and this document does not choose how to count the leftover slot |
| Staff stints | 4-year mean tenure. GM and head coach only are 2 roles. Seating coordinators as well is 4 roles. | 1,797 / 4 is 449.25 per role, so the mean no longer yields an integer; the mean is unchanged |
| Awards | a fixed annual honor list, not N(year) | about 3,500 |
| Expansion-draft picks | 16 per new club in 1976; 26 per new club in 1995, 1999, and 2002 | 32 + 52 + 26 + 26 = 136 |

Changing the 1978 active roster from 45 changes the 1978–1986 slot seasons only. Changing the club counts in this section is a new schedule. Splitting the old 15 position labels into 19 positions does not change these row counts, because the active and taxi limits are unchanged. Adding `return_vision` and `special_teams` adds two rating columns per player, not two people. Removing the 1996 undrafted multiplier of 3 changes the undrafted class and does not change franchise-seasons, regular-season games, or the 103,501 capacity figure. The 21,155 playable identities and the 1,797 franchise-seasons stay as stated while the roster, round, and undrafted-multiplier figures hold.

### Obsolete 32-team estimate

An earlier draft reported **28,384** universe player rows for 32 teams in every year and drafts from 1971 through 2049 (1,536 roster slots + 160 free agents + 26,688 draft prospects). That number is retired. A later no-expansion sketch of **17,424** playable players assumed 24 teams in every season through 2029. That sketch is also retired. An intermediate confirmed schedule of 24, then 28, then 32, with franchise-seasons of 1,792 and regular-season games of 14,280, is also retired by the 26-club founding schedule in this section. **21,155** playable players is the total only while the era roster, round, and undrafted-multiplier defaults hold. An earlier expansion sketch that required veteran free agents in 1996 is retired. The 136 veteran selections in this section are 32 in 1976, 52 in 1995, 26 in 1999, and 26 in 2002. They are not that retired sketch.

---

## Invariants

A future implementation can turn these into tests. This document does not add them.

1. `start_timeline` does not change universe player, GM, or coach counts.
2. Two new timelines on one release have the same player keys, the same GM keys, the same coach keys, the same pre-1970 stat rows, the same 1970 pre-draft `(player, franchise, role)` tuples, the same free-agent pool, and the same 1970 prospect keys.
3. Those timelines have no 1970–2029 stat rows and no draft picks until the owner finishes the opening decisions and the season runs.
4. The 1970 draft class is the first class materialized, and that materialization is a later draft transaction. `start_timeline` does not materialize it. Materializing year Y creates no overlay whose class year is greater than Y.
5. A pick, stint, injury, contract, rating change, award, or 1970-or-later stat written on timeline A is invisible to timeline B and does not update universe tables.
6. Timeline `season_year` values lie in 1970–2029. Shared pro seasons lie in 1960–1969.
7. `season_year = 1969 + season_index`, and a complete timeline has `season_index` 60 and `season_year` 2029.
8. Each of the 100 initial GMs has a distinct strength/weakness/trait/history signature, and the same holds for the 100 initial head coaches.
9. Opening replacement hires are members of the unemployed 74. The other 25 franchises still employ their shared GM and head coach when the 1970 draft starts, unless a later rule explicitly allows poaching.
10. Campaign-style code does not insert `League`, `Conference`, `Division`, or `Team` rows and does not update `Team.league`.
11. A published release rejects edits to an existing `universe_key` and rejects edits to a published expansion schedule. A schedule change is a new release.
12. Two timelines on one release have the same `N(year)`, the same expansion franchises, and the same draft-class keys for every year from 1970 through 2029.
13. An expansion draft inserts timeline picks only. It does not change the universe player count.
14. Every timeline on a release has the same conference slug, division slug, and `slot_order` for each franchise-season, matching the 26-, 28-, 30-, 31-, and 32-team tables in this document.
15. A player's `universe_key`, name, birth, college, body, position, `initial` ratings, `potential`, traits, and pre-1970 history are identical on every timeline. `current` ratings and 1970–2029 results are not.
16. Every active quota sums to that profile's active limit, and every taxi quota sums to that profile's taxi limit. The position enum has 19 values.
17. Random draws are a pure function of the release seed, season, week, franchise keys, game id, event index, and event type. They do not include the timeline id or the owner's decisions. The same pre-snap state consumes those draws to the same snap outcomes. A draw can be recomputed without that state. A snap outcome cannot, unless the kickoff inputs or a checkpoint were stored. The simulator does not read the scout report.

---

## Fit with the current project

- External ids stay natural keys: `universe_key`, college slug, existing team slug.
- Generation is a management command. The 32-team admin view is the right pattern for a few dozen catalog rows and the wrong pattern for a full player release. If the era defaults hold, one release is 22,273 player identities and, with coordinators included, 1,602 staff identities.
- `League.clean()` and the single active tier-1 rule stay. Timelines reference that tier-1 league and do not become leagues.
- No new Django setting is required. The generator seed lives on the release. Production settings stay out of this work.
- The owner FK should use `settings.AUTH_USER_MODEL` when the model is eventually added. This design does not set `AUTH_USER_MODEL`.
- The live generator's `TEAM_COUNT = 32` is neither the 1970 founding size nor a legal stand-in for `N(year)`. A release cannot be fingerprinted against that tree. Changing the generator is a later code change, not part of this document. `fg-founding-1` remains the 24-club founding generator and is not edited by this schedule. The replacement that writes the 26-club catalog must not hard-code 26 for every season.

Suggested order when implementation is allowed:

1. Represent founding and expansion franchises without a fixed team-count constant (separate work). Then generate and publish one universe release: the 32 placeholder franchises, the alignment in this document, the opening cohort, the archive, 1970–2029 classes sized by `N(year)`, staff classes including coordinators, and the 1976, 1995, 1999, and 2002 expansion events with their numeric rules filled in. That generator is not `fg-founding-1`.
2. `start_timeline` is implemented through phase `direction`: one human, one founding franchise, the 1970 season, the default owner directive, and the opening player overlay. The 1970 draft is a later transaction.
3. Season clock that materializes the next class and the next staff class each offseason, stopping after 2029.
4. Development, injuries, contracts, and awards, writing timeline rows only.

Scoring, schedules, payments, and public pages stay outside this design. Season lines are the durable stat record so a later box score can aggregate into them.

---

## Alternatives considered

**Keep the previous draft's 32-team, 1971-first, through-2049 population.** That is the retired 28,384-row model. It does not match a 26-franchise opening, a 1970 rookie draft, or a 60-season run.

**Put 1970 rookies on the opening rosters and skip a preseason draft.** That makes every timeline's 1970 roster identical through week 1, including rookies. The opening sequence now includes the draft as an owner decision, so rookies stay in the shared class until that timeline drafts them.

**Generate replacement coaches inside each timeline.** Sixty seasons outlive the 1970 staff. Per-timeline identities would make two owners hire different fictional people. Incoming classes are shared instead, and the hiring choice is the timeline's.

**Store pre-1970 statistics on each timeline as a copied record book.** The lines would be identical forever. They stay on the universe, and the career page reads both stores.

**Treat 26 as the league size in every season from 1970 through 2029.** Rejected. Two clubs enter in 1976, two enter in 1995, one enters in 1999, and one enters in 2002. Class size, roster-slot seasons, and incoming staff use that `N(year)`. The expansion draft redistributes players already in the timeline and does not mint a second player pool.

---

## Unresolved decisions

These structural choices are accepted and are not waiting on another approval before the model proposal:

- 19 primary positions, with special-teams assignments as weekly roster roles.
- 33 player components. Overall is display only.
- Rosters of 40, then 45, then 47, then 53 active players, each with taxi capacity of 8. Taxi slots are optional. An expansion club must fill the active roster and does not have to fill taxi.
- Controlled, transitional, and modern player-market eras. Unrestricted free agency and a salary cap begin in 1995.
- Deterministic scouting, and the stable draw coordinates in [Deterministic scouting and results](#deterministic-scouting-and-results). The timeline id and the owner decision fingerprint are not seed inputs.
- The seven-step owner offseason.
- Offensive and defensive coordinators generated with the shared universe, on the coach person.
- Shared people and pre-timeline histories identical in every timeline.
- Expansion from 26 to 28 in 1976, to 30 in 1995, to 31 in 1999, and to 32 in 2002. No further expansion, departure, or relocation through 2029 unless a later release adds one.
- The expansion arithmetic in [Recommended expansion draft](#recommended-expansion-draft): 32 veteran selections in 1976, 52 in 1995, and 26 in each of 1999 and 2002. Active rosters are filled from shared veterans, drafted rookies, and reserved undrafted prospects. The undrafted multiplier is 2 in every year. Free agents are optional.
- The playoff fields in the era table: 8 teams through 1998, then 12 teams from 1999, with conference-2 using 3 winners and 3 wild cards only in 1999–2001.

Numeric simulation coefficients, salary-cap amounts, the minimum salary, rookie-slot percents, and the other values below stay configurable. `EraRuleProfile` and the expansion-event row can store a later number without a new person table. An empty cap on a 1995-or-later profile still blocks publication of that profile. It does not block the model proposal.

The playable horizon is 60 seasons, 1970 through 2029 inclusive.

| Decision | Working value | What is still open |
|----------|----------------|--------------------|
| Cap total and minimum salary | Ledger units. Cap turns on in 1995. No amount is chosen. | The column exists. A 1995 profile cannot be published with an empty cap. |
| Rookie slot percents | 4-year deals from 1995, ordered by pick. | The percent table is empty until configured. |
| Accrued-season threshold | 4 seasons. | Stored on the profile. |
| Future-pick window and trade-deadline week | 1, then 2, then 3 years. Deadline after week 8. | Trades exist in every era. The window is a profile field. |
| Injured reserve | 2, then 3, then 4, with the return rules in the market table. | Profile fields. |
| Overtime years | Ties through 1973. Sudden death from 1974. Both-teams possession from 2012. | Profile fields. The playoff field itself is specified above. |
| Franchise-tag formula | Average of the top five charges at the position, one tag per club, from 1995. | Profile field. It does not set the cap. |
| Special-teams score weights, development coefficients, and other sim coefficients | The formulas are written. The numbers are recommendations. | They do not add person columns. |
| How far `talent_evaluation` tightens a scout band | Better evaluators get narrower bands. The noise draw does not change. | The slope is unset. |
| Free-agent pool and undrafted pool | 5 free agents per founding franchise in 1970. Undrafted prospects are 2 per active franchise in every year. | The removed 1996 multiplier of 3 is not part of this release. Free agents are not part of the expansion minimum. Changing a multiplier requires the active-roster arithmetic to be recomputed before publish. |
| Rating draw distributions | Era tables say which traits are more common. | Required before generation. Not a new column. |
| Archive mean career | 5 years, implying 1,118 archive players. | The 12,480 historical lines stay if the 48-man pre-1970 roster stays. The 5-year mean is still an assumption. |
| College count | About 48–80 schools, four seasons stored. | Generation choice. |
| Names, cities, and permanent slugs | Placeholders only. | Not required to describe the model. The catalog link stays empty until a later release names them. |
| Multi-human expansion award | HMAC fair draw is the candidate. Single-player apply is automatic. | The method is a product choice. The choice row can store it. |
| A mode that starts in 1976, 1995, 1999, or 2002 | Not part of Phase 1. The normal game begins in 1970. | Product choice. Eligibility belongs in later-phase timeline-start configuration, not `owner_selectable`. |
| Order among playoff qualifiers | Division winners take the seeds ahead of wild cards. The top two seeds bye. The first round is seed 3 against seed 6 and seed 4 against seed 5. | The order among division winners, and the order among wild cards, is not chosen. Tiebreakers are not chosen. |

### Not a reason to add a second staff table

Coordinator identities share the coach table. Specialty limits the coordinator chair only. Any specialty may become a head coach, and promotion does not change specialty. Scheme ratings control ability. A coordinator's later promotions and demotions are new stints on the same person.

---

## Document status

- Describes `leagues` as it exists on `main`, including the 32-team generator. This design does not change that generator.
- Confirms 19 primary positions and treats returner, snapper, coverage, and emergency kicker or punter as roster roles.
- Confirms 33 rating components, including `return_vision` and `special_teams`.
- Confirms the shared schedule: 26 franchises for 1970–1975, 28 for 1976–1994, 30 for 1995–1998, 31 for 1999–2001, and 32 for 2002–2029. Names and cities stay placeholders. No further expansion, departure, or relocation is scheduled through 2029.
- Confirms the playable timeline as 60 seasons, 1970 through 2029 inclusive. Franchise-seasons are exactly 1,797. Regular-season games are exactly 14,308 while the 14-, 16-, and 17-game lengths hold.
- If the era roster, round, and undrafted-multiplier figures hold: 21,155 playable player identities, 22,273 including the archive assumption, 16,183 draftable prospects, 3,594 undrafted prospects, and 103,501 roster-slot seasons as maximum capacity, plus 1,038 GM and head-coach identities and 1,602 staff identities with coordinators. The undrafted multiplier is 2 in every year. Occupied taxi rows can be lower than capacity.
- Sets the expansion drafts at 32 veteran selections in 1976, 52 in 1995, 26 in 1999, and 26 in 2002, 136 together. Each new club reaches 40 active players in 1976 and 53 in 1995, 1999, and 2002 from veterans, drafted rookies, and reserved undrafted prospects. Taxi is optional. Publish does not count timeline free agents.
- `fg-founding-1` remains the unchanged 24-club generator. A later generator version creates the 26-club founding catalog, the 1970 memberships, and the 26 opening seats. This document does not change that code.
- Sets deterministic draws from the universe-release seed, season, week, franchise keys, game id, event index, and event type. The timeline id and the owner decision fingerprint are not inputs. Those coordinates reproduce the draw. A snap outcome also needs the stored kickoff state or a replay from a checkpoint.
- The concrete table layout is in [docs/PLAYER_UNIVERSE_MODEL_PROPOSAL.md](PLAYER_UNIVERSE_MODEL_PROPOSAL.md). That file proposes models only. It does not add them. The five Phase 1 models are ready to implement. This document does not implement them.
- The retired totals of 28,384 and 17,424 stay labeled obsolete. An earlier 1996 rule that required two veteran free agents per new club is also obsolete.
- Adds no application code, database models, migrations, fixtures, settings, generated people, or database rows.
