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
| Application checks | `player.release` is `timeline.release`. `player.cohort` is `opening` or `draft`, never `archive`. `retired_year`, when set, is inside the timeline and the release, and a `TimelineSeason` exists for it. `status` matches the lifecycle table in this section, including zero memberships for a prospect and no open membership after retirement. No rating, membership, or development event is dated after `retired_year`. `timeline` and `player` do not change after insert. A completed or archived timeline cannot change the row. |
| Indexes | `(timeline, status)`; `(player)`. |
| Mutable | `status`, and `retired_year` written once when the player retires. |

`seasons_played` is derived and is not stored. It is the number of distinct seasons in which the overlay has at least one `RosterMembership` with a non-null franchise. A season spent only on a null-franchise free-agent row does not count. A season that contains both a club row and a free-agent row counts once. Open and closed rows both count. A prospect season has no membership and does not count. The retirement season counts when that season has a club membership. `career_altering_injuries` is not a column. An injury count belongs with a later injury ledger. `retired_week` is not a column. The week is the `end_week` of the membership closed with reason `retired`.

There is no second retirement table and no overall rating.

#### Lifecycle authority

The open `RosterMembership` is authoritative for where the player is: club, position, role, availability, and weeks. `TimelinePlayer.status` is the lifecycle value stored on the overlay so a reader can find prospects and retirees without scanning memberships. It must match the open membership. It is not a second source and it is not allowed to lag behind that row.

One service writes the membership change and the status change in the same database transaction. `full_clean()` on both models rejects a save that would leave them inconsistent. `open_overlay_key` already allows only one open membership per overlay.

| Present roster shape | `status` | `retired_year` |
|----------------------|----------|----------------|
| Zero membership rows, `retired_year` null | `prospect` | null |
| One open row, role `starter` or `backup`, franchise set | `active` | null |
| One open row, role `taxi`, franchise set | `taxi` | null |
| One open row, role `reserve`, franchise set | `reserve` | null |
| One open row, role `free_agent`, franchise null | `free_agent` | null |
| Zero open rows, retirement recorded | `retired` | the retirement season |

A player who has left a club and remains in the league is a free-agent row. He is not an overlay with no membership. The three shapes that are easy to confuse are different in the schema:

- **Free agent, including every opening free agent.** A `RosterMembership` row exists. `franchise` is null. `role` is `free_agent`. The database check `tl_membership_free_agent_franchise` is an if-and-only-if: `free_agent` exactly when `franchise` is null, and any other role exactly when `franchise` is set. The opening free-agent pool is the open rows in that shape copied from `OpeningAssignment`. It is not the set of overlays that lack a club.
- **Prospect.** `status` is `prospect`, `retired_year` is null, and the overlay has zero `RosterMembership` rows of any kind. A `PlayerSeasonRating` may exist. A null franchise is not used in place of the missing row.
- **Retired.** `status` is `retired`, `retired_year` is set, and every membership is closed. At least one closed row exists, and the row closed in `retired_year` uses `reason_ended = retired`. Closed history remains. There is no open free-agent row. There is no rating, membership, or development event in a season after `retired_year`. Retirement is a close of an open membership, so an unsigned prospect is not marked retired by leaving his membership count at zero. He stays `prospect` until a roster row exists to close.

`status` stays aligned through the offseason. Completing a season does not delete the open membership and does not turn a veteran into a prospect. The next season's roster write closes the previous membership and opens the new one in the same transaction. A player who is still in the league therefore always has exactly one open membership. Section 3 lists each transition.

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

There is exactly one `PlayerSeasonRating` per overlay per season. That row is the live rating while the season is in progress. When `TimelineSeason.phase` becomes `complete`, the same row is the frozen season-end rating. Creating the next season's rating row copies that frozen row. There is no separate opening-day rating row. Opening day of the overlay's first season is the shared `initial_*` vector, read from `PlayerRatingProfile` before any `DevelopmentEvent` in that season. Opening day of a later season is the previous season's frozen row, before any event in the new season.

