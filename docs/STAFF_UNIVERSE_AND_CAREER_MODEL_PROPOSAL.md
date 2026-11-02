# Federation Gridiron — Staff Universe and Career Model Proposal

This document proposes the campaign career layer for coaches and general managers. It is a design review. It does not add models, migrations, services, admin, APIs, pages, forms, or database rows.

The playable horizon remains 60 seasons, 1970 through 2029 inclusive, on a published `UniverseRelease`. Many independent timelines share that release. Staff follow the same boundary as players: identity and reusable starting information belong to the universe; employment, development, decisions, achievements, and career history that can differ by campaign belong to the timeline.

This proposal uses the merged universe staff tables as they are. It does not reopen them. The unimplemented staff sketches in [PLAYER_UNIVERSE_MODEL_PROPOSAL.md](PLAYER_UNIVERSE_MODEL_PROPOSAL.md) (`GMCurrentRating`, `CoachCurrentRating`, a single mutable reputation, and scout-component tables bundled into the old phase 5) are not the authority. The authority for shape is the merged player pattern: one overlay, one season ability row, immutable employment history, and ordered development events. Player contracts, transactions, game simulation, schedules, standings, and scouting reports stay out of this phase except where a later service must read staff.

## Existing schema findings

Shared staff already exist. Timeline employment does not.

| Already merged | What it stores | What it does not store |
|---|---|---|
| `UniverseGM`, `GMRatingProfile` | Stable key, name, birth year, debut year, former role, two positional priorities, strength and weakness tags, two traits, 15 initial and potential abilities, initial reputation | Any campaign's current ability, job, or retirement |
| `UniverseCoach`, `CoachRatingProfile` | The same identity facts, specialty, preferred offense and defense, coach tags and traits, 17 initial and potential abilities, initial reputation | A campaign scheme, a chair, or a season-by-season ability |
| `UniverseStaffPost` | Pre-debut jobs only. The post must end no later than the year before `debut_year`. A Federation franchise post must end before the release's first playable season | Any 1970–2029 employment. Those years are campaign history |
| `UniverseAward` | Archive awards, including `coach_of_year` and `executive_of_year`, in the years before the first playable season | Awards earned inside a timeline |
| `OpeningStaffAssignment` | The shared 1970 template: one `gm`, `head_coach`, `offensive_coordinator`, and `defensive_coordinator` per founding franchise | The copy a timeline keeps after the owner replaces someone |
| `ExpansionEvent.gm_hiring_rule`, `hc_hiring_rule` | Prose the publication can require to be non-empty | A parsed hiring algorithm |
| `OwnerDirective`, `TimelineParticipant`, `TimelineFranchiseControl` | The human owner's broad instructions and which franchise that human controls | A staff person, a vacancy, or an AI decision |
| `EraRuleProfile` on `TimelineSeason` | Roster, draft, contract, and market rules for that year | Staff chairs, scheme legality, or retirement age |
| Player lifecycle | `coaching` as a label on a player development event | A foreign key to a coach |

`UniverseGM` and `UniverseCoach` are different tables. A row is one or the other. `UniverseStaffPost` and `OpeningStaffAssignment` each require exactly one of those foreign keys. MySQL treats `NULL` as distinct, so the unique keys on `gm` and on `coach` do not collapse the null side; the check constraint is what keeps exactly one person. The same fact matters for the timeline overlay below.

Debut age is already checked: `debut_year` is at least `birth_year + 20` and at most `birth_year + 80`, and it lies inside the release. There is no stored age and no shared retirement year. Preferred `west_coast` is rejected unless `debut_year` is 1987 or later. Preferred `air_raid` is rejected unless `debut_year` is 2004 or later. Those checks sit on the shared coach. They are not era-profile columns, and this proposal does not move them.

Opening roles are `gm`, `head_coach`, `offensive_coordinator`, and `defensive_coordinator`. Specialty limits the coordinator chairs: offense or both for an offensive coordinator, defense or both for a defensive coordinator. Specialty does not stop a head-coach chair. `position_coach` exists on `UniverseStaffPost` and on `UniverseCoach.former_role`. It is not an opening role.

`OwnerDirective` is one row per participant per season. Bot franchises have no participant and no directive. A locked directive cannot change. Deleting a user sets `Timeline.created_by` and `TimelineParticipant.user` to null and does not delete the campaign. A participant whose user is null is no longer a playable human; that franchise is treated as bot-controlled. No recovery service exists.

No timeline model names a GM or a coach. Creating a timeline does not copy staff. The player services do not read staff.

## Architectural boundary

