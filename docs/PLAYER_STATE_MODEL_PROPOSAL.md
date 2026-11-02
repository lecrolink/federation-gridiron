# Federation Gridiron — Timeline player and roster state

This is a design review for the next schema slice. It does not add models, migrations, services, admin, APIs, tests, or database rows. The baseline is `main` at `b20180eb3a9fa06c72f59c17afb255e60963b3fb` ("Add Phase 4 timeline core and owner directives"). Phases 1–3 are the `universe` app. Phase 4 is the `timelines` app. Nothing in this document is migrated or deployed.

The accepted product and storage rules stay in [PLAYER_UNIVERSE_DESIGN.md](PLAYER_UNIVERSE_DESIGN.md) and [PLAYER_UNIVERSE_MODEL_PROPOSAL.md](PLAYER_UNIVERSE_MODEL_PROPOSAL.md). Where this review chooses a smaller player-and-roster slice than the phase list in the model proposal, this file is the recommendation for that slice. A later, separately approved coding phase should reconcile the phase list. This review does not edit those documents or any application code.

## Boundary

Shared player identity and the player's initial information stay on the universe release. A campaign's changing player state stays on that timeline. A second timeline points at the same people and stores its own roster and ratings.

The smallest coherent model that meets that boundary is four timeline-owned entities:

| Entity | Job |
|--------|-----|
| `TimelinePlayer` | One campaign's link to one shared `UniversePlayer`, plus the present lifecycle. |
| `PlayerSeasonRating` | The 33 rating components as they stand in one season of that campaign. |
| `RosterMembership` | Club, season, roster position, role, and availability for a span of weeks. |
| `DevelopmentEvent` | The immutable deltas that explain how a season's ratings moved. |

That set can connect a timeline to shared players, record a roster for a team and season including position, keep season-specific ratings and a development trail, represent unavailable / left the roster / retired / stopped playing, and reconstruct a past season from rows that are no longer being edited.

The following stay out of this slice. They may be ordered as later work. They are not required to define the player and roster model:

- Staff overlays, staff ratings, and scouting reports.
- Contracts, the salary cap, free-agent offers, trades, and expansion-draft execution.
- Draft slots, draft picks, and writing a round, pick, or franchise onto a shared prospect.
- The injury ledger, weekly depth charts, and special-teams roles.
- Counting statistics, awards, schedules, standings, playoff brackets, and game simulation.
- User interface, admin, APIs, generators, and deployment.

`OpeningContract` remains the shared veteran template from Phase 3. This slice does not copy it. `ExpansionPin` already points at `UniversePlayer` and stays that way. A later service can resolve a pin through `(timeline, player)` once an overlay exists. That does not require a new foreign key here.

## 1. Proposed entities

All four models live in the `timelines` app, subclass the existing `TimelineChild`, and follow the Phase 4 deletion and freeze rules. Primary keys are `BigAutoField`. Years are `PositiveSmallIntegerField` validated against the timeline and its release. They are not a hardcoded 1970–2029 check. Index names stay within Django's 30-character limit. `CheckConstraint` uses `condition=`. MySQL treats `NULL` as distinct in unique indexes and does not support partial indexes, so an open-row rule uses a stored `GeneratedField`, the same pattern as `TimelineFranchiseControl`.

`TimelineChild` already refuses new rows and field edits when the timeline is `completed` or `archived`, and it refuses instance `delete` of a child in that state. `Timeline.delete()` still cascades in SQL without calling child `delete()`. Completed-season immutability below is the same kind of application guard: `clean()` and `save()` enforce it. Queryset `update`, `bulk_update`, and direct SQL bypass it. This design adds no database triggers.

### TimelinePlayer

The overlay. One row per shared player per campaign, created when that person becomes relevant. The archive cohort never gets a row.