A retired player has no `PlayerSeasonRating` for a season after `retired_year`. The copy step skips every overlay with `status = retired`. If a later season's row was already created and the player retires in the offseason before that season, the retirement service removes that unplayed row, as section 3 requires. The retirement season's own rating row stays.

### RosterMembership

Where the player is for a team and a season, including the position he is playing. A season is one `TimelineSeason`. A new club, a new position, or a new role closes one row and opens another. A position change opens a new row even when the player stays with the same club. `position` and `role` are immutable, so the closed row keeps the previous club, position, and role.

| | |
|--|--|
| Ownership | Timeline. |
| Foreign keys | `overlay` to `TimelinePlayer`, `CASCADE`. `season` to `TimelineSeason`, `CASCADE`. `franchise` to `UniverseFranchise`, `PROTECT`, null for a free agent. |
| Fields | `position` in the existing 19-value `POSITIONS` list (`QB`, `HB`, `FB`, `WR`, `TE`, `LT`, `LG`, `C`, `RG`, `RT`, `DE`, `DT`, `ILB`, `OLB`, `CB`, `FS`, `SS`, `K`, `P`). `role` in `starter`, `backup`, `taxi`, `reserve`, `free_agent`. `availability` in `available`, `unavailable`. `start_week` and `end_week`. `reason_ended`, null while open. `open_overlay_key`, a stored generated column. |
| Unique | `open_overlay_key`. |
| Database checks | `position` is one of the 19. `role` and `availability` are in their lists. `start_week` is at least 0. `end_week` is null or at least `start_week`. `reason_ended` is null exactly when `end_week` is null, and otherwise is one of `cut`, `transaction`, `expansion`, `retired`, `season_complete`. Check `tl_membership_free_agent_franchise`: `role` is `free_agent` if and only if `franchise` is null. |
| Application checks | The overlay and the season belong to the same timeline. The franchise, when set, belongs to the release and has a `FranchiseSeasonMembership` for that season year. `position` is the shared player's `position`, `secondary_position_1`, or `secondary_position_2`. Week ranges for one overlay do not overlap, except the same-season boundary week described with the close reasons. The open row and `TimelinePlayer.status` match the lifecycle table above. A `prospect` overlay has zero membership rows. A `retired` overlay has zero open memberships, at least one closed membership, and a `retired` close in `retired_year`. A `free_agent` overlay has exactly one open null-franchise row. `role = reserve` is rejected while that season's phase is `choose_franchise`, `staff`, `direction`, `draft`, or `instructions`. After `TimelineSeason.phase` is `complete`, club, position, role, `start_week`, and `availability` stay fixed. The only later write on that season's row is filling `end_week` and `reason_ended` once, as the handoff or the retirement close. `end_week` is not compared with `EraRuleProfile.schedule_length`. |
| Indexes | `(franchise, season)`; `(overlay, season)`. |
| Immutable | Overlay, season, franchise, position, role, `start_week`. |
| Mutable while open | `availability`, until the season phase is `complete`. Then `end_week` and `reason_ended`, written once at close. |

`open_overlay_key` is `overlay_id` while `end_week` is null and `NULL` when the membership is closed. The unique constraint allows many closed rows and one open row per overlay. MySQL would not enforce that with a nullable end week alone.

`starter`, `backup`, `taxi`, and `free_agent` match `OpeningAssignment` roles. Depth order is not a column. `OpeningAssignment.depth` stays on the shared template. Weekly depth is a later depth-chart model.

#### Roster weeks

Roster weeks start at 0. Week 0 is the preseason, the same label the design uses for a roster span that begins before the regular-season calendar. `start_week >= 0` is the database rule. There is no upper bound taken from `schedule_length`, because that field is games per franchise and a season can contain a bye week beyond that count. `GameKickoffState.week` stays a game week of at least 1. The opening membership uses `start_week` 0 so the camp roster and the week-1 roster are one row until the club, position, or role actually changes.

#### Reserve