| Layer | Owns | Who writes it |
|---|---|---|
| Universe | The person, the starting ability vector, tags, traits, preferred schemes, specialty, debut year, and pre-debut posts and awards | The generator, once, while the release is draft. Published rows stay immutable |
| Timeline | The overlay, season abilities, employment, development events, honors, and retirement inside one campaign | Timeline staff services. They reference universe rows and never update them |
| Owner | Broad instructions for the franchise that participant controls | The existing directive path. It does not create a person and does not edit abilities |

A second timeline does not copy `UniverseGM` or `UniverseCoach`. It inserts overlay rows that point at those people. Two campaigns can employ the same `universe_key` at different clubs, develop different abilities, and retire on different dates.

Cross-timeline comparison joins on `universe_key`. It does not join on a timeline employment id.

## Recommended shared-universe entities

Keep the merged shared tables. Do not add a staff-class table, a second person table, or new ability columns in the first implementation stage.

| Entity | Role in this design |
|---|---|
| `UniverseGM` and `GMRatingProfile` | One GM identity and the reusable starting vector |
| `UniverseCoach` and `CoachRatingProfile` | One coach identity, specialty, preferred schemes, and the reusable starting vector |
| `UniverseStaffPost` | Pre-debut biography only |
| `UniverseAward` | Archive coach and executive awards only |
| `OpeningStaffAssignment` | The 1970 founding-club template that initialization copies |

`debut_year` groups later candidates. Publication checks on counts and signature uniqueness stay in the generator. They are not new tables.

## Shared staff identity

Keep `UniverseGM` and `UniverseCoach`. Do not merge them into one person table. Opening assignments, rating profiles, posts, and awards already require exactly one of the two keys. A general manager and a coach are different candidate pools. A single shared row does not qualify for both tracks.

Within a track, one person can hold several roles over a career:

- A GM's only playable chair is `gm`. Pre-debut posts may be `personnel_director`, `scout`, or `college_coach`. Those posts stay on `UniverseStaffPost`. A timeline does not employ a GM as a scout.
- A coach is one person for `head_coach`, `offensive_coordinator`, and `defensive_coordinator`. Promotion, demotion, and a later return to a coordinator chair are new timeline employment rows on the same overlay. They are not a new `universe_key`.
- Specialty is shared eligibility for a coordinator chair. It is not rewritten when the person becomes a head coach. Head coach remains legal for every specialty.
- `position_coach` stays a historical and former-role value. This proposal does not seat position coaches. See [Roles and career paths](#roles-and-career-paths).

Identity fields that already support 1970 through 2029:

| Fact | Where | Rule |
|---|---|---|
| Stable identity | `universe_key` | Unique across the database. The generator's key shape stays `g-{release}-{debut_year}-{index}` and `c-{release}-{debut_year}-{index}`. Timelines never allocate a key |
| Display name | `name` | Not unique. The UI disambiguates with role, birth year, and key |
| Age basis | `birth_year` | Age in a season is `season_year - birth_year`. No age column |
| Entry | `debut_year` | The first year a timeline may materialize the person. Later classes exist on the release before any timeline reaches them |
| Retirement boundary | None on the shared row | Retirement is a timeline fact. The same person can still be employed in another campaign |
| Starting ability | `initial_*`, `potential_*`, `initial_reputation` | Copied into the first timeline season rating. Never updated from a campaign |
| Background | former role, priorities, tags, traits, specialty, preferred schemes | Shared, immutable, and visible as scouting flavor. They are not the season ability vector |

The overview's planning populations stay release data, not check constraints: 100 initial GMs and 100 initial head coaches; 24 employed in each of those chairs in 1970; coordinator identities in addition to that head-coach pool; incoming classes from 1971 through 2029 sized by the overview floors, with a larger class in an expansion year. Publication may refuse a release whose generated counts or staff-signature uniqueness do not match what that release claims. The tables do not hardcode 100, 76, 24, 8, or 6.

No new shared entity is required for a staff class. `debut_year` is the cohort key. `DraftClass` exists because a player class carries round counts and undrafted-pool arithmetic. A staff cohort does not.

## Roles and career paths

Playable chairs are the four opening roles:

| Chair | Person | Eligibility |
|---|---|---|
| `gm` | `UniverseGM` | The person is a GM. No specialty |
| `head_coach` | `UniverseCoach` | Any specialty |
| `offensive_coordinator` | `UniverseCoach` | Specialty `offense` or `both` |
| `defensive_coordinator` | `UniverseCoach` | Specialty `defense` or `both` |

Eligibility is both layers. The shared person decides which chairs are legal. The timeline decides which of those chairs the person holds, and when.

`position_coach` does not justify a timeline chair. The owner never hires one. Coordinator and head-coach abilities already cover scheme, play calling, and player development. A seated position-coach pool would add a labor market the owner does not see and a large employment history the simulator does not need. Historical `UniverseStaffPost` rows may still use the role. Timeline employment rejects it.

Unemployed staff are not an employment row and not a role value. A materialized person with no open employment is available. That is the 1970 pool of 76 GMs, 76 head coaches, and the unemployed coordinators. It is also every later candidate before a hire, and every person between jobs.

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
| Retire | `retired` | None |
| Season continues in the same job | no close | The open row stays open. A new season does not invent a row |
| Leave the campaign's clock at the final season | `timeline_complete` | None. Written only when a later clock closes surviving jobs |

`opening_replace` is not a reason. Replacing an opening coach is an ordinary fire plus hire. The shared `OpeningStaffAssignment` stays as it was.

A person has at most one open employment. A franchise has at most one open row per chair. Zero coordinators is legal. Zero GM or zero head coach is also legal in the schema; whether a season may start that way is unresolved below.

Resignation, firing, and retirement are records of a decision the caller already made. This design does not decide who quits, who is fired, or who retires.

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
| Veteran or youth lean | `veteran_preference` | The owner's `veteran_preference` directive can override the lean for the owned club. It does not rewrite this number |

`initial_reputation` has no potential. A season can still move reputation. Its ceiling is 100, not a personal potential.

### Coach — 17 abilities plus reputation

| Gameplay need | Stored ability | Also used, and not a new number |
|---|---|---|
| Offensive and defensive scheme knowledge | `offensive_scheme`, `defensive_scheme` | `preferred_offense` and `preferred_defense` are the person's origin, not the installed scheme |
| Play calling and game management | `game_management`, `clock_management`, `fourth_down_aggression`, `aggressiveness`, `in_game_adjustments` | The owner's fourth-down directive biases the owned club. It does not replace the rating |
| Player development | `player_development` | A player development event may later cite this coach. The player event stays a label until that formula exists |
| Leadership, discipline, adaptability | `leadership`, `discipline`, `motivation`, `adaptability` | Traits remain labels |
| Staff management | `staff_management` | Used when a head coach's tendencies affect coordinators. It does not hire by itself |
| Preparation and availability | `preparation`, `fatigue_management`, `injury_management` | |
| Veteran or youth lean | `veteran_preference` | The playing-time directive can override it for the owned club |

No scouting vector is added for coaches. No position-coach vector is added. Tags such as `develops_quarterbacks` or `halftime_adjustments` remain explanations of the numbers, not extra columns.

### What is true, and what a human may know

The season ability row is the true vector. The simulator, when it exists, reads that row. It does not read a reveal table.

The human owner does not see the integers. The owner can see the shared name, age, tags, traits, specialty, preferred schemes, the installed scheme on the current job, and a coarse public reputation. How the coarse grade is derived, and whether a later scouting report stores bands the way player evaluation does, is unresolved. This phase does not add `TimelineStaffScoutReport` or a normalized reveal table. Those were display tables in the old sketch. The player implementation deferred the same idea, and staff should not invent it early.

Bot decisions may read the true vector for every club, including the owned club. The owner's instructions are a separate input, described below. They are not a second ability vector.

### Development, decline, and past seasons

A season ability row is copied, then moved only by ordered development events.

- The first season rating for an overlay copies `initial_*` and `initial_reputation`.
- A later season copies that overlay's immediately previous completed season. It does not reread `initial_*`. A missing previous-year rating fails. An overlay materialized in the new season, including a new debut class, has no previous rating and does not need one.
- Each event stores the raw delta, from −20 through 20, and a reason. The reason set is `age`, `results`, `mentor`, and `scheme`. The reason records why the caller moved the number. It does not compute the number.
- Replay applies that component's events in `event_index` order and clamps after every delta. Abilities clamp to `0` and to shared `potential_*`. Reputation clamps to `0` and `100`. A single clamp of the summed deltas is not the replay.
- The stored season row freezes when `TimelineSeason.phase` is `complete`. Later reads use the frozen row. A later season's copy uses that frozen row, not a fresh formula.
- Decline is not a column and not an automatic birthday write. If age lowers an ability, a caller records an `age` event. The curve is an unresolved game rule.

`StaffDevelopmentEvent` does not point at a player. A player's `coaching` reason does not point at a staff row. Connecting them is a later formula, not a foreign key in this phase.

## Timeline-owned staff state

All of the following are new. None of them exist today. Names are proposals for a later implementation, not a migration in this phase.

### `TimelineStaff`

One campaign's link to one shared person. Exactly one of `gm` or `coach` is set. `timeline` and the person key are immutable.

| Field | Rule |
|---|---|
| `status` | `available`, `employed`, or `retired` |
| `retired_year` | Null unless `status` is `retired`. Set to the season being closed. A later reactivation clears it |

Status matches employment the way a player's status matches the open roster row:

- `available`: no open employment, `retired_year` null.
- `employed`: exactly one open employment. `retired_year` null.
- `retired`: no open employment, `retired_year` set.

Leaving the employment count at zero does not mean retired. Retirement is an explicit close.

`retired_year` may be cleared by reactivation. That differs from `TimelinePlayer`, whose retirement year is written once, because this design allows a possible return. The closed `retired` employment stays. A return does not reopen it.

Unique `(timeline, gm)` and `(timeline, coach)`. Because MySQL allows many nulls in a unique index, those two keys are not enough by themselves. A check constraint requires exactly one person key, which is the same pattern as `OpeningStaffAssignment`. Do not use a partial unique index. This project's MySQL backend does not support them.

The person foreign keys use `PROTECT`. Deleting a shared GM or coach that a campaign references is refused. `timeline` uses `CASCADE`.

An overlay is inserted when the person becomes hireable in that campaign: the opening cohort at initialization, and each later debut year when that year is materialized. It is not inserted for a later debut year at timeline creation. Archive-only staff do not exist; every `UniverseGM` and `UniverseCoach` has a debut inside the release.

### `GMSeasonRating` and `CoachSeasonRating`

One row per overlay per season, for the seasons in which that overlay exists and is not yet past retirement. A GM overlay has only GM ratings. A coach overlay has only coach ratings. Explicit columns, not a document and not one row per ability.

The columns are `current_<ability>` for that track's list, plus `current_reputation`. There is no second opening-day row. The row is live during the season and frozen when the season phase is `complete`.

Unique `(staff, season)`. The season belongs to the overlay's timeline. A rating is not inserted into a season whose phase is already `complete`.

### `StaffEmployment`

One job. A job may span seasons. It is not one row per season. Staff do not change every week, and a per-season copy would multiply rows without a new fact. The row closes when the chair, the club, the installed scheme, or the person's employment ends.

| Field | Rule |
|---|---|
| `staff` | The overlay. Immutable |
| `franchise` | `PROTECT`. Required. Staff employment is never a null-franchise row |
| `role` | One of the four playable chairs. Immutable. Must match the person track and, for coordinators, the specialty |
| `offense_scheme`, `defense_scheme` | Coach rows only. A head coach stores both. An offensive coordinator stores offense only. A defensive coordinator stores defense only. A GM stores neither. Each stored value is one of the existing scheme enums. Immutable. The shared `preferred_*` values are the default a hire may copy; they are not overwritten |
| `reports_to` | Null for a GM or a head coach. For a coordinator, the head-coach employment at the same franchise that this job reports to. `SET_NULL` so a timeline can be deleted. Services do not delete employment rows, so the link survives ordinary play |
| `start_year`, `start_week` | `start_week` is at least 0. Week 0 is the preseason and the opening copy |
| `end_year`, `end_week`, `reason_ended` | All null while open. All set together when the row closes. `end_year` is at least `start_year`. In the same year, `end_week` is at least `start_week` |
| Open key | A stored generated column that is the overlay id while `end_year` is null and null when closed. Unique. Two open jobs for one person cannot exist, including mid-transaction |

Close reasons are `fired`, `resigned`, `promoted`, `reassigned`, `hired_elsewhere`, `philosophy`, `retired`, and `timeline_complete`.

Week overlap inside one year uses the roster rule: inclusive ranges must not share a week, except a later row may start on the earlier row's end week, and that boundary week belongs to the later row. Gaps are legal. Spans in different years do not share a week comparison across the year boundary; a row that covers 1972 through 1974 occupies those seasons until its end week in the final year. `schedule_length` is not a staff-week cap.

A franchise open-chair key, generated from the franchise and role while the row is open, keeps a second GM or a second offensive coordinator off the same club. Closed rows do not collide.

`reports_to` is immutable. Firing the head coach does not, by itself, close coordinator rows. Their `reports_to` still names the head-coach job that was in force. Whether a service then fires those coordinators is unresolved. The schema does not cascade the firing.

Closed rows stay closed. Role, franchise, scheme, start, and `reports_to` do not change. The only later write on a closed row is none. End fields are written once, at the close.

### `StaffDevelopmentEvent`

Append-only. `staff`, `season`, `component`, `delta`, `reason`, `event_index`. Unique `(staff, season, component, event_index)`. `component` is one of that person's abilities or `reputation`. `event_index` starts at 1. The model stores the delta. It does not apply it. Events in the retirement year are legal. Events in a later year are rejected.

### `TimelineStaffHonor`

A playable-year award. `season`, `staff`, `franchise`, and `kind` of `coach_of_year` or `executive_of_year`. Unique `(timeline, season, kind)`. The kind must match the track: executive for a GM, coach of the year for a coach. The season year is inside the timeline. This table does not copy `UniverseAward` and does not store player awards.

Wins, losses, ties, playoff appearances, and championships are not columns. `UniverseStaffPost` stores them because the archive has no games. From 1970 on, a later standings service counts games whose coach or GM of record is the employment that covers that week. The employment's franchise, role, and span are the boundary. Caching those totals on the employment would drift. Derived display can wait for standings.

## Human participant and AI staff

The user is the owner. Gameplay still speaks as if that owner is the GM and the head coach, because the owner hires them and gives them instructions. The database does not turn the user into a staff person.

- The owned franchise has real `StaffEmployment` rows for a GM and a head coach, copied from `OpeningStaffAssignment` or hired later. Those people have `universe_key` values, abilities, and histories.
- `OwnerDirective` is the only owner instruction. One row per participant per season, already implemented. Bot clubs have none.
- No synthetic GM or coach is created for the participant, the user, or a vacated chair.
- `draft_participation` of `owner_selects` means a later draft service lets the owner pick for that club. The GM employment remains. `delegate_to_gm` means that service asks the employed GM's abilities to pick. The default stays `delegate_to_gm`.
- `team_direction`, trade appetite, veteran preference, and the three priority groups bias the employed GM. Offensive emphasis, defensive emphasis, fourth down, and playing time bias the employed head coach. `automatic` veteran preference and playing time keep today's meaning: derive the lean from team direction, and do not overwrite the staff rating.
- Coordinator hiring stays with the head coach. The owner is not given a coordinator screen in this design. The rows exist so a firing can promote someone who is already real.
- An AI club reads the true season abilities of its own GM, head coach, and coordinators. It has no directive. A later decision service may also read shared tags as a tie break. That service is not `OwnerDirective`.
- When `TimelineParticipant.user` is null, the franchise is bot-controlled. Staff rows and past directives remain. Later decisions for that franchise ignore the directive and read staff abilities only. Past seasons are not resimulated. No recovery service rewrites the jobs.

Firing the owned club's GM leaves a vacancy until a hire. The owner does not become the GM for that interval. The directive still exists. A decision service that runs while the chair is empty has no ability vector to read. Whether the clock allows that state is unresolved.

## Shared pool across timelines

The release holds every candidate before any campaign starts. A timeline consumes nothing from that pool. Hiring, firing, development, and retirement write timeline rows only.

| Moment | Shared data | Timeline write |
|---|---|---|
| Publication | All GMs and coaches with debut years from the first season through the last, plus opening assignments for the founding clubs | None |
| Timeline initialization | Read `OpeningStaffAssignment` and every GM and coach whose `debut_year` is the timeline's start year | Overlays, first season ratings from `initial_*`, and open employment for the opening assignments. Debut-year people without an assignment are `available` and have no employment |
| Later debut year | People with that `debut_year` already exist | Materialize overlays and that season's initial rating. No employment. Do not materialize them early |
| Ordinary hire or fire | Unchanged | Close and open employment. Update status |
| Expansion year | The same people in every timeline. `gm_hiring_rule` and `hc_hiring_rule` stay unread prose. The incoming class is whatever the generator stored for that debut year | The caller names the hires. The service records them. It does not parse the prose and it does not mint a person |
| Another timeline | The same `universe_key` | A different overlay, different jobs, different ratings |

Opening initialization materializes the start-year cohort, not only the 200 GMs and head coaches. Unemployed coordinators with that debut year are part of the hireable pool. People with a later debut are absent until that year. That matches draft classes: creating a timeline does not insert the 1988 people.

The pool does not run out because of campaign use. A timeline can employ only one person per chair per club, and retirement in that timeline does not delete the shared person or the other timelines' overlays. Later decades stay supplied because the generator writes debut years through the release's last season, including the larger expansion-year classes the overview describes. Those counts are publication targets. They are not schema constants. If a publication wants more candidates than the floor, it stores more people. The career tables do not change.

An expansion club has no `OpeningStaffAssignment`. Its first GM and head coach, and any coordinators, are timeline hires from people who are already materialized and `available`, including that year's new class. The service that records those hires is below. Choosing the people is not this design.

## Historical eras

`EraRuleProfile` stays the season's rule card. This proposal adds no staff columns to it.

Old employment rows keep the chair and the installed scheme they were given. A later era does not rewrite them. A 1970 head coach who was hired into `power_run` is still that row in 1974 if the job never closed. Adopting another scheme is a new employment with reason `philosophy`, dated in the season the change happens.

1970 already seats coordinators in `OpeningStaffAssignment`. This design does not remove them to imitate a world without coordinators, and it does not add position-coach chairs to imitate a modern staff. The same four chairs cover every era. What changes by era, if the game later requires it, is which scheme values a hire may install and how abilities are interpreted. Those are inputs to a later service or future era fields. They are not updates to closed jobs.

The shared checks that restrict `preferred_offense` by debut year stay where they are. They describe the person who was generated. They are not reapplied as a migration against timeline rows, because those rows do not exist yet. Whether a timeline may install `west_coast` or `air_raid` on a coach whose debut predates those labels is unresolved. Until that choice is made, a hire stores a scheme from the existing enums and does not consult the debut-year checks.

`cap_management` exists before the cap era. Before a cap, it still describes contract shape. The era decides whether the number binds a cap. The ability is not wiped when the era changes.

## Constraints, indexes, deletion, and scale

Implementation follows the merged conventions: `BigAutoField`, `CheckConstraint` with `condition`, index names of at most 30 characters, `full_clean()` on instance `save()`, and no database triggers. Queryset `update`, `bulk_create`, `bulk_update`, and queryset `delete` are not the career-service path. MySQL nulls stay distinct, so open jobs use a generated column rather than a nullable end in the unique key.

| Rule | Where |
|---|---|
| Exactly one of `gm` or `coach` | `TimelineStaff` check |
| Unique person per timeline | Unique `(timeline, gm)` and `(timeline, coach)`, plus that check |
| Status matches `retired_year` and the open employment | Application check, deferred when both change |
| One open employment per person | Generated open key, unique |
| One open chair per franchise | Generated `(franchise, role)` key while open, unique |
| Coordinator specialty | Application check against the shared coach |
| Scheme columns match the chair | Check: GM both null; coordinator only its side; head coach both set |
| Week order and same-year overlap | Application check, same boundary rule as roster membership |
| Reason paired with the end | Check: end fields and `reason_ended` are all null or all set |
| Season rating unique and frozen when the season is complete | Unique `(staff, season)`; application freeze |
| Ability not above potential; reputation 0–100 | Database `<= 100` where a column exists; potential ceiling in `full_clean` |
| Development component, delta, index | Checks and unique `(staff, season, component, event_index)` |
| Honor kind matches the track | Application check; unique `(timeline, season, kind)` |
| Closed employment immutable | Application check. No second write |
| Person, timeline, role, franchise, scheme, start, `reports_to` immutable | Application check |

Indexes, with short names at implementation time:

- `TimelineStaff (timeline, status)` and `(gm)`, `(coach)`.
- `StaffEmployment (staff, start_year)` and `(franchise, role, start_year)`.
- Season rating `(season)` besides the unique staff-season key.
- Development `(staff, season)`.
- Honor `(staff, season)`.

Deletion:

| Deleted object | Effect |
|---|---|
| `UniverseRelease` | Cascades shared people, profiles, posts, awards, and opening assignments, as it does today. A timeline that still points at the release is `PROTECT` on `Timeline.release`, so a referenced release is not removed |
| Shared GM or coach | `PROTECT` from `TimelineStaff`. Refused while any campaign points at the person |
| Franchise | `PROTECT` from employment and honors |
| Timeline | Cascades overlays, ratings, employment, events, and honors. `reports_to` is `SET_NULL` so the collector can remove the employment rows |
| Timeline season | Cascades that season's ratings, development events, and honors. Employment points at years, not at the season row, so a season delete must be refused by the application while an employment span uses that year. The clock does not delete seasons |
| User account | Sets the participant user null. Does not delete staff, directives, or the timeline |
| Closed employment or a frozen rating | Not an application delete. History stays |

Scale, as planning figures for one timeline that materializes every shared person and plays all 60 seasons. They are not checks. The overview's 1,610 staff identities are the planning pool (462 GMs, 580 head coaches, 568 coordinators). A second timeline stores its own career rows and no extra shared people.

| Stored | Order of magnitude | Why this and not a copy |
|---|---|---|
| Shared people and profiles | About 1,610, once per release | Already the identity |
| Overlays | One per materialized person per timeline. The opening year is the start-year cohort only, not all 1,610 | Later debuts wait |
| Season ratings | One per overlay per season from materialization through retirement. A full pool that never retired would be about 1,610 × 60 rating rows | Past seasons stay readable. A single mutable current row would destroy that, which is why players do not use one |
| Employment | A handful of rows per career, not one per season. A few thousand rows per full timeline | The job is the fact. The season rating is the ability |
| Development events | One row per changed component, not a zero-delta row for every ability every year | The replay of an empty component is the prior value |
| Honors | At most two per season | One coach and one executive |

Many timelines multiply the timeline side only. Week-level staff snapshots, per-ability reveal rows, and cached win totals are not stored. Game results, when they exist, reconstruct the record from the employment span. Frozen season ratings reconstruct the ability. Closed employment reconstructs the career.

`seasons_employed` is derived: the distinct seasons covered by employment whose franchise is set, which is all of them. It is not a column.

## Season-to-season career behavior

A season does not close a job by itself. Completing 1974 leaves the open employment open. The 1974 ability row freezes. Advancing to 1975 copies that frozen vector onto 1975 for every non-retired overlay that already belonged to 1974. Someone materialized for 1975 as a new debut does not need a 1974 rating.

Retirement during the handoff closes the employment with `retired`, sets `retired_year`, and does not copy a next rating. If a next rating was created early and is still only an unmodified copy, with no employment and no development in that next season, the handoff may delete that one rating before it writes `retired_year`. Any richer next-season history means the caller must retire the later season instead. That is the same guard as a player, with employment in place of roster membership.

A return, if the caller allows one, happens after that retirement year. It clears `retired_year`, sets `available` or `employed`, and may open a new job. It does not edit the retired job. The next season rating after a return is a new first rating for the new span only if no earlier rating exists. If the retirement year still has a rating, the return year's rating copies that frozen row. The person did not lose the past.

Development recorded during the season updates the live rating. After the phase is `complete`, the rating and its events stay. A retry of the same events must match before any new delta is applied.

Philosophy, promotion, and a club change are employment changes. They can happen in the offseason at week 0 of the new season, or inside a season at a caller-supplied week. They do not rewrite the previous row's scheme or role.

## Future service boundaries

These services are specified here and not implemented. They follow the player lifecycle rules: one call is one transaction; a failure raises and rolls back; instance `save()` and, for the one guarded early rating, instance `delete()`; status and employment change together inside `deferred_lifecycle()`; a nested call joins the open deferred block; an exact committed match returns before source-state checks and before the active-timeline and completed-phase guards; a partial or conflicting row raises and writes nothing. A completed or archived timeline cannot gain, change, or lose staff rows. A retry that already matches writes nothing, including on a closed timeline.

The caller supplies the person, the chair, the scheme, the week, and any ability delta. The service does not choose who is hired, who is fired, how a rating moves, or when a season ends.

| Service | Writes | Notes |
|---|---|---|
| `initialize_opening_staff` | Overlays, initial season ratings, and open employment for every opening assignment. Available overlays for every other start-year GM and coach | One transaction for the cohort. One disagreement rolls the cohort back. An exact copy is a no-write success. Does not read or write `OpeningStaffAssignment` except as a template. Does not create `OwnerDirective` or set the phase |
| `materialize_staff_class` | Overlays and initial ratings for one debut year | No employment. Does not overwrite a rating. An overlay without that year's rating is a partial state and fails. A person who is already employed is left alone |
| `hire_staff` | Open employment, status `employed` | Source is `available`, or `retired` only when the caller is allowed to reactivate in the same call. The chair must be vacant or this call is only the person side; the franchise chair key rejects a second open chair |
| `fire_staff` | Close with `fired` or `resigned`, status `available` | The caller names which reason. The person becomes available, not retired |
| `reassign_staff` | Close and open | Used for `promoted`, `reassigned`, `hired_elsewhere`, and `philosophy`. The reason must match the change. A philosophy close keeps the chair and franchise and changes scheme. A promotion changes the chair |
| `record_staff_development` | Events and the live season rating | Replay clamps after every delta. An exact stored event is a no-write success. A conflicting payload fails before any write |
| `advance_staff_season` | Retire the named people, copy frozen ratings, leave open jobs open | Does not create the next `TimelineSeason` and does not set phase. Next-year-only debuts are not copied and are not a missing-rating failure. One disagreement rolls the handoff back |
| `retire_staff` | Close with `retired`, set status and `retired_year` | May delete one unmodified next rating under the guard above. Does not invent a replacement hire |
| `reactivate_staff` | Clear `retired_year`, set `available` | Does not open a job. A hire can follow. If the game forbids a return, callers do not use this service. The closed retirement remains |
| `verify_staff_replay` | Nothing | Read-only. A closed timeline does not block it |

Expansion vacancies are `hire_staff` calls. There is no second hiring engine inside the staff service. `gm_hiring_rule` and `hc_hiring_rule` are not parsed. A later expansion service chooses candidates and then calls `hire_staff` inside whatever transaction must also record the expansion roster. This layer only refuses an illegal chair, a missing person, a filled chair, or a broken retry.

A later AI decision service, not designed here, would read the employed staff's frozen-or-live season abilities and, for a franchise with a playable human, the locked `OwnerDirective`. It would then call the player lifecycle and, when those systems exist, contract, draft, and simulation services. It would not write shared people and would not edit the directive.

Replay and idempotency match the player services closely enough that the implementation can share the same transaction habits. They should not share tables. A staff delta must not be writable through `record_development` on a player.

## Alternatives considered

| Alternative | Why it is not the recommendation |
|---|---|
| One mutable `GMCurrentRating` or `CoachCurrentRating` | The old model sketch. It cannot answer what the GM was in 1978 after 1984 development. Players already rejected a single current row |
| One person table for GMs and coaches | Fights the merged schema. The game does not need one human to be both |
| A timeline position-coach pool | Cost without an owner decision or a distinct ability. Historical posts already cover the biography |
| An `unemployed` employment row | A vacancy would be a fake job. Available is the absence of an open row, which is how the 76 unhired GMs work |
| One employment row per season | Tens of thousands of identical copies. The ability row is already per season. The job changes rarely |
| Win-loss columns on timeline employment | They would drift from games. Archive posts keep counters because the archive has no game table |
| A synthetic staff row for the owner | Breaks identity, debut year, and cross-timeline comparison. The directive is the owner's voice |
| Normalized ability rows | The same cost already rejected for the 33 player components. Staff abilities are a short fixed list |
| A `StaffClass` table | `debut_year` already groups the cohort. There is no draft-round arithmetic to store |
| Rewriting `EraRuleProfile` or the 1987 and 2004 coach checks in this phase | Not required to store careers. Scheme legality across eras is unresolved rather than silently migrated |
| Scout-component tables now | Display. The player tables deferred them. The true vector is the season rating |

## Unresolved decisions

These are game rules. The schema above does not pick them.

1. Whether a retired coach or GM may return, and after what age, cooldown, or reputation, if any.
2. Whether firing or losing a head coach also closes that club's coordinator jobs.
3. Whether a club may enter a simulated week without a GM or without a head coach. The schema allows the vacancy.
4. Whether a timeline may install `west_coast` or `air_raid` on a coach whose shared preferred scheme was not allowed to be that label. The shared debut checks stay either way.
5. Whether era profiles should gain an explicit list of legal schemes and chairs. Closed jobs would still keep what they stored.
6. The development and decline formulas: age curve, wins, firing, mentor, and scheme change. Callers pass deltas until those formulas exist.
7. What the owner sees instead of the raw abilities, and how a public grade is computed. No reveal table is proposed yet.
8. Whether the first playable version even offers a coordinator hiring screen. The rows are still initialized.
9. A mandatory retirement age, if any. Birth year supports the check. No age is stored as a cutoff.
10. How expansion prose (`gm_hiring_rule`, `hc_hiring_rule`) becomes a choice. The career service only records the choice.
11. Which inputs name coach of the year and executive of the year. The honor row is the record, not the vote.
12. Whether a mid-season philosophy change is allowed, or only an offseason one. Weeks can store either. The product rule is open.

## Staged implementation sequence

Each stage is a later change. This document is stage 0.

1. **Timeline staff tables only.** Add `TimelineStaff`, the two season-rating models, `StaffEmployment`, `StaffDevelopmentEvent`, and `TimelineStaffHonor`, with the constraints and indexes above. Generate the migration. Do not run it. Do not change universe models, `OwnerDirective`, or player services. Do not seed rows.
2. **Career services.** Implement the service table in this document: initialize, materialize, hire, fire, reassign, development, advance, retire, reactivate, and replay. Atomic, retry-safe, and free of hiring logic. No AI, no UI.
3. **Decision and presentation.** A later service reads abilities and directives to choose roster, draft, and game behavior, and decides what the owner can see. Contracts, transactions, schedules, standings, and simulation join there. They are not stage 1 or stage 2.

Stage 1 should stop if a merged constraint makes the model illegal. The likely sharp edges are MySQL null uniqueness, generated open keys, and `reports_to` during timeline deletion. Those are called out above so the implementation can follow them instead of inventing a second employment history.

## Confirmation

This phase changes no application code, model, migration, setting, or database state. It does not run migrations and it does not contact a database. Shared staff rows remain the ones already merged. Campaign careers remain unbuilt until a later stage implements this proposal.