| | |
|--|--|
| Ownership | Timeline. |
| Foreign keys | `timeline` to `Timeline`, `CASCADE`. `player` to `UniversePlayer`, `PROTECT`. |
| Fields | `status` in `prospect`, `active`, `taxi`, `reserve`, `free_agent`, `retired`. `retired_year`, null until retirement. |
| Unique | `(timeline, player)`. |
| Database checks | `status` is one of those six values. `retired` matches a non-null `retired_year`, and any other status matches a null `retired_year`. |
| Application checks | `player.release` is `timeline.release`. `player.cohort` is `opening` or `draft`, never `archive`. `retired_year`, when set, is inside the timeline and the release, and a `TimelineSeason` exists for it. `timeline` and `player` do not change after insert. A completed or archived timeline cannot change the row. |
| Indexes | `(timeline, status)`; `(player)`. |
| Mutable | `status`, and `retired_year` written once when the player retires. |

`seasons_played` and `career_altering_injuries` are not columns. Seasons on a club can be counted from `RosterMembership`. An injury count belongs with a later injury ledger. `retired_week` is not a column. The week is the `end_week` of the membership closed with reason `retired`.

There is no second retirement table and no overall rating.

Present status follows the open membership, except for the two states that have no open club row:

| Open membership role | `TimelinePlayer.status` |
|----------------------|-------------------------|
| None, and not retired | `prospect` |
| `starter` or `backup` | `active` |
| `taxi` | `taxi` |
| `reserve` | `reserve` |
| `free_agent` | `free_agent` |
| None, after retirement | `retired` |

### PlayerSeasonRating

One row per overlay per season the campaign has entered. This is the readable rating state for that season. It uses the same 33 components already stored on `PlayerRatingProfile`, not a new scale and not an overall.

The components, in the existing order, are `speed`, `acceleration`, `agility`, `strength`, `stamina`, `durability`, `awareness`, `discipline`, `processing`, `throw_power`, `throw_accuracy`, `catching`, `route_running`, `carrying`, `elusiveness`, `run_blocking`, `pass_blocking`, `tackling`, `coverage`, `pass_rush`, `run_defense`, `kick_power`, `kick_accuracy`, `punt_power`, `punt_accuracy`, `return_vision`, `special_teams`, `work_ethic`, `leadership`, `temperament`, `clutch`, `consistency`, and `adaptability`.

Each component is an explicit `current_<component>` `PositiveSmallIntegerField`. The prefix matches `initial_*` and `potential_*` on the shared profile. "Current" here means the value during this season, and the season-end value once the season is complete. It does not mean a single mutable row for the whole career.

| | |
|--|--|
| Ownership | Timeline. |
| Foreign keys | `overlay` to `TimelinePlayer`, `CASCADE`. `season` to `TimelineSeason`, `CASCADE`. |
| Unique | `(overlay, season)`. |
| Database checks | Each `current_*` is at most 100. `PositiveSmallIntegerField` already excludes negatives. |
| Application checks | The overlay and the season belong to the same timeline. Each `current_*` is at most that player's shared `potential_*`. The link and the season do not change after insert. While `TimelineSeason.phase` is `complete`, the 33 values do not change. A completed or archived timeline cannot change the row. |
| Indexes | `(season)`; the unique key covers `(overlay, season)`. |
| Rows | One per relevant player per season the timeline has actually entered. A new timeline does not precreate later seasons. |

`current_* <= potential_*` crosses the timeline table and `PlayerRatingProfile`, so it stays in `full_clean()`, the same split the model proposal already uses. The shared profile already checks `initial_* <= potential_*` and both ends of the 0–100 range.

The first rating row for an overlay is copied from `initial_*`. A later season's row is copied from the previous season's completed row when that `TimelineSeason` is created. Development during the season updates this row. See section 3.

### RosterMembership

Where the player is for a team and a season, including the position he is playing. A season is one `TimelineSeason`. A mid-season club change or position change closes one row and opens another. The closed row keeps the old club and position.