`reserve` is part of this schema. The design already lists it beside `starter`, `backup`, `taxi`, and `free_agent`, and the era profiles already treat injured reserve as outside the active-roster limit. Opening assignments do not use it, and the copy from `OpeningAssignment` writes only `starter`, `backup`, `taxi`, and `free_agent`. A membership may use `reserve` once that season's phase is `roster_cut` or later (`regular`, `playoffs`, `offseason`, `complete`). It is rejected in `choose_franchise`, `staff`, `direction`, `draft`, and `instructions`. `instructions` sits before `roster_cut` on the Phase 4 phase list, so a reserve row is not legal while the owner is still confirming instructions. The value is therefore legal as soon as a roster cut could place a player there, and the enum does not wait for a later migration. This slice still does not implement the cut service, the injured-reserve stay, or the return limits on `EraRuleProfile`.

#### Availability and close reasons

`unavailable` does not remove the player and does not close the membership. `availability` is the only field on an open row that changes without a new row. A player who stops playing is `retired`. A player who leaves a club and can still be signed becomes a free-agent row in the same transaction.

These five values are the complete set of `reason_ended`. Each one is the roster result, not the contract, trade, or waiver that caused it. Those agreements stay out of this slice.

| Reason | When it is written | What the same transaction does |
|--------|--------------------|--------------------------------|
| `cut` | The player is removed from a club and remains in the league unsigned. | Close the club row. Open a `free_agent` row with a null franchise in the same season. Set `status` to `free_agent`. |
| `transaction` | One open membership is replaced by another and the player remains in the league, other than a cut to free agency or an expansion move. | Close the old row. Open the new row. Set `status` from the new role. |
| `expansion` | An expansion selection moves the player from one club to a new club. | Close the old club row. Open the new club row. Set `status` from the new role. |
| `retired` | The player stops playing. | Close the open row, whether it is a club row or a free-agent row. Set `status` to `retired` and set `retired_year` to that membership's season. Do not open a replacement row. |
| `season_complete` | The player continues, and the next season's membership is being created. | Close the prior season's still-open row. Open the new season's row. Set `status` from the new role. |

`transaction` includes a move from one club to another, a signing from a free-agent row onto a club, a same-club position change, and a same-club role change among `starter`, `backup`, `taxi`, and `reserve`. The position change uses a new row even when `franchise` is unchanged, because `position` is immutable. A role change uses a new row for the same reason. The later trade, waiver, or signing record is what will explain the cause. This reason does not stand in for a cut: a cut always leaves a null-franchise row behind, and a direct club-to-club move does not.

`expansion` is only the expansion selection. An ordinary cut in an expansion year stays `cut`. The expansion-draft service is later work. The reason is in the enum so that service has a legal close value.

A replacement in the same season may start on the closed row's `end_week`. Inclusive ranges otherwise share no week. That boundary week belongs to the new row. The closed row records the assignment that ended as the week began. A next-season row starts at week 0 of the new season, so it does not share a week with the previous season.

The five reasons cover every way a membership row ends. They do not each name a status value. These lifecycle steps have no close reason, because they do not end a row:

- Materializing a prospect inserts an overlay and a rating and no membership.
- The first club row for a prospect inserts a membership and sets `status` from its role. There is no previous row to close.
- Changing `availability` updates the open row.
- Reading `seasons_played` only counts closed and open club rows. It writes nothing.

