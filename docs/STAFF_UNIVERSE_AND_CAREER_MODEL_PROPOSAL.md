# Federation Gridiron — Staff Universe and Career Model Proposal

This document proposes the campaign career layer for coaches and general managers. It is a design review. It does not add models, migrations, services, admin, APIs, pages, forms, or database rows.

The playable horizon remains 60 seasons, 1970 through 2029 inclusive, on a published `UniverseRelease`. Many independent timelines share that release. Staff follow the same boundary as players: identity and reusable starting information belong to the universe; employment, development, decisions, achievements, and career history that can differ by campaign belong to the timeline.

This pull request does not change the merged universe tables. A thin shared identity, described under [Coach-to-GM careers and shared identity](#coach-to-gm-careers-and-shared-identity), has to land in a new universe migration before any timeline staff foreign key is created. That migration is a later change. It does not edit `universe/migrations/0002_shared_people.py` or `0003_shared_rules_and_opening.py`.

The unimplemented staff sketches in [PLAYER_UNIVERSE_MODEL_PROPOSAL.md](PLAYER_UNIVERSE_MODEL_PROPOSAL.md) (`GMCurrentRating`, `CoachCurrentRating`, a single mutable reputation, `StaffStint` with a position-coach chair, and scout-component tables bundled into the old phase 5) are not the authority. The authority for shape is the merged player pattern: one overlay, one season ability row, immutable employment history, and ordered development events. Player contracts, transactions, game simulation, schedules, standings, and scouting reports stay out of this phase except where a later service must read staff.

## Existing schema findings

Shared staff already exist. Timeline employment does not. A common person identity does not.

| Already merged | What it stores | What it does not store |
|---|---|---|
| `UniverseGM`, `GMRatingProfile` | Stable key, name, birth year, debut year, former role, two positional priorities, strength and weakness tags, two traits, 15 initial and potential abilities, initial reputation | Any campaign's current ability, job, or retirement. A link to a coach |
| `UniverseCoach`, `CoachRatingProfile` | The same identity facts, specialty, preferred offense and defense, coach tags and traits, 17 initial and potential abilities, initial reputation | A campaign scheme, a chair, a season-by-season ability, or a link to a GM |
| `UniverseStaffPost` | Pre-debut jobs only. The post must end no later than the year before `debut_year`. A Federation franchise post must end before the release's first playable season. Exactly one of `gm` or `coach` | Any 1970–2029 employment. Those years are campaign history |
| `UniverseAward` | Archive awards, including `coach_of_year` and `executive_of_year`, in the years before the first playable season. Exactly one of player, GM, or coach | Awards earned inside a timeline |
| `OpeningStaffAssignment` | The shared 1970 template: one `gm`, `head_coach`, `offensive_coordinator`, and `defensive_coordinator` per founding franchise. Exactly one of `gm` or `coach` | The copy a timeline keeps, and any rule that the human owner must employ those people |
| `ExpansionEvent.gm_hiring_rule`, `hc_hiring_rule` | Prose the publication can require to be non-empty | A parsed hiring algorithm |
| `OwnerDirective`, `TimelineParticipant`, `TimelineFranchiseControl` | The human owner's broad instructions and which franchise that human controls | A staff person, a vacancy, or an AI decision |
| `EraRuleProfile` on `TimelineSeason` | Roster, draft, contract, and market rules for that year, plus `style_key` | A list of legal offense or defense schemes, staff chairs, or a retirement age |
| Player lifecycle | `coaching` as a label on a player development event | A foreign key to a coach |

`UniverseGM` and `UniverseCoach` are different tables. Each `universe_key` is unique inside its own table. The generator prefixes (`g-` and `c-`) keep the published keys from colliding, and the database does not enforce one key space across both tables. `name` is not unique. There is no shared person table, no shared primary key, and no foreign key from a coach row to a GM row or to `UniversePlayer`. Matching on name is not identity.

`UniverseStaffPost` and `OpeningStaffAssignment` each require exactly one of `gm` or `coach`. `UniverseAward` requires exactly one of player, GM, or coach. `OpeningStaffAssignment` also has a unique key on `gm` and a unique key on `coach`. MySQL treats `NULL` as distinct, so those unique keys do not collapse the null side; the check constraint is what keeps exactly one person. A GM or a coach can still have many pre-debut posts and many archive awards. `former_role` is a label on that profile. It does not point at another row.

Debut age is already checked: `debut_year` is at least `birth_year + 20` and at most `birth_year + 80`, and it lies inside the release. There is no stored age and no shared retirement year. Preferred `west_coast` is rejected unless the coach `debut_year` is 1987 or later. Preferred `air_raid` is rejected unless the coach `debut_year` is 2004 or later. Those checks sit on the shared coach. They describe the generated person. They are not era-profile columns, and they are not the rule for a scheme a timeline installs later.

Opening roles are `gm`, `head_coach`, `offensive_coordinator`, and `defensive_coordinator`. Specialty limits the coordinator chairs: offense or both for an offensive coordinator, defense or both for a defensive coordinator. Specialty does not stop a head-coach chair. `position_coach` exists on `UniverseStaffPost` and on `UniverseCoach.former_role`. It is not an opening role.

`EraRuleProfile.style_key` is one of `run-first`, `passing-rules`, `timing-specialists`, `cap-and-size`, `spread-influence`, and `modern-passing`. It is not an allow-list of `power_run`, `west_coast`, `air_raid`, or the other scheme enums.

`OwnerDirective` is one row per participant per season. Bot franchises have no participant and no directive. A locked directive cannot change. Deleting a user sets `Timeline.created_by` and `TimelineParticipant.user` to null and does not delete the campaign. A participant whose user is null is no longer a playable human; that franchise is treated as bot-controlled. No recovery service exists.

No timeline model names a GM or a coach. Creating a timeline does not copy staff. The player services do not read staff.

## Architectural boundary

| Layer | Owns | Who writes it |
|---|---|---|
| Universe | The person, each track's starting ability vector, tags, traits, preferred schemes, specialty, debut year, and pre-debut posts and awards | The generator, once, while the release is draft. Published rows stay immutable |
| Timeline | The overlay, season abilities, employment, development events, honors, and retirement inside one campaign | Timeline staff services. They reference universe rows and never update them |
| Owner | Broad instructions for the franchise that participant controls. On that club the participant also performs the GM and head-coach functions until a later delegation says otherwise | The existing directive path. It does not create a person and does not edit abilities |

A second timeline does not copy `UniverseGM` or `UniverseCoach`. It inserts overlay rows that point at the shared identity. Two campaigns can employ the same person at different clubs, develop different abilities, and retire on different dates.

Cross-timeline comparison joins on the identity `universe_key`. It does not join on a timeline employment id. After the identity migration, that key is the canonical public key. The backfill copies today's profile key onto the identity, so a person who still has one profile keeps the key the generator already published.

## Recommended shared-universe entities

Keep the merged profile tables. Do not merge the 15 GM abilities with the 17 coach abilities. Do not add a staff-class table or new ability columns.

One new shared entity is required before timeline staff models exist. This pull request does not create it.

| Entity | Role in this design |
|---|---|
| `UniverseStaffIdentity` | The person. Required before `TimelineStaff`. Release, canonical `universe_key`, name, and birth year. Not created in this pull request |
| `UniverseGM` and `GMRatingProfile` | The GM track: debut year, labels, and the reusable GM vector. At most one per identity |
| `UniverseCoach` and `CoachRatingProfile` | The coach track: debut year, specialty, preferred schemes, labels, and the reusable coach vector. At most one per identity |
| `UniverseStaffPost` | Pre-debut biography only. Still points at the profile |
| `UniverseAward` | Archive coach and executive awards only. Still points at the profile |
| `OpeningStaffAssignment` | The 1970 founding-club template. Still points at the profile. Initialization resolves the profile to its identity |
| Era scheme allow-list | Child rows of `EraRuleProfile`: which offense and defense schemes a season in that era may install. Required before `hire_staff` and `advance_staff_season`. Not created in this pull request |

`debut_year` stays on each profile and groups later candidates. A coaching debut and a GM debut on the same identity may differ. Publication checks on counts and signature uniqueness stay in the generator. They are not new tables.

## Coach-to-GM careers and shared identity

### What the schema can represent today

The current shared-person schema cannot represent one human who moves from coaching into a GM career.

`UniverseGM` and `UniverseCoach` each own `universe_key`, `name`, and `birth_year`. Nothing links a row in one table to a row in the other. The foreign keys that name a staff member name exactly one profile:

- `UniverseStaffPost.gm` or `UniverseStaffPost.coach`
- `OpeningStaffAssignment.gm` or `OpeningStaffAssignment.coach`
- `UniverseAward.gm` or `UniverseAward.coach`
- `GMRatingProfile.gm` and `CoachRatingProfile.coach`, each one-to-one

`former_role` does not close that gap. A coach whose former role is `college_coach`, and a GM whose former role is `college_coach`, are two rows. A GM whose former role is `player` is not a foreign key to `UniversePlayer`.

### Separation is a schema limit, not a game rule

The overview's separate pools are generation counts: 100 initial GMs, 100 initial head coaches, and coordinators in addition to that head-coach pool. Those counts do not say that one human is forbidden from changing families. Across 60 seasons, a coach later becoming a personnel executive or a GM is a normal career, and an executive can later take a head-coach chair. Prohibiting those paths because the current tables are separate would freeze an implementation accident into the game.

This design does not treat permanent separation as an intentional rule.

### What stays separate

The ability tables stay separate. A GM vector and a coach vector are different lists, with different potentials. One wide row would store empty abilities for every person who has only one track. `UniversePlayer` stays a third family. "Former player" is already a label. Unifying players with staff is a larger refactor, and it is not required to let a coach become a GM.

### Smallest future-compatible design

Add `UniverseStaffIdentity` in a new universe migration. Do not edit the merged `0002` or `0003` files.

| Field | Rule |
|---|---|
| `release` | The same release as both profiles. `CASCADE` with the release, matching the other shared people |
| `universe_key` | Unique on this table. The canonical public key. Timelines never allocate it |
| `name` | Not unique. The display name of the person |
| `birth_year` | The age basis. Age in a season is `season_year - birth_year` |

Add a required foreign key from `UniverseGM` and from `UniverseCoach` to that identity. Each foreign key is unique, so one identity has at most one GM profile and at most one coach profile. Both may be present. `debut_year`, specialty, schemes, tags, traits, and the rating profile stay on the track that owns them.

The migration backfills one identity per existing GM row and one identity per existing coach row. It copies that row's `universe_key`, `name`, and `birth_year`. It does not merge any existing rows, including rows that share a display name. Nothing in the current data says that two rows are the same person. If two profile keys are already equal, the unique identity key fails the migration. That failure does not combine the people. The backfill is the same if an environment has already applied migration `0002` and already holds rows: one identity per existing profile. The migration must not assume the tables are empty, and it must not invent a second profile.

Keep `universe_key`, `name`, and `birth_year` on the profile tables in that migration. `OpeningStaffAssignment`, `UniverseStaffPost`, `UniverseAward`, and the rating-profile foreign keys continue to point at the profile, so those tables do not need a rewrite. An application check requires the profile's name and birth year to match the identity. While the identity has only one profile, the profile `universe_key` matches the identity `universe_key`. Debut-age checks use the identity birth year, and the copied profile birth year has to agree. A later second profile is a new profile row. It receives its own `universe_key` from the generator, and it copies the identity's name and birth year. The identity key does not change. That publication step does not take an existing GM or coach row off the identity the backfill already gave it, and it does not choose the person by matching names.

### Why this has to happen before timeline models

`TimelineStaff` foreign-keys to `UniverseStaffIdentity` with `PROTECT`. It does not store `gm` XOR `coach`.

Shipping the overlay against the exclusive profile keys would copy that split into every campaign. Season ratings, employment, development events, and honors all hang off the overlay. Moving those rows onto a shared identity later is a migration of every staff table in every timeline. Adding the identity first is a backfill of the shared profiles only, and the timeline tables are born pointing at the person.

### What the first career services may hire

`hire_staff` and `reassign_staff` accept a chair only when that identity already has the shared profile for the chair. A timeline must not insert `UniverseGM` or `UniverseCoach`. Published universe rows stay immutable.

The first GM season rating copies that GM profile's `initial_*` and `initial_reputation`. The first coach season rating copies the coach profile the same way. A later season of the same track copies the immediately previous completed rating of that track. A track with no profile has no rating rows and cannot be hired.

This phase does not generate a latent second profile for every person. Which identities, if any, receive a coaching profile and a GM profile is a later publication choice. The schema no longer forbids both. Until a generator attaches the second profile, a coach cannot be hired as a GM, because there is no legal GM vector to copy, and the reverse hire is refused for the same reason.

## Shared staff identity

Within one identity, the playable chairs follow the profiles that identity has:

- The GM chair requires the GM profile. Pre-debut posts may be `personnel_director`, `scout`, or `college_coach`. Those posts stay on `UniverseStaffPost`. A timeline does not employ a GM as a scout.
- The coach profile is one person for `head_coach`, `offensive_coordinator`, and `defensive_coordinator`. Promotion, demotion, and a later return to a coordinator chair are new timeline employment rows on the same overlay. They are not a new identity.
- Specialty is shared eligibility for a coordinator chair. It is not rewritten when the person becomes a head coach. Head coach remains legal for every specialty.
- `position_coach` stays a historical and former-role value. This proposal does not seat position coaches. See [Roles and career paths](#roles-and-career-paths).
- A second profile, once a publication attaches it, adds the other track on the same identity and the same timeline overlay. It does not create a second person.

| Fact | Where | Rule |
|---|---|---|
| Stable identity | `UniverseStaffIdentity.universe_key` | Unique. The backfill copies the existing profile key. Timelines never allocate a key |
| Display name | Identity `name` | Not unique. The UI disambiguates with role, birth year, and key |
| Age basis | Identity `birth_year` | Age in a season is `season_year - birth_year`. No age column. No mandatory retirement age |
| Entry | Profile `debut_year` | The first year a timeline may use that track. The identity is materialized in the earliest profile debut year the timeline has reached |
| Retirement boundary | None on the shared row | Retirement is a timeline fact. The same person can still be employed in another campaign |
| Starting ability | `initial_*`, `potential_*`, `initial_reputation` on the profile for that track | Copied into the first timeline season rating of that track. Never updated from a campaign |
| Background | former role, priorities, tags, traits, specialty, preferred schemes | Shared, immutable, and visible. They are not the season ability vector |

The overview's planning populations stay release data, not check constraints: 100 initial GMs and 100 initial head coaches; 24 employed in each of those chairs in 1970; coordinator identities in addition to that head-coach pool; incoming classes from 1971 through 2029 sized by the overview floors, with a larger class in an expansion year. Those figures count profiles. They do not become smaller until a future publication chooses to attach two profiles to one identity instead of minting two people. Publication may refuse a release whose generated counts or staff-signature uniqueness do not match what that release claims. The tables do not hardcode 100, 76, 24, 8, or 6.

No staff-class table is required. `debut_year` on the profile is the cohort key. `DraftClass` exists because a player class carries round counts and undrafted-pool arithmetic. A staff cohort does not.

## Roles and career paths

Playable chairs are the four opening roles. Offensive and defensive coordinator chairs are part of the durable schema now. Employing a coordinator is optional in the first playable version. Position coaches remain shared historical background and do not become timeline employment chairs.

| Chair | Required profile | Eligibility |
|---|---|---|
| `gm` | GM profile | The identity has a GM profile. No specialty |
| `head_coach` | Coach profile | Any specialty |
| `offensive_coordinator` | Coach profile | Specialty `offense` or `both` |
| `defensive_coordinator` | Coach profile | Specialty `defense` or `both` |

Eligibility is both layers. The shared profile decides which chairs are legal. The timeline decides which of those chairs the person holds, and when. A chair whose profile is missing is not legal, however the caller labels the person.

`position_coach` does not justify a timeline chair. The owner never hires one. Coordinator and head-coach abilities already cover scheme, play calling, and player development. A seated position-coach pool would add a labor market the owner does not see and a large employment history the simulator does not need. Historical `UniverseStaffPost` rows may still use the role. Timeline employment rejects it. A later version can let coordinator abilities affect player development and simulation. This phase does not add that foreign key. The player development reason `coaching` stays a label.

Unemployed staff are not an employment row and not a role value. A materialized person with no open employment is available. That is the 1970 pool of unhired GMs, head coaches, and coordinators. It is also every later candidate before a hire, and every person between jobs.

Each employment row is independent. Firing or losing a head coach does not close either coordinator. The schema has no cascade from the head-coach row to the coordinator rows. A later staffing service may separately retain, fire, release, or replace those coordinators according to club and coach preferences. Retaining a coordinator leaves the open coordinator row in place.

Career changes close one employment and open another in the same transaction:

| Change | Close reason | Next row |
|---|---|---|
| Fire | `fired` | None, or a different person's hire in a separate call |
| Resign | `resigned` | None |
| Hire from the available pool | none on the first row | An open employment |
| Promotion | `promoted` | A new chair on the same person, usually `head_coach` |
| Demotion or a step to another chair | `reassigned` | The new chair on the same person |
| Club change in the same chair | `hired_elsewhere` | The same chair at the new franchise |
| Philosophy change without a new chair | `philosophy` | The same person, club, and chair, with a new installed scheme |
| Retire | `retired` | None. The first implementation does not reopen this row |
| Season continues in the same job | no close | The open row stays open. A new season does not invent a row |
| Leave the campaign's clock at the final season | `timeline_complete` | None. Written only when a later clock closes surviving jobs |

`opening_replace` is not a reason. Replacing an opening coach on an AI club is an ordinary fire plus hire. The shared `OpeningStaffAssignment` stays as it was.

A person has at most one open employment. A franchise has at most one open row per chair. Zero coordinators is legal in every phase of the first playable version, on AI clubs and on the participant's club.

GM and head-coach vacancies are legal in the schema. They are not a permanent row constraint.

- An AI-controlled club may leave either chair vacant during the offseason.
- Before an AI club begins regular-season simulation, it has a seated GM and a seated head coach. That check belongs to the later season clock. It is not a check constraint on the employment table.
- A participant-controlled club does not need a GM employee or a head-coach employee. The participant performs those functions. See [Human participant and AI staff](#human-participant-and-ai-staff).
- If the game later lets the participant delegate either function, the clock can require a real employee for that delegated chair only.

Resignation, firing, and retirement are records of a decision the caller already made. This design does not decide who quits, who is fired, or who retires. Later game logic chooses retirement from age, career state, ability, reputation, opportunity, and era. The schema stores the result. It does not store a mandatory retirement age.

## Abilities

Use the ability lists that are already on the rating profiles. They are enough for the decisions the game asks staff to make. Adding a field for every phrase in the request would duplicate tags, traits, and directive enums.

### General manager — 15 abilities plus reputation

| Gameplay need | Stored ability | Also used, and not a new number |
|---|---|---|
| Player evaluation and scouting | `talent_evaluation`, `scheme_fit` | Positional priorities and the strength tags |
| Drafting | `draft_strategy` | `risk_tolerance`, `patience` |
| Trading | `pick_trade_willingness` | `risk_tolerance`. `negotiation` is trade negotiation |
| Free agency | `free_agency` | |
| Contracts and roster management | `cap_management`, `roster_construction` | `cap_management` is length, guarantees, and cap fit. The cap rule itself stays on the era |
| Player development | `player_development` | |
| Leadership, discipline, adaptability, staff | `leadership`, `discipline`, `patience`, `coach_evaluation` | Traits such as `adaptable`, `stubborn`, `delegator`, `gambler` are labels, not sliders |
| Veteran or youth lean | `veteran_preference` | The owner's `veteran_preference` directive can override the lean for a club that has an employed GM. It does not rewrite this number |

`initial_reputation` has no potential. A season can still move reputation. Its ceiling is 100, not a personal potential.

### Coach — 17 abilities plus reputation

| Gameplay need | Stored ability | Also used, and not a new number |
|---|---|---|
| Offensive and defensive scheme knowledge | `offensive_scheme`, `defensive_scheme` | `preferred_offense` and `preferred_defense` are the person's origin, not the installed scheme |
| Play calling and game management | `game_management`, `clock_management`, `fourth_down_aggression`, `aggressiveness`, `in_game_adjustments` | The owner's fourth-down directive biases the owned club. It does not replace the rating |
| Player development | `player_development` | A later simulation may read the coordinator's season rating. This phase does not add a player-event foreign key |
| Leadership, discipline, adaptability | `leadership`, `discipline`, `motivation`, `adaptability` | Traits remain labels |
| Staff management | `staff_management` | Available to a later service when a head coach's tendencies affect coordinators. It does not hire or fire by itself |
| Preparation and availability | `preparation`, `fatigue_management`, `injury_management` | |
| Veteran or youth lean | `veteran_preference` | The playing-time directive can override it for the owned club when a head coach is employed |

No scouting vector is added for coaches. No position-coach vector is added. Tags such as `develops_quarterbacks` or `halftime_adjustments` remain explanations of the numbers, not extra columns.

### What is true, and what a human may know

True ability values stay internal game state. The season ability row is that vector. The simulator, when it exists, reads that row.

The owner initially sees reputation, career history, achievements, schemes, traits, and broad ability grades or ranges. Reputation is the stored season reputation, not a second number. Grades and ranges are a later presentation function of the true vector. Exact component visibility may improve later through scouting or familiarity. This phase does not add a second visible-rating vector, `TimelineStaffScoutReport`, or a stored projection. Presentation and scouting projections are later work.

Bot decisions may read the true vector for every employed staff member. The owner's instructions are a separate input. They are not a second ability vector.

### Development, decline, and past seasons

Development and decline formulas stay outside the schema. The ordered development events and the resulting season rating are the audit path.

- The first season rating for a track copies that profile's `initial_*` and `initial_reputation`.
- A later season of the same track copies that overlay's immediately previous completed rating for that track. It does not reread `initial_*`. A missing previous-year rating for a track that already has one fails. A track whose profile is first usable in the new season has no previous rating and does not need one.
- Each event stores the track (`gm` or `coach`), the raw delta, from −20 through 20, and a reason. The reason set is `age`, `role`, `performance`, `experience`, `mentorship`, `workload`, and `era`. The reason records why the caller moved the number. It does not compute the number. Later logic may use those same inputs to choose the delta. The schema does not. The track is required because `player_development`, `discipline`, `leadership`, `veteran_preference`, and `reputation` exist on both profiles. An event with only the component name would be ambiguous once one identity has both tracks.
- Replay follows the player-development pattern. Within one track, apply components in that profile's ability order, and apply one component's events in `event_index` order. Clamp after every delta. The clamp is `max(0, min(potential, current + delta))`. `potential` is the shared profile value for that track and component. Reputation clamps to `0` and `100`. There is no reputation potential. The stored delta remains the requested delta, including when the clamp shrinks the effect. A single clamp of the summed deltas is not the replay. One track's events do not move the other track's rating.
- The stored season row freezes when `TimelineSeason.phase` is `complete`. Later reads use the frozen row. A later season's copy uses that frozen row, not a fresh formula.
- Decline is not a column and not an automatic birthday write. If age lowers an ability, a caller records an `age` event.

`StaffDevelopmentEvent` does not point at a player. A player's `coaching` reason does not point at a staff row. Connecting them is a later formula. Coordinator influence on development, when it exists, reads the coordinator season rating. It does not add a foreign key in this phase.

## Timeline-owned staff state

All of the following are new. None of them exist today. Names are proposals for a later implementation, not a migration in this phase. They are specified against `UniverseStaffIdentity`, so the implementation waits until that identity exists.

### `TimelineStaff`

One campaign's link to one shared identity. `timeline` and `identity` are immutable.

| Field | Rule |
|---|---|
| `identity` | `PROTECT`. The person. Not a GM or coach foreign key |
| `status` | `available`, `employed`, or `retired` |
| `retired_year` | Null unless `status` is `retired`. In the first implementation it is written once, to the season being closed, and services do not clear it |

Status matches employment the way a player's status matches the open roster row:

- `available`: no open employment, `retired_year` null.
- `employed`: exactly one open employment. `retired_year` null.
- `retired`: no open employment, `retired_year` set.

Leaving the employment count at zero does not mean retired. Retirement is an explicit close. In the first implementation it is permanent.

A retired staff member cannot hold an open employment and cannot receive a season-rating row for a season after `retired_year`. Events and the rating in the retirement year stay legal. Those bans are application checks, matching the way `TimelinePlayer` refuses later rows. Do not add a database check whose only job is to forbid employment or rating rows dated after `retired_year`. A future comeback would otherwise have to drop that constraint. The status/`retired_year` pairing check used by `TimelinePlayer` is appropriate. The write-once guard on `retired_year` is application code in the first implementation.

The future comeback path, not built now, is a `StaffReactivation` record plus a new employment. The closed `retired` employment stays immutable and is the durable record of the first retirement. The reactivation names the return season. That future service leaves `retired` status, clears `retired_year` in the same write the pairing check already allows, and opens a new stint. Clearing the year is why the first implementation keeps the write-once guard in application code rather than in a check constraint. Each track that already has a retirement-year rating copies that track's own frozen row. History is not edited. No first-implementation service performs that return. `hire_staff` refuses a retired person.

Unique `(timeline, identity)`. The identity foreign key uses `PROTECT`. Deleting a shared identity that a campaign references is refused. `timeline` uses `CASCADE`.

An overlay is inserted when the person becomes hireable in that campaign: the opening cohort at initialization, and each later debut year when that year is materialized. It is not inserted for a later debut year at timeline creation. The debut year that matters is the earliest profile debut on that identity. Archive-only staff do not exist; every current GM and coach has a debut inside the release.

### `GMSeasonRating` and `CoachSeasonRating`

One row per overlay per season per track, for a track the identity actually has, in seasons where that track is in use and the overlay is not past retirement. Explicit columns, not a document and not one row per ability.

A GM rating exists only when the identity has a GM profile. A coach rating exists only when the identity has a coach profile. The first year of a track copies `initial_*`. Each later year copies the previous completed rating of that same track. This phase does not keep a rating for a profile the identity does not have, and it does not invent a second profile so that a rating can exist.

The columns are `current_<ability>` for that track's list, plus `current_reputation`. There is no second opening-day row and no second visible vector. The row is live during the season and frozen when the season phase is `complete`.

Unique `(staff, season)` inside each rating table. The season belongs to the overlay's timeline. A rating is not inserted into a season whose phase is already `complete`. A rating is not inserted for a season year after `retired_year`.

### `StaffEmployment`

One job. A job may span seasons. It is not one row per season. Staff do not change every week, and a per-season copy would multiply rows without a new fact. The row closes when the chair, the club, the installed scheme, or the person's employment ends.

| Field | Rule |
|---|---|
| `staff` | The overlay. Immutable |
| `franchise` | `PROTECT`. Required. Staff employment is never a null-franchise row |
| `role` | One of the four playable chairs. Immutable. The identity must have the profile for that chair. Coordinator roles must match specialty |
| `offense_scheme`, `defense_scheme` | Coach rows only. A head coach stores both. An offensive coordinator stores offense only. A defensive coordinator stores defense only. A GM stores neither. Each stored value is one of the existing scheme enums. Immutable. A value written now must be legal for the era profile on the employment's season. The shared `preferred_*` values are the default a hire may copy when that default is legal; they are not overwritten |
| `reports_to` | Null for a GM or a head coach. For a coordinator, null or the head-coach employment at the same franchise that was in force when this job opened. It stays null when that chair is empty, including a participant-controlled club where the participant is the head coach and an AI club during an offseason vacancy. A coordinator hire does not require a synthetic head coach to satisfy this field. `SET_NULL` so a timeline can be deleted. Services do not delete employment rows, so the link survives ordinary play. Immutable |
| `start_year`, `start_week` | `start_week` is at least 0. Week 0 is the preseason and the opening copy |
| `end_year`, `end_week`, `reason_ended` | All null while open. All set together when the row closes. `end_year` is at least `start_year`. In the same year, `end_week` is at least `start_week` |
| Open key | A stored generated column that is the overlay id while `end_year` is null and null when closed. Unique. Two open jobs for one person cannot exist, including mid-transaction |

Close reasons are `fired`, `resigned`, `promoted`, `reassigned`, `hired_elsewhere`, `philosophy`, `retired`, and `timeline_complete`.

Week overlap inside one year uses the roster rule: inclusive ranges must not share a week, except a later row may start on the earlier row's end week, and that boundary week belongs to the later row. Gaps are legal. Spans in different years do not share a week comparison across the year boundary; a row that covers 1972 through 1974 occupies those seasons until its end week in the final year. `schedule_length` is not a staff-week cap.

A franchise open-chair key, generated from the franchise and role while the row is open, keeps a second GM or a second offensive coordinator off the same club. Closed rows do not collide.

When `reports_to` is set, it records the head-coach stint under which the coordinator job opened. A null value means no head-coach employment was in force. It is not a live pointer that must move when the head coach changes. The live head coach, if there is one, is the open head-coach employment at that franchise. Firing the head coach does not close coordinator rows and does not rewrite `reports_to`. A later service that wants the coordinator to report to a new head coach closes and opens the coordinator employment. The schema does not do that by itself.

Closed rows stay closed. Role, franchise, scheme, start, and `reports_to` do not change. End fields are written once, at the close. A comeback does not reopen a `retired` row.

### `StaffDevelopmentEvent`

Append-only. `staff`, `season`, `track`, `component`, `delta`, `reason`, `event_index`. `track` is `gm` or `coach`. Unique `(staff, season, track, component, event_index)`. `component` is one of that track's abilities or `reputation`, and the season rating for that track must be the row the event explains. `event_index` starts at 1. The model stores the delta. It does not apply it. Events in the retirement year are legal. Events in a later year are rejected. The formula that chose the delta is not stored.

### `TimelineStaffHonor`

A playable-year award. `season`, `staff`, `franchise`, and `kind` of `coach_of_year` or `executive_of_year`. The kind strings match `UniverseAward`. Unique `(season, kind)`. The season already belongs to one timeline, so that unique key is one winner for each honor, each timeline, and each season.

The kind must match a profile the identity has: `executive_of_year` requires the GM profile, and `coach_of_year` requires the coach profile. The season year is inside the timeline. A later awards service supplies the winner. The selection formula is not in the schema. This table does not copy `UniverseAward` and does not store player awards.

Wins, losses, ties, playoff appearances, and championships are not columns. `UniverseStaffPost` stores them because the archive has no games. From 1970 on, a later standings service counts games whose coach or GM of record is the employment that covers that week. The employment's franchise, role, and span are the boundary. Caching those totals on the employment would drift. Derived display can wait for standings.

## Human participant and AI staff

The participant is the owner. On that participant's club, the participant performs the GM and head-coach functions. The database does not turn the user into a staff person, and it does not require a synthetic employee in either chair.

The project overview describes an offseason in which the owner keeps or replaces a real GM and a real head coach. The shared `OpeningStaffAssignment` rows remain that publication template. Campaign initialization does not seat them on the participant's club. Seating them would duplicate the jobs the participant is performing. Those template people are materialized as available, and the participant may later hire them into a coordinator chair or, once delegation exists, into a delegated GM or head-coach chair.

- Initialization reads the open `TimelineFranchiseControl`. A franchise whose open control has a participant with a non-null user is participant-controlled. Opening assignments for that franchise are not copied into employment.
- Every other founding franchise is AI-controlled at the start. Initialization copies all four opening chairs into open employment. A club with no opening assignment, including an expansion club that already exists when a timeline starts after 1970, is not given template employment. It hires from the available pool, and the regular-season check still requires its GM and head coach before simulation.
- A participant whose `user` is later set null becomes bot-controlled. Staff rows and past directives remain. Later decisions for that franchise ignore the directive. Before the next regular season, that club must have a seated GM and a seated head coach, like any other AI club. Past seasons are not resimulated. No recovery service rewrites the jobs.
- `OwnerDirective` remains the only owner instruction. One row per participant per season, already implemented. Bot clubs have none.
- No synthetic GM or coach is created for the participant, the user, or a vacated chair.
- In the first playable version, `draft_participation` does not by itself require a GM employee. The participant is performing the GM function. When the game later treats `delegate_to_gm` as real delegation, or adds a flag that delegates the head-coach function, the clock requires a real employee for that delegated chair before the delegated work runs. `hire_staff` seats that employee. The directive still does not create a person.
- Where an employed GM exists, `team_direction`, trade appetite, veteran preference, and the three priority groups bias that GM. Where an employed head coach exists, offensive emphasis, defensive emphasis, fourth down, and playing time bias that coach. On the participant's club, with those chairs empty, the directive is the instruction the later simulation reads directly. `automatic` veteran preference and playing time keep today's meaning: derive the lean from team direction, and do not overwrite a staff rating.
- Coordinator employment is optional. The participant may hire coordinators. An AI club may leave them vacant. The first playable version does not require a coordinator screen and does not require the chairs to be filled.
- An AI club reads the true season abilities of the staff who are actually employed. It has no directive. A later decision service may also read shared tags as a tie break. That service is not `OwnerDirective`.
- Before regular-season simulation, an AI club has an open GM employment and an open head-coach employment. Offseason vacancies are legal. The check is a clock precondition, not a row that must exist in every phase.

## Shared pool across timelines

The release holds every candidate before any campaign starts. A timeline consumes nothing from that pool. Hiring, firing, development, and retirement write timeline rows only.

| Moment | Shared data | Timeline write |
|---|---|---|
| Publication | All identities and profiles with debut years from the first season through the last, plus opening assignments for the founding clubs | None |
| Timeline initialization | Read `OpeningStaffAssignment`, open franchise control, and every identity whose earliest profile debut is the timeline's start year | Overlays and first season ratings from `initial_*`. Open employment for the opening chairs of AI founding clubs. The participant's template staff, and every other start-year person without a copied chair, are `available` |
| Later debut year | People with that profile `debut_year` already exist | Materialize overlays and that season's initial rating for each new track. No employment. Do not materialize them early |
| Ordinary hire or fire | Unchanged | Close and open employment. Update status |
| Expansion year | The same available people in every timeline. `gm_hiring_rule` and `hc_hiring_rule` stay unread prose | The caller supplies the selected candidates. `hire_staff` records each employment change. It does not parse the prose and it does not mint a person |
| Another timeline | The same identity key | A different overlay, different jobs, different ratings |

Opening initialization materializes the start-year cohort, not the entire future pool. People with a later debut are absent until that year. That matches draft classes: creating a timeline does not insert the 1988 people.

The pool does not run out because of campaign use. A timeline can employ only one person per chair per club, and retirement in that timeline does not delete the shared person or the other timelines' overlays. Later decades stay supplied because the generator writes debut years through the release's last season, including the larger expansion-year classes the overview describes. Those counts are publication targets. They are not schema constants. If a publication wants more candidates than the floor, it stores more people. The career tables do not change.

An expansion club has no `OpeningStaffAssignment`. It hires from the same available timeline staff pool as every other club, including that year's newly materialized class. Later AI or participant-choice logic decides who is offered and hired. This layer only records the employment the caller already chose. The employment change is one atomic, retry-safe `hire_staff` call. A caller that must seat several people, such as a GM and a head coach together, invokes those calls in one transaction. A nested call joins the open `deferred_lifecycle()` block. One failure rolls the group back. An exact retry of a hire that is already committed writes nothing.

## Historical eras

`EraRuleProfile` stays the season's rule card. It has no scheme allow-list today. `style_key` is not that list. This pull request adds no columns to the era profile.

Before `hire_staff` and `advance_staff_season` exist, a new universe migration adds the allow-list: child rows of `EraRuleProfile`, one legal scheme each. The side is `offense` or `defense`. An offense row's scheme is one of `OFFENSE_SCHEMES`. A defense row's scheme is one of `DEFENSE_SCHEMES`. The row is unique on the era profile, the side, and the scheme. `style_key` is not a member of this table. Existing era rows are not rewritten into a different shape beyond that child table. The allow-list is release data. It is not a hardcoded 1970 or 2004 check in the career tables.

A scheme on a new employment, or on a philosophy change, must be allowed by the era profile of the season in which that employment starts. `TimelineSeason.profile` is that era profile, and the link stays frozen with the season. The shared debut-year checks on `preferred_offense` stay on `UniverseCoach`. They are not reapplied as the timeline legality rule. A hire may copy a preferred scheme only when the era allow-list also contains it.

The employment row keeps the scheme it was given. Season ratings store abilities, not a scheme, and a later era does not rewrite either one. A 1970 head coach who was hired into `power_run` is still that employment row in 1974 if the job never closed.

If either stored scheme on an open coach employment is absent from the next season's era allow-list, `advance_staff_season` fails unless the caller includes a `philosophy` close of that row and a new open employment whose stored schemes are legal. A head coach's replacement must be legal on both sides. A coordinator is checked only on its side. The closed row keeps the old scheme. One missing replacement rolls the handoff back. A scheme the next era still allows does not force a new stint. A GM employment has no scheme and is not part of this replacement.

1970 already seats coordinators in `OpeningStaffAssignment`. This design does not remove them from the schema to imitate a world without coordinators, and it does not add position-coach chairs to imitate a modern staff. The same four chairs cover every era. Coordinator seats may still be empty. What changes by era is which scheme values a hire may install. Abilities are interpreted by later services. Those services do not update closed jobs.

`cap_management` exists before the cap era. Before a cap, it still describes contract shape. The era decides whether the number binds a cap. The ability is not wiped when the era changes.

## Constraints, indexes, deletion, and scale

Implementation follows the merged conventions: `BigAutoField`, `CheckConstraint` with `condition`, index names of at most 30 characters, `full_clean()` on instance `save()`, and no database triggers. Queryset `update`, `bulk_create`, `bulk_update`, and queryset `delete` are not the career-service path. MySQL nulls stay distinct, so open jobs use a generated column rather than a nullable end in the unique key. This project's MySQL backend does not support partial unique indexes.

| Rule | Where |
|---|---|
| One identity per timeline | Unique `(timeline, identity)` on `TimelineStaff` |
| Profile name and birth year match the identity | Application check on the profile, added with the identity migration |
| At most one GM profile and one coach profile per identity | Unique identity foreign key on each profile table |
| Status matches `retired_year` | Check, same pairing as `TimelinePlayer` |
| No open employment and no later season rating while retired | Application check. Not a database check that a comeback would have to drop |
| `retired_year` written once | Application check in the first implementation |
| One open employment per person | Generated open key, unique |
| One open chair per franchise | Generated `(franchise, role)` key while open, unique |
| Chair matches a profile the identity has; coordinator specialty matches | Application check against the shared profile |
| New or changed scheme is on the era allow-list | Application check in `hire_staff`, `reassign_staff`, and `advance_staff_season`, once the allow-list exists |
| Scheme columns match the chair | Check: GM both null; coordinator only its side; head coach both set |
| Week order and same-year overlap | Application check, same boundary rule as roster membership |
| Reason paired with the end | Check: end fields and `reason_ended` are all null or all set |
| Season rating unique and frozen when the season is complete | Unique `(staff, season)` on each rating table; application freeze |
| No season rating after `retired_year` | Application check |
| Ability not above potential; reputation 0–100 | Database `<= 100` where a column exists; potential ceiling in `full_clean` |
| Development track, component, delta, index | Checks and unique `(staff, season, track, component, event_index)`. `track` is `gm` or `coach`. Delta from −20 through 20. Reasons are the seven audit labels |
| One winner per honor, timeline, and season | Unique `(season, kind)`. Kind matches a profile the identity has |
| Closed employment immutable | Application check. No second write |
| Person, timeline, role, franchise, scheme, start, `reports_to` immutable | Application check |
| No coordinator cascade when a head coach leaves | No such constraint. Each employment closes on its own |

Indexes, with short names at implementation time:

- `TimelineStaff (timeline, status)` and `(identity)`.
- `StaffEmployment (staff, start_year)` and `(franchise, role, start_year)`.
- Season rating `(season)` besides the unique staff-season key.
- Development `(staff, season)`.
- Honor `(staff, season)`.

Deletion:

| Deleted object | Effect |
|---|---|
| `UniverseRelease` | Cascades shared identities, profiles, posts, awards, and opening assignments. A timeline that still points at the release is `PROTECT` on `Timeline.release`, so a referenced release is not removed |
| Shared identity | `PROTECT` from `TimelineStaff`. Refused while any campaign points at the person |
| Shared GM or coach profile | Still referenced by posts, awards, opening assignments, and the rating profile. The identity migration does not retarget those foreign keys |
| Franchise | `PROTECT` from employment and honors |
| Timeline | Cascades overlays, ratings, employment, events, and honors. `reports_to` is `SET_NULL` so the collector can remove the employment rows |
| Timeline season | Cascades that season's ratings, development events, and honors. Employment points at years, not at the season row, so a season delete must be refused by the application while an employment span uses that year. The clock does not delete seasons |
| User account | Sets the participant user null. Does not delete staff, directives, or the timeline |
| Closed employment or a frozen rating | Not an application delete. History stays |

Scale, as planning figures for one timeline that materializes every shared person and plays all 60 seasons. They are not checks. The overview's 1,610 staff profiles are the planning pool (462 GMs, 580 head coaches, 568 coordinators). While each profile is its own identity, that is also 1,610 identities. A future publication that attaches two profiles to one person lowers the identity count and does not change these tables. A second timeline stores its own career rows and no extra shared people.

| Stored | Order of magnitude | Why this and not a copy |
|---|---|---|
| Shared identities and profiles | About 1,610 profiles, once per release, one identity each until a publication says otherwise | The person and the starting vectors |
| Overlays | One per materialized identity per timeline. The opening year is the start-year cohort only | Later debuts wait |
| Season ratings | One per active track per season from the first use of that track through retirement | Past seasons stay readable. A single mutable current row would destroy that |
| Employment | A handful of rows per career, not one per season. A few thousand rows per full timeline | The job is the fact. The season rating is the ability |
| Development events | One row per changed component, not a zero-delta row for every ability every year | The replay of an empty component is the prior value |
| Honors | At most two per season | One coach of the year and one executive of the year |

Many timelines multiply the timeline side only. Week-level staff snapshots, per-ability reveal rows, and cached win totals are not stored. Game results, when they exist, reconstruct the record from the employment span. Frozen season ratings reconstruct the ability. Closed employment reconstructs the career.

`seasons_employed` is derived: the distinct seasons covered by employment whose franchise is set, which is all of them. It is not a column.

## Season-to-season career behavior

A season does not close a job by itself. Completing 1974 leaves the open employment open. The 1974 ability row freezes. Advancing to 1975 copies that frozen vector onto 1975 for every non-retired overlay that already had that track in 1974. Someone materialized for 1975 as a new debut does not need a 1974 rating. A second track that does not exist yet is not a missing-rating failure.

`advance_staff_season` does not create `TimelineSeason` and does not set phase. It retires the people the caller names, copies frozen ratings for the other belonging overlays, and leaves open jobs open. The exception is an installed scheme the next era no longer allows. The call then requires the caller's `philosophy` close and the replacement employment, and it writes both in the same transaction. One disagreement rolls the handoff back. Next-year-only debuts are not copied.

Retirement during the handoff closes the employment with `retired`, sets `retired_year`, and does not copy a next rating. If a next rating was created early and is still only an unmodified copy, with no employment and no development in that next season, the handoff may delete that one rating before it writes `retired_year`. Any richer next-season history means the caller must retire the later season instead. That is the same guard as a player, with employment in place of roster membership. After `retired_year` is set, no later season-rating row is created.

The first implementation does not clear `retired_year` and does not open a later job for that person. The future comeback path is the reactivation record described on `TimelineStaff`. It is not part of the handoff.

Development recorded during the season updates the live rating. After the phase is `complete`, the rating and its events stay. A retry of the same events must match before any new delta is applied.

Philosophy, promotion, and a club change are employment changes. They can happen in the offseason at week 0 of the new season, or inside a season at a caller-supplied week. They do not rewrite the previous row's scheme or role. A philosophy change uses a scheme the current era allows.

## Future service boundaries

These services are specified here and not implemented. They follow the player lifecycle rules: one call is one transaction; a failure raises and rolls back; instance `save()` and, for the one guarded early rating, instance `delete()`; status and employment change together inside `deferred_lifecycle()`; a nested call joins the open deferred block; an exact committed match returns before source-state checks and before the active-timeline and completed-phase guards; a partial or conflicting row raises and writes nothing. A completed or archived timeline cannot gain, change, or lose staff rows. A retry that already matches writes nothing, including on a closed timeline.

The caller supplies the person, the chair, the scheme, the week, and any ability delta. The service does not choose who is hired, who is fired, how a rating moves, who wins an award, or when a season ends.

| Service | Writes | Notes |
|---|---|---|
| `initialize_opening_staff` | Overlays, initial season ratings, and open employment for AI founding clubs. Available overlays for the participant's template staff and every other start-year person | Reads open `TimelineFranchiseControl` to decide which clubs are participant-controlled. On an AI club, each copied coordinator's `reports_to` is the head-coach employment opened for that franchise in the same transaction. One identity is seated at most once. Two opening assignments that resolve to the same identity fail the cohort rather than opening two jobs. One disagreement rolls the cohort back. An exact copy is a no-write success. Does not write `OpeningStaffAssignment`. Does not create `OwnerDirective` or set the phase |
| `materialize_staff_class` | Overlays and initial ratings for one debut year | No employment. Does not overwrite a rating. For a person in that class, an overlay that already exists without that year's rating for the track being materialized is a partial state and fails. A first rating on a newly usable second track is the write, not a missing prior year. People outside the class are left alone |
| `hire_staff` | Open employment, status `employed` | Source is `available` only. A retired person is refused. The identity must already have the profile for the chair. The scheme must be on the era allow-list. The franchise chair must be vacant. Expansion hires are this call |
| `fire_staff` | Close with `fired` or `resigned`, status `available` | The caller names which employment. Coordinators are a separate call. The person becomes available, not retired |
| `reassign_staff` | Close and open | Used for `promoted`, `reassigned`, `hired_elsewhere`, and `philosophy`. The reason must match the change. A philosophy close keeps the chair and franchise and changes scheme. The new scheme must be legal in that season's era. A promotion changes the chair and still requires the destination profile |
| `record_staff_development` | Events and the live season rating for the named track | Applies that track's components in profile ability order, and each component's deltas in `event_index` order, clamping after every delta. An exact stored event is a no-write success. A conflicting payload fails before any write. Does not choose the delta |
| `advance_staff_season` | Retire the named people, copy frozen ratings, leave legal open jobs open, and replace an illegal scheme only when the caller supplies the new stint | Does not create the next `TimelineSeason` and does not set phase. Does not purge coordinators. Next-year-only debuts are not copied and are not a missing-rating failure. One disagreement rolls the handoff back |
| `retire_staff` | Close with `retired`, set status and `retired_year` | May delete one unmodified next rating under the guard above. Does not invent a replacement hire. Does not clear `retired_year` later |
| `verify_staff_replay` | Nothing | Read-only. A closed timeline does not block it |

`reactivate_staff` is not a first-implementation service. The comeback path stays documented on the overlay and is not required to ship careers.

A later awards service chooses coach of the year and executive of the year and inserts `TimelineStaffHonor`. Uniqueness is the schema's only vote. A later AI decision service reads the employed staff's season abilities and, for a franchise with a playable human, the locked `OwnerDirective`. It then calls the player lifecycle and, when those systems exist, contract, draft, and simulation services. It does not write shared people and does not edit the directive. Coordinator effects on development and simulation belong to that later service.

The season clock, not these services, refuses to start regular-season simulation for an AI club that lacks a GM or a head coach. Coordinator vacancies do not trip that check.

Replay and idempotency match the player services closely enough that the implementation can share the same transaction habits. They should not share tables. A staff delta must not be writable through `record_development` on a player.

## Alternatives considered

| Alternative | Why it is not the recommendation |
|---|---|
| One mutable `GMCurrentRating` or `CoachCurrentRating` | The old model sketch. It cannot answer what the GM was in 1978 after 1984 development. Players already rejected a single current row |
| Keep GMs and coaches permanently unable to be the same person | That is what the current tables happen to store. It is not a game rule. A 60-year coach-to-GM career is realistic, and pointing `TimelineStaff` at `gm` XOR `coach` would force every campaign table to move later |
| Merge the GM and coach ability columns into one row | The vectors differ. A person needs a GM profile, a coach profile, or both. Empty abilities on the missing track would be fiction |
| Fold `UniversePlayer` into the same identity now | Former-player is already a label. Unifying players with staff is a larger refactor and is not required for a coach to become a GM |
| A timeline position-coach pool | Cost without an owner decision or a distinct ability. Historical posts already cover the biography. Coordinators can later affect development without a position-coach chair |
| An `unemployed` employment row | A vacancy would be a fake job. Available is the absence of an open row |
| One employment row per season | Tens of thousands of identical copies. The ability row is already per season. The job changes rarely |
| Cascade-close coordinators when the head coach leaves | The user-facing staffing choice is retain, fire, release, or replace. The schema would hide that choice inside a delete rule |
| A database check that forbids any row after `retired_year` | The first implementation already refuses those rows in application code. The check would have to be dropped for the documented comeback path |
| Win-loss columns on timeline employment | They would drift from games. Archive posts keep counters because the archive has no game table |
| A synthetic staff row for the owner | Breaks identity, debut year, and cross-timeline comparison. The participant performs the GM and head-coach functions through `OwnerDirective` |
| Seat the opening GM and head coach on the participant's club anyway | Those people would duplicate the jobs the participant performs. They stay in the available pool. AI clubs still copy the template |
| Normalized ability rows, or a second stored visible vector | The same cost already rejected for the 33 player components. Grades and ranges are a presentation function. Scouting projections are later |
| A `StaffClass` table | `debut_year` already groups the cohort. There is no draft-round arithmetic to store |
| Treat `style_key` as the scheme catalog, or skip the allow-list | `style_key` does not name `power_run` or `air_raid`. Hire and season advance need an explicit list. Closed jobs still keep the scheme they stored |
| Parse `gm_hiring_rule` and `hc_hiring_rule` inside the career service | They are prose. The caller supplies the people. `hire_staff` only writes the employment |
| Put the coach-of-the-year formula on `TimelineStaffHonor` | The awards service supplies one winner. The schema enforces one row per honor, timeline, and season |

## Resolved decisions

1. **Retirement and return.** The schema has no mandatory retirement age. Later game logic chooses retirement from age, career state, ability, reputation, opportunity, and era. Retirement is permanent in the first implementation. A retired staff member cannot hold an open employment or receive a later season-rating row. The future comeback is a documented `StaffReactivation` plus a new stint, without editing the closed employment and without a database check that would have to be dropped. No reactivation service ships with the first career services.
2. **Head-coach firing.** Firing or losing a head coach does not remove either coordinator. Each employment is independent. A later staffing service may retain, fire, release, or replace coordinators. The data model has no automatic purge.
3. **Required jobs and vacancies.** AI clubs may have vacancies during the offseason. Before an AI club begins regular-season simulation, it has a seated GM and a seated head coach. Coordinator seats may be vacant in the first playable version. A participant-controlled club does not require synthetic GM or head-coach employees, because the participant performs those functions. If the game later allows the participant to delegate either function, that delegated chair can require a real employee.
4. **Schemes and eras.** A newly assigned or changed scheme must be allowed by the applicable era profile. The allow-list is a new universe child of `EraRuleProfile`, with side `offense` or `defense` and a scheme from that side's existing enum. It is required before hire and season advance, and it is not `style_key`. If either stored scheme on an open coach job becomes illegal in the next season, advancement requires a legal replacement and closes and opens the employment. The closed employment keeps the scheme that was legal then. Season ratings do not store a scheme and are not rewritten.
5. **Staff development.** Formulas stay outside the schema. Ordered development events and the resulting season rating are the audit path. Later logic may use age, role, performance, experience, mentorship, workload, and era to choose deltas. The service applies and clamps those deltas in order, following the player-development pattern.
6. **Ability visibility.** True abilities stay internal. The owner initially sees reputation, career history, achievements, schemes, traits, and broad ability grades or ranges. Exact visibility may improve later through scouting or familiarity. This phase adds no second visible-rating vector. Presentation and scouting projections are later work.
7. **Coordinators.** Offensive and defensive coordinator chairs are in the durable schema now. Employing them is optional in the first playable version. Position coaches stay shared historical background. A later service may let coordinators affect development and simulation. This phase does not add that link.
8. **Expansion hiring.** Expansion teams hire from the same available timeline staff pool. The caller supplies the selected candidates. Later AI or participant-choice logic decides who is offered and hired. `hire_staff` makes the employment change atomic and retry-safe. The hiring prose is not parsed.
9. **Annual honors.** Coach of the year and executive of the year are timeline facts on `TimelineStaffHonor`. A later awards service supplies the winner. At most one winner exists for each honor, timeline, and season. The selection formula is not in the schema.
10. **Coach-to-GM careers.** Separate `UniverseGM` and `UniverseCoach` rows are a schema limitation, not a ban. The next universe migration adds `UniverseStaffIdentity` and points both profiles at it before any `TimelineStaff` foreign key exists. The first services still hire a chair only when that profile is already on the identity.

## Remaining product choices

These do not change the tables above.

- The function that turns a true ability into a public grade or range. It is presentation, and it reads the season rating.
- Which season inputs the awards service reads. The honor row stores the winner the service supplies.
- Whether the product offers a philosophy change during the regular season or only in the offseason. Employment weeks can store either, and either scheme still has to be legal for that season's era.
- Which published identities receive a second profile. The schema allows one GM profile and one coach profile. This phase does not generate the second profile, and timelines do not invent it.

## Staged implementation sequence

Each stage is a later change. This document is stage 0. This pull request stops here.

1. **Shared identity and scheme legality.** A new universe migration adds `UniverseStaffIdentity`, the required unique foreign keys from `UniverseGM` and `UniverseCoach`, the one-to-one backfill, and the application consistency checks. The same stage adds the era scheme allow-list, before any timeline staff table exists, so later hire and advance rules are not written against a missing legality source. Do not edit `0002` or `0003`. Do not seed rows. Do not retarget posts, awards, opening assignments, or rating profiles. Do not add timeline tables in this stage.
2. **Timeline staff tables.** Add `TimelineStaff` pointing at the identity, the two season-rating models, `StaffEmployment`, `StaffDevelopmentEvent`, and `TimelineStaffHonor`, with the constraints and indexes above. Generate the migration. Do not run it. Do not change `OwnerDirective` or player services.
3. **Career services.** Implement the service table in this document. There is no `reactivate_staff`. Hiring is from `available` only. Advancement replaces an illegal scheme only when the caller supplies the new stint. Vacancy rules for the regular season stay on the clock. Atomic, retry-safe, and free of hiring logic. No AI and no UI.
4. **Decision and presentation.** A later service reads abilities and directives, decides what the owner is shown, selects awards, and may apply coordinator effects. Optional comebacks and cross-family hires wait until a publication has attached the second profile and a reactivation path is actually built. Contracts, transactions, schedules, standings, and simulation join there. They are not stage 1, 2, or 3.

Stage 2 should stop if a merged constraint makes the model illegal. The likely sharp edges are MySQL null uniqueness, generated open keys, and `reports_to` during timeline deletion. Those are called out above so the implementation can follow them instead of inventing a second employment history. Stage 2 should also stop if `UniverseStaffIdentity` is not in the database yet. An overlay foreign key to `gm` XOR `coach` is the outcome this design rejects.

## Confirmation

This phase changes one design document. It changes no application code, model, migration, setting, or database state. It does not run migrations and it does not contact a database. Shared staff rows remain the ones already merged. `UniverseStaffIdentity` and the era scheme allow-list remain unbuilt until the next stage. Campaign careers remain unbuilt until a later stage implements this proposal.