| | |
|--|--|
| Ownership | Timeline. |
| Foreign keys | `overlay` to `TimelinePlayer`, `CASCADE`. `season` to `TimelineSeason`, `CASCADE`. `franchise` to `UniverseFranchise`, `PROTECT`, null for a free agent. |
| Fields | `position` in the existing 19-value `POSITIONS` list (`QB`, `HB`, `FB`, `WR`, `TE`, `LT`, `LG`, `C`, `RG`, `RT`, `DE`, `DT`, `ILB`, `OLB`, `CB`, `FS`, `SS`, `K`, `P`). `role` in `starter`, `backup`, `taxi`, `reserve`, `free_agent`. `availability` in `available`, `unavailable`. `start_week` and `end_week`. `reason_ended`, null while open. `open_overlay_key`, a stored generated column. |
| Unique | `open_overlay_key`. |
| Database checks | `position` is one of the 19. `role` and `availability` are in their lists. `start_week` is at least 0. `end_week` is null or at least `start_week`. `reason_ended` is null exactly when `end_week` is null, and otherwise is one of `cut`, `transaction`, `expansion`, `retired`, `season_complete`. `free_agent` matches a null franchise. Any other role matches a non-null franchise. |
| Application checks | The overlay and the season belong to the same timeline. The franchise, when set, belongs to the release and has a `FranchiseSeasonMembership` for that season year. `position` is the shared player's `position`, `secondary_position_1`, or `secondary_position_2`. Week ranges for one overlay do not overlap. The open row's role matches `TimelinePlayer.status` using the table above. `end_week` is not compared with `EraRuleProfile.schedule_length`. That field is games per franchise, not the highest calendar week, and a roster week may be 0 for the preseason. |
| Indexes | `(franchise, season)`; `(overlay, season)`. |
| Immutable | Overlay, season, franchise, position, role, `start_week`. |
| Mutable while open | `availability`, and then `end_week` plus `reason_ended`, written once at close. |

`open_overlay_key` is `overlay_id` while `end_week` is null and `NULL` when the membership is closed. The unique constraint allows many closed rows and one open row per overlay. MySQL would not enforce that with a nullable end week alone.

`starter`, `backup`, `taxi`, and `free_agent` match `OpeningAssignment` roles. `reserve` is timeline-only. Opening assignments do not use it. Depth order is not a column. `OpeningAssignment.depth` stays on the shared template. Weekly depth is a later depth-chart model.

`unavailable` does not remove the player and does not close the membership. Leaving a roster closes it. Retirement closes it with `retired` and sets the overlay. A player who stops playing is `retired`. A player who leaves a club and can still be signed is `free_agent`, with a null-franchise membership if he is still in the league that season.

`transaction` is the roster result of moving from one club to another inside the campaign. The agreement that caused the move is a later model. This phase can still record the close and the new membership. `expansion` is the roster result of an expansion selection, not the expansion event itself. `cut` is removal from a club. `season_complete` closes a membership at the end of the season so the next season can insert its own row without rewriting this one.

### DevelopmentEvent

The trail for one component move. The season rating row is the value a reader uses. The event explains the path and supports a mid-season reconstruction. The owner is not shown the delta or the 33 components.

| | |
|--|--|
| Ownership | Timeline. |
| Foreign keys | `overlay` to `TimelinePlayer`, `CASCADE`. `season` to `TimelineSeason`, `CASCADE`. |
| Fields | `component` `CharField(32)`. `delta` `SmallIntegerField`. `reason` in `age`, `snaps`, `injury`, `coaching`. `event_index` `PositiveIntegerField`. |
| Unique | `(overlay, season, component, event_index)`. |
| Database checks | `delta` is between −20 and 20 inclusive. `component` is one of the 33 names. `reason` is one of the four. `event_index` is at least 1. |
| Application checks | The overlay and the season belong to the same timeline. The row is immutable after insert. No new event is accepted once the season phase is `complete`. A completed or archived timeline cannot gain or lose the row except by deleting the timeline. |
| Indexes | `(overlay, season)`. |
| Rows | Sparse. One row when a component actually moves. Not one row per snap, and not 33 rows when nothing changed. |

`injury` as a reason records that a rating moved because the player was hurt. It is not an injury ledger. Weeks out, body part, and a career-altering flag wait for a later model. Until then, unavailability is `RosterMembership.availability`.