No other close reason is in this slice. `released` would duplicate `cut`. `departed` would duplicate `retired`. `traded`, `waived`, and `signed` would name agreements this slice does not store. A later phase that needs a more specific reason has to extend the check. Until then, `transaction` is the roster result for every replacement except a cut, an expansion move, a retirement, and the season handoff.

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
| Which campaign, and the lifecycle value `prospect`, `active`, `taxi`, `reserve`, `free_agent`, or `retired` | `TimelinePlayer` | Diverges by campaign. The open `RosterMembership` is authoritative for a player who has one. `status` is stored so it can be checked against that row. |
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
| The campaign's first `TimelineSeason` is prepared | One `TimelinePlayer` for each `OpeningAssignment` on the release. One `PlayerSeasonRating` copied from `initial_*`. One `RosterMembership` copied from the assignment. A rostered player gets the founding franchise and the assignment role (`starter` or `backup` sets `status` to `active`; `taxi` sets `taxi`). An opening free agent gets `franchise` null, `role` `free_agent`, and `status` `free_agent` in that same write. He is not inserted as a prospect. `position` is the shared primary position. `start_week` is 0. The membership stays open. The copy never writes `reserve`. |
| The campaign reaches a draft class | One `TimelinePlayer` with `status` `prospect` for each `UniversePlayer` in that `DraftClass`, draftable and undrafted. One `PlayerSeasonRating` copied from `initial_*`. Zero membership rows. No pick fields written anywhere. |
| A new `TimelineSeason` is created after the first | For each overlay who is not retired, one new `PlayerSeasonRating` copied from the previous season's frozen row. No second snapshot is stored. The copy skips `status = retired` and skips any overlay whose `retired_year` is already the previous season. Memberships are not copied by the season insert. |
| The next season's roster is written | For each player who continues, the same transaction closes the still-open membership with `season_complete` and opens that player's new-season membership. `status` is set from the new role. For each player who retires instead of continuing, the same transaction uses the retirement path below and does not open a new membership or leave a new rating row. |
| During a season | `availability` may change on the open row. A position change, including a change that keeps the same club, closes the old row with `transaction` and opens a new row. The same close-and-open path covers a role change, a club change, a signing, a cut, and an expansion move, using the reason in the table in section 1. Each applied `DevelopmentEvent` updates that season's single `PlayerSeasonRating` in the same service: `current = clamp(current + delta, 0, potential)`. |
| Retirement | Set `status` to `retired` and `retired_year` to the season of the membership being closed. Close that open membership with `retired`. Do not open another membership. Do not insert a rating row for any later season. The retirement season's rating row remains. |
| A player who is already retired | No further membership, no further rating row, and no further development event. |

The 1970 class, for a timeline that starts at the release's first season, is materialized when the opening draft becomes relevant, which the design places before the first season is simulated. Later classes are materialized when the timeline's clock reaches that class year. Creating a timeline does not insert overlays for a future class. Prospects who are not yet in the campaign have no overlay, no season rating, and no membership.

Do not precreate all 60 seasons. `TimelineSeason` is already created one year at a time. Ratings and memberships follow seasons that exist.

### What is true at each moment

The one stored season rating is the live value during the season. When the season phase becomes `complete`, that same row is the season-end value and is frozen. The next season copies it. A second opening-day row is not part of the model.

Opening day of a player's first overlay season is `initial_*` on `PlayerRatingProfile`, before any `DevelopmentEvent` in that season. Opening day of a later season is the previous season's frozen `PlayerSeasonRating`, before any event in the new season.

Mid-season value for one component, through event N, is the replay from `prior` through every event for that component with `event_index` less than or equal to N, clamping to 0 and to potential after each delta.

`prior` is the previous season's frozen component, or `initial_*` when this is the overlay's first season. The service that writes an event also writes the season row with that clamped result. A reader who trusts the snapshot does not have to replay events. A reader who needs the value before the last event replays from `prior`.

When the season is completed, the service refuses to mark it complete unless the frozen `current_*` equals a replay of that season's events. The replay starts from `prior` and applies that component's events in `event_index` order, clamping to 0 and to potential after each delta. That is the same clamp the writer applies when it stores the event. A single clamp of the summed deltas is not the check: an intermediate clamp does not equal a clamp of the total. The snapshot is the authority a screen or a later kickoff payload reads. The replay stops a missed event from becoming the next season's baseline. This check is application code. It is not a cross-row database constraint.

A past season stays interpretable because its rating row and its development events are immutable once the season is complete, and each membership row keeps the club, position, and role it was opened with. The one later edit allowed on a completed season's membership is the handoff close: `end_week` and `reason_ended` may be filled, once, when the next roster transaction or the retirement transaction closes that still-open row. Club, position, role, `start_week`, and `availability` stay as they were. Reconstructing "what was he in season Y" is: the `PlayerSeasonRating` for that season, the `RosterMembership` rows for that season ordered by `start_week`, and, if the question is mid-season, the events with `event_index` up to the point in question.

#### Offseason retirement