### Deletion

| Action | Result |
|--------|--------|
| Delete a timeline | Cascades to its `TimelinePlayer` rows, and from each overlay to that campaign's season ratings, memberships, and development events. |
| Delete a `TimelineSeason` while the timeline is active | Cascades to that season's ratings, memberships, and development events. Instance delete is already refused when the timeline is completed or archived. |
| Delete a `TimelinePlayer` while the timeline is active | Cascades to its ratings, memberships, and events. |
| Delete a `UniversePlayer`, `UniverseFranchise`, `DraftClass`, or release that a timeline still references | `PROTECT` on the timeline-to-shared foreign keys. The shared row remains. |
| Delete a user account | No effect on these four tables. Phase 4 already uses `SET_NULL` on `Timeline.created_by` and `TimelineParticipant.user`. Player and roster rows stay. |
| Delete one timeline | Does not delete another timeline's overlays or any shared person. |

No shared person uses `CASCADE` toward a timeline row. A shared delete cannot wipe a campaign.

### What these tables do not store

No copy of name, birth, college, body, traits, `peak_age`, `development_rate`, `decline_rate`, `board_rank`, or draft-class membership. Those stay on `UniversePlayer`. No copy of `initial_*` or `potential_*`. Those stay on `PlayerRatingProfile`. No round, pick, or destination franchise on a shared prospect. No contract, salary, draft slot, trade asset, depth-chart rank, stat line, or snap log.

## 2. Shared universe and timeline-owned state

| Fact | Where it lives | Why |
|------|----------------|-----|
| Name, birth, college, handedness, height, weight, traits | `UniversePlayer` | Identical for every campaign. |
| Primary position and two secondary positions | `UniversePlayer` | The position vocabulary. A timeline chooses among these three for a membership. It does not invent a fourth or write a new primary back onto the person. |
| Cohort, draft pool, board rank, class year | `UniversePlayer` and `DraftClass` | The class is one list for every timeline. `universe_player_prospect_draft` already forces `origin_draft_round`, `origin_draft_pick`, and `origin_franchise` to stay null for the draft cohort. |
| `peak_age`, `development_rate`, `decline_rate` | `UniversePlayer` | Inputs a later development service reads. This slice does not recompute them. |
| `initial_*` and `potential_*` for the 33 components | `PlayerRatingProfile` | The starting vector and the ceiling. Potential does not move inside a campaign. |
| Pre-playable employment and counting stats | `UniverseProStint`, `UniverseProSeason` | Archive-window history. Phase 2 already rejects a draft prospect on a shared pro stint. These rows are not the campaign roster. |
| Opening club, role, and depth template | `OpeningAssignment` | The shared opening snapshot, including null-franchise free agents. A later service copies club and role into the first memberships. Depth is not copied. |
| Opening veteran money | `OpeningContract` | Out of this slice. |
| Which campaign, and whether the player is a prospect, on a roster, a free agent, or retired | `TimelinePlayer` | Diverges by campaign. |
| The 33 values in a given season of a campaign | `PlayerSeasonRating` | Starts from `initial_*` and then diverges. |
| Club, season, position played, role, availability | `RosterMembership` | Diverges by campaign. Two timelines can roster the same person on different clubs. |
| Why a component changed | `DevelopmentEvent` | Campaign history. |

Sharing rules 1, 3, 4, 5, 6, and 11 in the design document are the rules this slice implements. Rule 2's list of divergent facts is larger than this slice. Draft results, injuries, contracts, statistics, and awards remain later timeline facts. They are not columns on the shared player in the meantime.

Timeline services write timeline tables only. Nothing in this design updates `UniversePlayer`, `PlayerRatingProfile`, `OpeningAssignment`, `DraftClass`, `leagues.Team`, or a published release.

## 3. Season-to-season state and past seasons

### When rows appear

A later service creates rows. The migration recommended in section 6 inserts nothing.

| Moment | Rows |
|--------|------|
| The campaign's first `TimelineSeason` is prepared | One `TimelinePlayer` for each `OpeningAssignment` on the release. Status follows the assignment role (`starter` and `backup` become `active`). One `PlayerSeasonRating` copied from `initial_*`. One `RosterMembership` copied from the assignment: founding franchise and role for rostered players, null franchise and `free_agent` for opening free agents. `position` is the shared primary position. `start_week` is 0. The membership stays open. |
| The campaign reaches a draft class | One `TimelinePlayer` with status `prospect` for each `UniversePlayer` in that `DraftClass`, draftable and undrafted. One `PlayerSeasonRating` copied from `initial_*`. No membership. No pick fields written anywhere. |
| A new `TimelineSeason` is created after the first | For each overlay who has not retired before that season, one new `PlayerSeasonRating` copied from the previous season's completed row. Memberships are not copied automatically. The previous season's open memberships are closed with `season_complete` when that season completes. The roster service for the new season inserts the new memberships. |
| During a season | `availability` may change. A close-and-open pair records a club change, a position change, a cut, an expansion move, or a retirement. Each applied `DevelopmentEvent` updates that season's `PlayerSeasonRating` in the same service: `current = clamp(current + delta, 0, potential)`. |
| Retirement | Set `status = retired` and `retired_year` to that season. Close the open membership with `retired`. Do not insert rating rows for later seasons. The retirement season's rating row remains. |
| A player who is already retired | No further membership and no further rating row. |

The 1970 class, for a timeline that starts at the release's first season, is materialized when the opening draft becomes relevant, which the design places before the first season is simulated. Later classes are materialized when the timeline's clock reaches that class year. Creating a timeline does not insert overlays for a future class. Prospects who are not yet in the campaign have no overlay, no season rating, and no membership.

Do not precreate all 60 seasons. `TimelineSeason` is already created one year at a time. Ratings and memberships follow seasons that exist.

### What is true at each moment

The stored season rating is the live value during the season. When the season phase becomes `complete`, that same row is the season-end value and is frozen.

Opening day of a player's first overlay season is `initial_*` on `PlayerRatingProfile`, before any `DevelopmentEvent` in that season. Opening day of a later season is the previous season's frozen `PlayerSeasonRating`, before any event in the new season. A second "season start" snapshot is not stored.

Mid-season value for one component, through event N:

`prior + sum(deltas for that component with event_index <= N)`

`prior` is the previous season's frozen component, or `initial_*` when this is the overlay's first season. The service that writes an event also writes the season row, clamped at 0 and at potential. A reader who trusts the snapshot does not have to replay events. A reader who needs the value before the last event replays from `prior`.

When the season is completed, the service refuses to mark it complete unless:

`frozen current_* = prior_* + sum(that season's deltas for that component)`

for every component, after clamping. The snapshot is the authority a screen or a later kickoff payload reads. The equality check stops a missed event from becoming the next season's baseline. This check is application code. It is not a cross-row database constraint.

A past season stays interpretable because its rating row, its memberships, and its events are immutable once the season is complete. A later club, a later position, a retirement, or a later development event inserts or updates a different season's rows. It does not update the completed season. Reconstructing "what was he in season Y" is: the `PlayerSeasonRating` for that season, the `RosterMembership` rows for that season ordered by `start_week`, and, if the question is mid-season, the events with `event_index` up to the point in question.

`GameKickoffState.inputs` remains the audit copy taken at kickoff. Phase 4 already says that payload may name ratings at kickoff. This slice does not change that table. A later simulation should copy from the season rating plus events up to kickoff, not from a single career-long mutable row.

### Common annual draft class

`DraftClass` is already unique on `(release, season_year)`. Every prospect points at that class with `UniversePlayer.draft_class`, and `(draft_class, board_rank)` is unique for ranks that are set. The class counts (`draftable_count`, `undrafted_count`) are release data.