`retired_year` is the season of the last membership the player held. A player who retires in the offseason, after that season is complete and before the next season is played, retires from that completed season. He must not have a `PlayerSeasonRating`, a `RosterMembership`, or a `DevelopmentEvent` for any later season.

The normal order avoids a stray row. Record the retirement before the next season's rating copy. The copy then skips him. His completed rating stays frozen. His still-open membership closes with `retired`, not with `season_complete`. No next-season membership is opened.

If the next season's rating row was created first, the retirement service removes it in the same transaction as the retirement, under all of these guards:

- That later `TimelineSeason.phase` is not `complete`.
- The overlay has no `DevelopmentEvent` for that later season.
- The overlay has no `RosterMembership` for that later season.
- The row's 33 values are still the unmodified copy of the previous frozen season.

When those guards pass, the service deletes that later rating row, closes the previous membership with `retired`, and sets `status` and `retired_year`. The retirement season's rating is not deleted. A later-season membership, or any development event in that later season, means the player has already entered the new season. Retirement then uses `retired_year` equal to that new season and keeps that season's rating. The delete-the-copy path is only for an offseason retirement that raced ahead of the roster handoff.

A player who is already `retired` is excluded from every later copy. `full_clean()` rejects a rating, membership, or development event dated after `retired_year`.

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
| `TimelineSeason` | The clock. Ratings, memberships, and events require a season row. Progress flags already on that season (`directives_locked`, `regular_season_simulated`, `postseason_complete`, phase `complete`) are not redefined. Rating values and development events freeze when the phase becomes `complete`. A membership's club, position, and role freeze then too. Its close fields are filled later, in the roster handoff or the retirement write. |
| `TimelineParticipant`, `TimelineFranchiseControl`, `OwnerDirective` | Unchanged. Directives may later influence how a roster service chooses roles. They are not foreign keys on the player. Bots still have no participant row. Account deletion still does not delete the campaign. |
| `TimelineExpansionChoice`, `ExpansionPin` | Unchanged. Pins remain shared-player references. This slice does not retarget them and does not execute an expansion draft. |
| `GameKickoffState`, `SimulationAuditRecord` | Unchanged. No HMAC, no simulation, and no new game rows. |

The model proposal's phase 5 currently bundles `TimelinePlayer` and `PlayerCurrentRating` with staff overlays and scouting, puts `RosterStint` in phase 6, and puts `DevelopmentEvent` in phase 7. This review does not implement that bundle.

The change from `PlayerCurrentRating` is deliberate. A single mutable current row cannot answer "what was he in a completed season" after the next offseason overwrites it. `PlayerSeasonRating` is that answer. `DevelopmentEvent` moves into this slice because a season snapshot without the deltas cannot answer a mid-season question, and a delta log without the snapshot makes every later year depend on a complete replay. Staff, scouting, contracts, the injury ledger, depth charts, and transactions stay later. The coding phase should update the phase list so those later phases are not specified twice.

`RosterMembership` is the roster grain the design called `RosterStint`, narrowed to one season per row and given an explicit position. A stint that spans years and stores a mutable role would lose the previous season's position the moment the role changed. Per-season rows match `TimelineSeason` and the reconstruction requirement. The name is `RosterMembership` so it is not mistaken for the multi-year stint in the older proposal text.

## 5. Alternatives, settled decisions, and risks

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

### Settled decisions

These were the open points in the first draft of this review. They are now rules of the model above.