That is the common class. Timelines do not each store a second list of prospects. When a timeline reaches the class year it inserts overlays that foreign-key the same `UniversePlayer` rows. Another timeline inserts its own overlays to those same people. Standings, owner decisions, and draft order do not add or remove prospects. They do not write `origin_draft_round`, `origin_draft_pick`, or `origin_franchise`. Those destination facts, when a later phase records them, are timeline rows such as the proposal's `DraftSlot` and `TimelineDraftPick`. They point at the overlay or the shared player. They do not alter the class.

Undrafted prospects are part of the class (`draft_pool = undrafted`) and receive the same prospect overlay and initial rating copy. They do not receive a membership until a later signing service creates one. Unsigned is `prospect` with no membership, or `free_agent` with a null-franchise membership once the player is in the league and unsigned. This slice does not decide the signing transaction.

Expansion years use the same class header. `ExpansionEvent` already carries the undrafted multiplier for that year. The player-state model does not copy those counts onto the timeline.

## 4. Fit with Phases 1–4

No Phase 1–4 table changes. A later coding phase adds these four models in a new `timelines` migration that depends on `timelines.0001_initial`. That migration already depends on `universe.0003_shared_rules_and_opening`. Universe migrations `0001`, `0002`, and `0003` stay as merged.

| Existing piece | How this slice uses it |
|----------------|------------------------|
| `UniverseRelease` horizon | Season years must sit inside the release and at or after `Timeline.start_season_year`. The confirmed 1970–2029 horizon is publication data, not a new check. |
| `UniverseFranchise`, `FranchiseSeasonMembership` | A club membership must name a franchise that is active that season. Expansion clubs are not rostered before `first_active_season`. |
| `UniversePlayer`, `POSITIONS`, traits, development inputs | Identity and the allowed positions. Overlays are forbidden for `cohort = archive`. |
| `PlayerRatingProfile` | Source of the first season vector and of every potential ceiling. The 33 names are imported from `PLAYER_COMPONENTS`. No new rating system. |
| `UniverseProStint`, `UniverseProSeason` | Left as archive history. They are not opened, closed, or copied into `RosterMembership`. |
| `DraftClass` | The shared class header. Overlays point at its players. The class row is not duplicated. |
| `OpeningAssignment` | Template for the first memberships and for who is an opening free agent. `OpeningContract` is unused here. |
| `EraRuleProfile` | Not a parent of these rows. `schedule_length` is not a roster-week cap. Quota columns are a later roster-legality check, not a constraint on a single membership. |
| `Timeline`, `TimelineChild` | Campaign owner and the completed/archived freeze. |
| `TimelineSeason` | The clock. Ratings, memberships, and events require a season row. Progress flags already on that season (`directives_locked`, `regular_season_simulated`, `postseason_complete`, phase `complete`) are not redefined. Freeze of player state keys off phase `complete`. |
| `TimelineParticipant`, `TimelineFranchiseControl`, `OwnerDirective` | Unchanged. Directives may later influence how a roster service chooses roles. They are not foreign keys on the player. Bots still have no participant row. Account deletion still does not delete the campaign. |
| `TimelineExpansionChoice`, `ExpansionPin` | Unchanged. Pins remain shared-player references. This slice does not retarget them and does not execute an expansion draft. |
| `GameKickoffState`, `SimulationAuditRecord` | Unchanged. No HMAC, no simulation, and no new game rows. |

The model proposal's phase 5 currently bundles `TimelinePlayer` and `PlayerCurrentRating` with staff overlays and scouting, puts `RosterStint` in phase 6, and puts `DevelopmentEvent` in phase 7. This review does not implement that bundle.

The change from `PlayerCurrentRating` is deliberate. A single mutable current row cannot answer "what was he in a completed season" after the next offseason overwrites it. `PlayerSeasonRating` is that answer. `DevelopmentEvent` moves into this slice because a season snapshot without the deltas cannot answer a mid-season question, and a delta log without the snapshot makes every later year depend on a complete replay. Staff, scouting, contracts, the injury ledger, depth charts, and transactions stay later. The coding phase should update the phase list so those later phases are not specified twice.

`RosterMembership` is the roster grain the design called `RosterStint`, narrowed to one season per row and given an explicit position. A stint that spans years and stores a mutable role would lose the previous season's position the moment the role changed. Per-season rows match `TimelineSeason` and the reconstruction requirement. The name is `RosterMembership` so it is not mistaken for the multi-year stint in the older proposal text.

## 5. Alternatives, unresolved decisions, and risks

### Alternatives considered

| Alternative | Why it is not the recommendation |
|-------------|------------------------------|
| `PlayerCurrentRating` as the only rating store, with events as a log | Matches the older proposal, and it is the right shape for "what is he now." It is the wrong shape for a past season. The live row is overwritten. |
| Events only, no season snapshot | A missing or edited event changes every later year. Reconstruction always replays the career. The snapshot is the value the next season copies. |
| Entity-attribute-value ratings | Already rejected for the 33 components. One row of explicit columns is the stored match vector. Scout readings, which are not in this slice, remain the only planned normalized component rows. |
| Copying `UniversePlayer` into each timeline | A second campaign would duplicate biography or collide with the first campaign's ratings. |
| A `current_franchise` column on `TimelinePlayer` | One club, no history, no position, no free-agent span. |
| A membership that spans years | The past season's club and position are no longer a frozen row. |
| Weekly depth-chart rows as the roster | That is a coach ordering, and the volume is games times active players. Membership answers team, season, position, and role without storing every week. |
| Putting staff overlays and scouting in this slice | The owner-facing boundary for this phase is players and rosters. Staff is a parallel overlay with a different rating list. |
| Putting contracts, trades, and the draft board in this slice | Those explain why a membership changed. The membership can record the result with `cut`, `transaction`, or `expansion` before those agreements exist. |
| An injury table in this slice | Availability plus a development reason of `injury` covers "he cannot play" and "a rating moved." Diagnosis, weeks out, and career-altering flags need their own model later. |
| Precreating 60 seasons of ratings at timeline creation | Violates the rule that future classes and future seasons are not materialized early, and it fills ratings for years the campaign may never play. |

### Unresolved decisions

1. **Live value versus a stored opening-day copy.** The recommendation is one row per season: live during the season, frozen as the ending value when the season completes. Opening day is the previous frozen row, or `initial_*`. A reviewer could instead require two snapshots. That doubles the rating rows and is not required to reconstruct either moment.
2. **Opening free agents.** The recommendation is a null-franchise `free_agent` membership, matching `OpeningAssignment`. The alternative is an overlay with no membership until a club signs him. The membership makes the opening pool visible as roster state.
3. **`seasons_played`.** The recommendation is to derive it from closed non-free-agent memberships, not to store it. A cache can be added later if a screen needs it on every row.
4. **Close reasons.** The recommended set is `cut`, `transaction`, `expansion`, `retired`, and `season_complete`. `released` is omitted because it duplicates `cut`. `departed` is omitted because a player who stops playing is `retired`. A later trade model may want a more specific reason than `transaction`. Adding a value then is a constraint change, not a new entity.
5. **Position change inside a club.** The recommendation is a new membership, so the old position remains on the closed row. The alternative is one membership per club-season and a separate position history. That is a fifth entity for a fact the membership already has room to store.
6. **Status kept equal to the open role.** The recommendation is the application invariant in section 1. The alternative is to treat `status` as a coarser cache that may lag the membership. One source of "where is he now" is easier to read.
7. **Offseason retirement before the next season exists.** `retired_year` is the season in which he last had a roster membership. If he retires in the offseason after that season is complete, the completed rating stays frozen and no next-season rating is created. If the next season's rating was already copied forward, that row remains and the membership for the new season is closed with `retired` without a game being played. The service must not delete the copied rating just because he never played. This edge should be settled before the season-entry service is written. It does not change the columns.
8. **Roster week 0.** The recommendation allows `start_week >= 0` for the preseason and does not cap the week at `schedule_length`. Game weeks on `GameKickoffState` stay at least 1. If reviewers want roster weeks to start at 1, only the check changes.
9. **Whether `reserve` belongs before roster cuts exist.** It is in the design's status list and is cheap to allow. Opening copy never writes it. It can wait if this slice should only carry roles that `OpeningAssignment` already has, plus free agency after a cut.