| Decision | Rule |
|----------|------|
| One rating row | One `PlayerSeasonRating` per overlay per season. It is live during the season and frozen at season end. The next season copies that frozen row. Opening day is `initial_*` or the previous frozen row. No opening-day twin is stored. |
| Opening free agents | An opening free agent is a `RosterMembership` with `franchise` null and `role` `free_agent`, written with `status` `free_agent`. A prospect has zero membership rows. A retired player has zero open memberships and a `retired_year`. `tl_membership_free_agent_franchise` keeps a null franchise from meaning anything except `free_agent`. |
| `seasons_played` | Derived. Count distinct seasons that have a membership with a non-null franchise. Do not store the count. |
| Position change | A new `RosterMembership`, even when the club stays the same. The old row closes with `transaction` because `position` is immutable. A same-club role change uses the same close. |
| Close reasons | `cut`, `transaction`, `expansion`, `retired`, and `season_complete` are the full set of ways a membership ends. Section 1 states when each applies. Prospect creation, the first roster row, and an availability change do not use a close reason. `released` and `departed` are not values. A more specific trade or waiver reason would be a later constraint change. |
| Status | The open membership is authoritative. `TimelinePlayer.status` is updated in the same transaction and must match the lifecycle table. A player who remains in the league always has exactly one open membership, including through the offseason, until the handoff closes it and opens the next row. |
| Offseason retirement | `retired_year` is the last season he held a membership. He receives no later rating row. The copy skips retirees. A next-season rating created too early is deleted only when that season is not complete, has no events, and has no membership. Once he has entered the new season, that season's rating stays and `retired_year` is that season. |
| Roster week 0 | Allowed. Week 0 is the preseason. `schedule_length` is not a roster-week cap. Game weeks on `GameKickoffState` stay at least 1. |
| `reserve` | Included now. Opening copy does not write it. It is legal from phase `roster_cut` onward. It is rejected in every earlier phase, including `draft` and `instructions`. The cut service, the injured-reserve stay, and the return limits remain later work. |

### Risks

- A service that updates `PlayerSeasonRating` without a `DevelopmentEvent`, or the reverse, breaks the season-end equality check and would poison the next season's copy. The coding phase should make one write path for both.
- A service that writes a developed value back onto `PlayerRatingProfile.initial_*` or `potential_*` would change every timeline on the release. Review of the later service should treat any universe write as a defect.
- Completed-season freeze and published-release freeze are application checks. Bulk updates bypass them. The same residual risk is already accepted for Phases 1–4.
- `transaction` and `expansion` record a roster result without the agreement. A same-club position change also closes with `transaction`, so the reason does not by itself mean a trade. A later trade or expansion-pick table must close and open memberships in the same operation as the agreement, or the roster and the deal can diverge. Adding `traded` or `waived` later is a check change, not a new entity.
- Two memberships in one season are legal. Overlap is application-validated, not a database exclusion constraint. MySQL will not catch an overlapping pair inserted with `bulk_create` unless that path calls `full_clean()`.
- Index names over 30 characters fail `models.E034`. The coding phase should name indexes with a short `tl_` prefix and check them before the migration is considered done.
- `PlayerSeasonRating` is 33 small integers per player-season. That is the intended vector. It is much smaller than copying players, and it is larger than one career-long row. The growth is one row per relevant player per season actually entered, not 60 years up front.
- Leaving `ExpansionPin` on `UniversePlayer` means a pin can exist before the overlay. That is already true in Phase 4. The risk is a later feature assuming every pin has an overlay. The lookup is `(timeline, player)`, and it may be empty until the class or the opening cohort is materialized.

## 6. Implementation sequence

This sequence is for a later coding phase that has its own approval. It is not part of the current change.

1. Add `TimelinePlayer`, `PlayerSeasonRating`, `RosterMembership`, and `DevelopmentEvent` to `timelines/models.py`. Subclass `TimelineChild`. Import `PLAYER_COMPONENTS` and `POSITIONS` from `universe.people`. Do not edit universe models.
2. Add `timelines/migrations/0002_player_state.py`, depending on `timelines.0001_initial`. No `RunPython`, no `RunSQL`, and no inserted rows. Do not edit `0001` or any universe migration. Keep index names at or under 30 characters. Keep franchise nullable. Do not add a database default that would invent a shared player or a season.
3. Enforce the application checks in section 1 and section 3. That includes the same-timeline rule, the archive exclusion, the potential ceiling, an active franchise-season, week ranges that do not overlap except the same-season boundary week, `tl_membership_free_agent_franchise`, the prospect / free-agent / retired shapes, status written in the same transaction as the open membership, `reserve` rejected before `roster_cut`, one rating row per season with no opening-day twin, no rating or membership after `retired_year`, the guarded delete of a premature next-season rating, and the freeze of rating values once `TimelineSeason.phase` is `complete`.
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