### Risks

- A service that updates `PlayerSeasonRating` without a `DevelopmentEvent`, or the reverse, breaks the season-end equality check and would poison the next season's copy. The coding phase should make one write path for both.
- A service that writes a developed value back onto `PlayerRatingProfile.initial_*` or `potential_*` would change every timeline on the release. Review of the later service should treat any universe write as a defect.
- Completed-season freeze and published-release freeze are application checks. Bulk updates bypass them. The same residual risk is already accepted for Phases 1–4.
- `transaction` and `expansion` record a roster result without the agreement. A later trade or expansion-pick table must close and open memberships in the same operation as the agreement, or the roster and the deal can diverge.
- Two memberships in one season are legal. Overlap is application-validated, not a database exclusion constraint. MySQL will not catch an overlapping pair inserted with `bulk_create` unless that path calls `full_clean()`.
- Index names over 30 characters fail `models.E034`. The coding phase should name indexes with a short `tl_` prefix and check them before the migration is considered done.
- `PlayerSeasonRating` is 33 small integers per player-season. That is the intended vector. It is much smaller than copying players, and it is larger than one career-long row. The growth is one row per relevant player per season actually entered, not 60 years up front.
- Leaving `ExpansionPin` on `UniversePlayer` means a pin can exist before the overlay. That is already true in Phase 4. The risk is a later feature assuming every pin has an overlay. The lookup is `(timeline, player)`, and it may be empty until the class or the opening cohort is materialized.

## 6. Implementation sequence

This sequence is for a later coding phase that has its own approval. It is not part of the current change.

1. Add `TimelinePlayer`, `PlayerSeasonRating`, `RosterMembership`, and `DevelopmentEvent` to `timelines/models.py`. Subclass `TimelineChild`. Import `PLAYER_COMPONENTS` and `POSITIONS` from `universe.people`. Do not edit universe models.
2. Add `timelines/migrations/0002_player_state.py`, depending on `timelines.0001_initial`. No `RunPython`, no `RunSQL`, and no inserted rows. Do not edit `0001` or any universe migration. Keep index names at or under 30 characters. Keep franchise nullable. Do not add a database default that would invent a shared player or a season.
3. Enforce the application checks in section 1, including the same-timeline rule, the archive exclusion, the potential ceiling, an active franchise-season, non-overlapping weeks, status aligned with the open role, retirement closing the open membership, and the freeze once `TimelineSeason.phase` is `complete`.
4. Run the same kind of nonmutating checks used for Phase 4: an isolated `manage.py check` against an in-memory database, and `makemigrations --check --dry-run`. Do not run `migrate` against the project database and do not contact production.
5. Reconcile [PLAYER_UNIVERSE_MODEL_PROPOSAL.md](PLAYER_UNIVERSE_MODEL_PROPOSAL.md) so phase 5 is this player-and-roster slice, `PlayerCurrentRating` is not a second authority, and staff, scouting, `RosterStint`, contracts, and `DevelopmentEvent` are not specified twice.
6. Leave admin, APIs, generators, seed data, and the production database untouched in that coding phase as well, unless its own approval expands the scope.

Later work, still not part of that coding phase, in an order that keeps each step reviewable:

1. Staff overlays and scouting, on the staff rating lists that already exist. The simulator would read staff current ratings, not scout rows.
2. Contracts, copied from `OpeningContract` only when that phase says so.
3. Draft slots and timeline draft picks. They reference the overlay and still do not write a destination onto `UniversePlayer`.
4. Trades, free-agent offers, and expansion-draft results, which close and open `RosterMembership` rows.
5. The injury ledger, then depth charts and special teams.
6. Schedules, standings, counting stats, and simulation. Simulation writes `DevelopmentEvent` and, at kickoff, copies the reconstructed ratings into `GameKickoffState.inputs`.

## Confirmation

This document is the only intended change on the design branch. It does not modify application code, models, migrations, settings, or database state.
