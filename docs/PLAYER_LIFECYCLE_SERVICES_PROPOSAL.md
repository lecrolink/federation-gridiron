# Player lifecycle services

The lifecycle operations below remain the workflow for player state. `start_timeline` is now implemented in `timelines/services/timeline_start.py`. It calls `initialize_opening_state` only when the entire opening overlay is absent, and it does not call `materialize_draft_class`. The 1970 class stays on the shared release until `record_opening_draft`, the direction-to-draft transaction in `timelines/services/opening_draft.py`. Baseline for the lifecycle models is `main` at `6a37bb8c7caa5a49870bcc775c26676efaf9c190` (`Add Phase 5 timeline player and roster state`). The models and migration from that commit stay as merged. This document does not add models, migrations, settings, or database rows.

Authority for the rows is [PLAYER_STATE_MODEL_PROPOSAL.md](PLAYER_STATE_MODEL_PROPOSAL.md) and `timelines/models.py`. The older phase list in [PLAYER_UNIVERSE_MODEL_PROPOSAL.md](PLAYER_UNIVERSE_MODEL_PROPOSAL.md) still bundles staff and scouting into a different phase 5 and still describes `PlayerCurrentRating` as the live rating. Those older statements are not the authority for this workflow. This document does not rewrite that file.

The project overview used here is [PLAYER_UNIVERSE_DESIGN.md](PLAYER_UNIVERSE_DESIGN.md): the owner hires the GM and head coach, sets broad instructions, joins the rookie draft, and sees the season. The owner does not manage every roster move, and the owner never edits the 33 rating components. The shared opening roster is the pre-draft roster. Each draft class is one shared list. Picks and roster results belong to the campaign.

## Boundary

Shared rows stay immutable. A lifecycle service reads `UniverseRelease`, `UniverseFranchise`, `FranchiseSeasonMembership`, `UniversePlayer`, `PlayerRatingProfile`, `OpeningAssignment`, and `DraftClass`. It never saves them. It never writes `origin_draft_round`, `origin_draft_pick`, `origin_franchise`, `initial_*`, `potential_*`, class counts, or `leagues.Team`. A write back onto a universe table is a defect.

Campaign-owned rows are the only writes:

| Table | What the service may do |
|-------|-------------------------|
| `TimelinePlayer` | Insert the overlay. Update `status` and `retired_year` when a lifecycle rule says so. |
| `PlayerSeasonRating` | Insert the season vector. Update `current_*` only while that season's phase is not `complete`. Delete one later row only on the guarded offseason-retirement path. |
| `RosterMembership` | Insert a row. On an open row, update `availability` until the season is complete. Fill `end_week` and `reason_ended` once, including on a completed season. |
| `DevelopmentEvent` | Insert. Never update. |

`Timeline`, `TimelineSeason`, `OwnerDirective`, `TimelineFranchiseControl`, `TimelineExpansionChoice`, `ExpansionPin`, `GameKickoffState`, and `SimulationAuditRecord` are not written here.

`OpeningContract` is not copied. `OpeningAssignment.depth` is not copied. `ExpansionPin` stays a shared-player reference and is not consumed. `seasons_played()` stays a count of distinct seasons with a non-null franchise. Nothing stores that count.

## Where these operations sit

The design's opening sequence, for a timeline that starts at the release's first season, is: choose a franchise, staff, direction, rookie draft, instructions, roster cut, then simulate. Later offseasons repeat that shape. An expansion offseason runs its expansion event before that year's draft.

| Design step | Lifecycle operation |
|-------------|---------------------|
| First `TimelineSeason` exists, before the rookie draft | `initialize_opening_state` |
| 1970 phase moves from `direction` to `draft` | `record_opening_draft` |
| The clock has reached a class year | `materialize_draft_class` |
| A later draft service has chosen who joined which club | `place_drafted_players` |
| During the season, a rating delta is already chosen | `record_development` |
| During the season, a roster result is already chosen | `cut_player`, `sign_player`, `reassign_player`, `expansion_move`, `set_availability`, `retire_player` |
| A clock service is about to mark the season complete | `verify_season_replay` |
| The season is already complete and the next `TimelineSeason` exists | `advance_season` |

Creating the timeline, creating `TimelineSeason`, choosing the phase, locking directives, and marking a season complete belong to the clock. Draft order, expansion selection, retirement eligibility, development formulas, and roster-size quotas belong to later services. This workflow records the player-state result they pass in.

## Common rules

Every insert and update uses `Model.save()` on one instance, which runs `full_clean()`, except the fresh opening cohort. That cohort is the one bulk exception: `initialize_opening_state` validates it in memory and inserts `TimelinePlayer`, `PlayerSeasonRating`, and `RosterMembership` with `bulk_create` when every opening overlay is absent. A partial opening cohort, and every other lifecycle write, still uses `save()`. The one delete, the guarded early rating, uses `Model.delete()` on that instance, which runs the timeline-open check in `TimelineChild.delete`. `QuerySet.update`, `bulk_update`, and `QuerySet.delete` are forbidden. MySQL has no trigger that would replace them.

One service call is one database transaction. A failure raises and the transaction rolls back. The call does not catch a player-level error and continue with the next player. After a rollback, the committed rows are the rows that were committed before the call.

Idempotency compares committed state with the request. There is no request-id table.

1. If the committed rows already equal the requested result, return success and write nothing.
2. If the committed rows equal the source state the operation requires, apply the change inside the transaction.
3. Otherwise raise, and write nothing.

Rule 1 is decided before the source-state checks in each operation. Those checks describe rule 2. A retry of a committed result is rule 1 after the source row is gone. A half-applied player, or a complete row that differs from the request, is rule 3 and is not success.

A unique-constraint error inside the transaction rolls the attempt back. The caller retries. The retry hits rule 1 when the first attempt committed the same result, and rule 3 when it committed a different result.

`deferred_lifecycle()` is the transaction for any call that changes `TimelinePlayer.status` and `RosterMembership` together. Its outer use opens `transaction.atomic()`, collects the player ids saved inside the block, and after the block runs `_check_lifecycle` on those players as reloaded from the database. A mismatch raises `ValidationError` inside the atomic block, so the pair rolls back. A nested call, while that context is already active, yields without opening a second transaction and without validating early. Validation runs once, at the outer exit, for every player id noted by `TimelinePlayer.save` or `RosterMembership.save`.

Inside the block, each save still runs every other check: release match, archive exclusion, potential ceiling, franchise-season membership, position list, week overlap, close pairing, immutability, completed-season freeze, and the rule that `retired_year` cannot be saved while a later rating, membership, or event exists. Only the cross-row status check waits.

`RosterMembership`, `PlayerSeasonRating`, and `DevelopmentEvent` load the overlay with `_saved_player()`. An in-memory status that has not been saved does not authorize a child row. Status writes that must be visible to a non-deferred child save are saved first. Status writes paired with memberships stay inside `deferred_lifecycle()`, so the child save skips that one check until the block ends.

The open-membership unique key is `open_overlay_key`. Two open rows for one overlay cannot exist at the same time, including mid-transaction. A replacement therefore saves the close before it inserts the new row.

Close reasons stay the five model values.

| Reason | When this workflow uses it |
|--------|----------------------------|
| `cut` | A club row ends and a null-franchise `free_agent` row opens. Position is unchanged. |
| `transaction` | A replacement that is a club change, a signing from a free-agent row, a same-club position change, or a same-club role change. |
| `expansion` | A club change the caller has identified as an expansion selection. The new franchise differs. |
| `retired` | The player stops playing. No replacement row opens. |
| `season_complete` | The player continues, and the next season's membership is opening in the same transaction. |

The first membership of a prospect has no close reason. Changing `availability` has no close reason. `released`, `departed`, `traded`, `waived`, and `signed` are not values.

`_status_for_role` is the status map: `starter` and `backup` store `active`; `taxi`, `reserve`, and `free_agent` store that same word.

Week ranges in one season must not overlap. A later row may start on the earlier row's `end_week`; that boundary week belongs to the later row. A gap is legal because the model only rejects overlap. This workflow does not fill gaps. `start_week` is at least 0. `end_week` is at least `start_week` when set. `schedule_length` is not a roster-week cap.

`reserve` is rejected by the model while the season phase is `choose_franchise`, `staff`, `direction`, `draft`, or `instructions`. It is legal from `roster_cut` onward. Opening initialization never writes it.

Clamp for one component, applied after every delta, is `max(0, min(potential, value))`. `potential` is the shared `PlayerRatingProfile` value. The stored `DevelopmentEvent.delta` is the requested delta, including when the clamp shrinks the effect on `current_*`.

Replay for one component:

- `prior` is `initial_<component>` when this season is the overlay's earliest `PlayerSeasonRating`. Otherwise `prior` is `current_<component>` on the rating whose `season_year` is one less. A missing prior-year rating fails the replay. The service does not skip a year and does not reread `initial_*` for a later season.
- Apply that component's events in ascending `event_index`. Clamp after each delta.
- A clamp of the summed deltas is not the replay.

A completed season's rating is frozen. `verify_season_replay` is the read that must succeed before a clock service sets `TimelineSeason.phase` to `complete`. This workflow never sets that phase, and it never sets `directives_locked`, `regular_season_simulated`, `postseason_complete`, `expansion_gate`, or the era profile.

A completed or archived timeline cannot gain, change, or lose child rows. Every writing operation requires `Timeline.status` of `active` and raises before writing when the timeline is closed. `verify_season_replay` only reads, so a completed or archived timeline does not block that check.

## Shared replay

`replay_season(overlay, season)` is the in-process helper used by `verify_season_replay`, `record_development`, and `advance_season`. It is not a separate committed operation.

It loads the overlay's `PlayerRatingProfile` and the season's `DevelopmentEvent` rows. For each of the 33 `PLAYER_COMPONENTS` it computes the replay above and compares it with `PlayerSeasonRating.current_*`. The rating row must already exist. The helper returns the per-component results and does not write.

When this overlay has a `PlayerSeasonRating` in the previous calendar year, that season's phase must be `complete` or replay fails. The next season copies a frozen row. When this season is the overlay's earliest rating, `prior` is `initial_*`, and other players' rows in the previous year are ignored.

## `initialize_opening_state`

Copies the release's opening assignments into the campaign's first season.

### Inputs

- `timeline`
- `season`: the `TimelineSeason` to receive the rows
- `availability_by_player`: a map from each opening `UniversePlayer` id to `available` or `unavailable`

### Preconditions

- `timeline.status` is `active`.
- `timeline.release.status` is `published`.
- `season.timeline` is `timeline`.
- `season.season_year` equals `timeline.start_season_year`.
- `season.phase` is not `complete`.
- Every `OpeningAssignment` on the release has a `PlayerRatingProfile`.
- The map contains every opening player and no other player. Each value is `available` or `unavailable`.

The assignment already requires an opening-cohort player, a role in `starter`, `backup`, `taxi`, or `free_agent`, a null franchise exactly for `free_agent`, and a founding franchise otherwise. The membership save already requires a `FranchiseSeasonMembership` for that season year. This operation relies on those checks rather than restating them as new rules.

### Transaction

One `deferred_lifecycle()` block for the whole opening cohort. That block is the transaction. A failure rolls the overlays, ratings, and memberships back together.

When every opening overlay is absent and the release has the full 1,378-player cohort, the block does not call `save()` once per player. It checks scalars with `clean_fields`, runs `TimelinePlayer.clean()` while lifecycle pairing is deferred, and checks the excluded foreign keys in memory: timeline, season, shared player, rating profile, franchise, overlay, release, and the role, status, position, and availability relationships. It then checks unique universe keys, one overlay per assignment, the 494/546/208/130 role counts, the 1,248/130 franchise split, the 33 initial ratings, and the generated open-roster keys. `bulk_create` inserts the three tables in batches of 400. SQLite's variable cap may make those batches smaller. Primary keys are reloaded by `(timeline, player)` before the rating and membership rows are attached. An `IntegrityError` from any of those inserts becomes `ValidationError`. Database foreign keys, checks, and unique constraints still apply.

A cohort that is not the full absent 1,378, including a source-state overlay that still needs a rating or membership, keeps the per-row writes:

1. Save `TimelinePlayer` for `(timeline, player)` with `retired_year` null and `status` from `_status_for_role(assignment.role)`.
2. Save `PlayerSeasonRating` for `(overlay, season)` with each `current_*` copied from `initial_*`.
3. Save an open `RosterMembership`: `franchise` from the assignment, `position` from the shared primary position, `role` from the assignment, `availability` from the map, `start_week` 0, `end_week` null, `reason_ended` null.

`start_week` 0 is the opening-copy rule from the model proposal. The primary position is the same rule. Depth, contract, and `reserve` are omitted.

### Idempotency

Build the expected overlay, rating, and open membership for every assignment.

- Every expected row already matches: success, no write.
- The source state is an absent row, or an overlay whose rating matches the initial vector and that has no membership yet: insert the missing rating or membership and save the assignment status in that same block.
- Any other present row disagrees. A rating that is no longer the initial vector, a closed membership, or a membership whose franchise, role, position, or availability differs, raises with no write.

A cohort that mixes already-matching players with source-state players writes only the source-state players. One disagreeing player rolls the block back.

Overlays for players who are not on an opening assignment are left in place. They do not by themselves fail this call. The design order still materializes the draft class after this copy. A prospect overlay is not an opening row.

### Model changes

Inserts `TimelinePlayer`, `PlayerSeasonRating`, and one open `RosterMembership` per opening assignment that is not already present. Sets `status` to `active`, `taxi`, or `free_agent`. Opening free agents are `free_agent` with a null franchise in that same write. They are not inserted as prospects.

### Failure

Any `ValidationError`, a missing profile, a missing map entry, or a state that is neither the source nor the target rolls the block back. No opening player is left with a rating and no membership, or a membership and the wrong status, from this call.

## `materialize_draft_class`

Inserts prospect overlays for one shared `DraftClass`. Draftable and undrafted players both get an overlay. The class row and the shared players stay unchanged.

### Inputs

- `timeline`
- `draft_class`
- `season`: the `TimelineSeason` for `draft_class.season_year`

### Preconditions

- `timeline.status` is `active` and its release is `published`.
- `draft_class.release` is `timeline.release`.
- `season.timeline` is `timeline`, `season.season_year` equals `draft_class.season_year`, and `season.phase` is not `complete`.
- The class year is inside the timeline and the release.

The season row must already exist. This operation does not create it, so a future class is not materialized early. The same operation covers the opening class and later classes. It does not special-case a calendar year. After the release's last season there is no further class year to pass.

Players are every `UniversePlayer` whose `draft_class` is this class. That includes `draft_pool` `draftable` and `undrafted`. Archive players are rejected by the overlay save.

### Transaction

One `transaction.atomic()` for the class. `deferred_lifecycle()` is not used. A new prospect with zero memberships already satisfies `_check_lifecycle` on each save.

Per player who has no overlay:

1. Save `TimelinePlayer` with `status` `prospect` and `retired_year` null.
2. Save `PlayerSeasonRating` copied from `initial_*`.
3. Insert no membership.

### Idempotency

- No overlay: insert the prospect and the initial rating.
- Overlay exists, this season already has a rating, and the overlay is still a prospect with zero memberships: already materialized. Leave the rating in place, including when later development has changed it.
- Overlay exists, this season has a rating, and the player is no longer a prospect because a placement or signing has already happened: leave those rows. Do not reset the player to a prospect.
- Overlay exists and this season has no rating: partial state. Raise, and roll back the transaction, including inserts made earlier in the call.
- Overlay exists as a prospect while any membership exists: the lifecycle table forbids that pair. Raise.

### Model changes

Inserts prospect overlays and one initial rating each, for class members who had no overlay. Writes no pick, round, or destination. Writes no membership.

### Failure

A missing profile, a completed season, a class from another release, or a partial overlay aborts the transaction. The shared `DraftClass` is read to select the year and the players, and it is not saved.

## `record_opening_draft`

The 1970 clock step from `direction` to `draft`. It is not a lifecycle writer. It saves `Timeline.phase` and `TimelineSeason.phase`, inserts `TimelineDraftSelection` rows, and calls `materialize_draft_class` and `place_drafted_players`. Later seasons, standings order, trades, undrafted signings, and player selection stay outside it.

The caller supplies all 442 selections. Each record carries the overall pick, round, slot, franchise slug, shared player, role, position, availability, and start week. The service recomputes `opening_draft_board` and requires that franchise. It requires each 1970 draftable player once. It does not read `board_rank` or `draft_participation`. An empty, partial, extra, duplicate, or mismatched list is refused before any write. A stored class overlay, pick, or drafted membership that is not already the exact board is refused and left in place.

`created_at` on `TimelineDraftSelection` records when that selection row is inserted, not when the surrounding transaction commits.

An exact completed board returns `unchanged` and does not write. `ValidationError` and `IntegrityError` become `OpeningDraftRefused`, and the release lock rolls the phase, overlays, ratings, selections, and placements back together.

## `place_drafted_players`

Records roster results for players a draft service has already selected. It does not choose the board, the order, or the round.

### Inputs

- `timeline`
- `season`
- `placements`: a list of records, each with `player` (the shared `UniversePlayer`), `franchise`, `role`, `position`, `availability`, and `start_week`

An empty list is a successful no-op.

### Preconditions

The season belongs to the timeline, the timeline is `active`, and the season phase is not `complete`.

Each placement names an overlay on this timeline. Rule 1 is an open membership and status that already equal the request. The source state for rule 2 is:

- The saved status is `prospect`.
- The overlay has zero memberships.
- `franchise` is set, belongs to the release, and has a `FranchiseSeasonMembership` for the season year.
- `role` is `starter`, `backup`, or `taxi`.
- `position` is the shared primary or a listed secondary.
- `availability` is `available` or `unavailable`.
- `start_week` is at least 0.

`free_agent` is a signing, handled by `sign_player`. `reserve` during `draft` or `instructions` is rejected by the model. This operation does not tighten `taxi` further. Whether a drafted player should be taxi is a roster-policy question, left unresolved below.

### Transaction

One `deferred_lifecycle()` for the whole list.

Per placement that is still a prospect:

1. Save the open `RosterMembership` with the supplied franchise, role, position, availability, and `start_week`, and with `end_week` null.
2. Save `TimelinePlayer.status` from `_status_for_role(role)`.

No close reason is written. The rating row is left as it is.

### Idempotency

For each placement, compare the open membership and status with the request.

- Already equal: leave that player.
- Still a prospect with zero memberships: apply.
- Anything else: raise, and roll back every placement in the call.

The whole list follows those three rules inside one transaction. One conflicting player rolls the entire call back, including placements this call had already staged.

### Model changes

Turns each still-prospect placement into one open club membership and a matching `active` or `taxi` status. Does not write draft-pick rows and does not modify the class.

### Failure

A missing overlay, a player who is already on a different roster result, an illegal role, or a model validation error rolls the list back.

## `verify_season_replay`

Read-only check that every stored season rating equals a replay of that season's events.

### Inputs

- `season`

### Preconditions

- `season.timeline.status` is `active` or the season is being inspected after the fact. The check itself does not require the phase to be open, because the clock calls it in order to close the season, and `advance_season` calls it again after the phase is `complete`.
- The call writes nothing, so a closed timeline does not block the read.

### Transaction

No transaction. No `save`, no `delete`, no `deferred_lifecycle()`.

### Idempotency

Repeating the check repeats the read. Success and failure depend only on stored rows.

### Model changes

None.

### Behavior

Load every `PlayerSeasonRating` for the season. For each, run `replay_season`. Success means every component matches.

Failure is a reported mismatch or a missing prior. The clock must not set `phase` to `complete` after a failure. This function does not change the phase either way.

Players with no rating in this season are not invented. `advance_season` separately refuses to move the campaign forward while a non-retired overlay is missing this season's rating.

## `record_development`

Appends ordered development events and moves the live season rating by the same clamped replay.

### Inputs

- `overlay`
- `season`
- `changes`: a list of records, each with `component`, `delta`, `reason`, and `event_index`

`component` is one of the 33 names. `delta` is an integer from −20 through 20. `reason` is `age`, `snaps`, `injury`, or `coaching`. `event_index` is an integer of at least 1, supplied by the caller. The service does not allocate the next index.

`injury` records why a component moved. It does not open an injury ledger. Availability stays on the membership.

### Preconditions

- The timeline is `active`.
- The season belongs to the overlay's timeline.
- The season phase is not `complete`.
- A `PlayerSeasonRating` exists for this overlay and season.
- The saved player is not retired before this season.
- `replay_season` already matches every component, including components this call does not touch.
- Each new `event_index` for a component is greater than every stored index for that overlay, season, and component. Gaps are allowed. An index less than or equal to the current maximum is accepted only when that stored event has the same `delta` and `reason`.
- Within the request, indexes for one component are strictly increasing.

The service does not compute the delta from age, snaps, durability, coaching, or a draw. The caller passes the delta. An event in `retired_year` is still legal: the model rejects development only in a later season.

### Transaction

One `transaction.atomic()`. `deferred_lifecycle()` is not used. Status and membership do not change.

Apply `changes` in `PLAYER_COMPONENTS` order, and in `event_index` order within a component. Skip a change whose stored event already has the same `delta` and `reason`. A different stored payload fails the call before any write.

For each remaining change:

1. Insert `DevelopmentEvent` with the raw `delta`.
2. Set that component's `current_*` to `clamp(current + delta, 0, potential)` and save the rating.

After the last change, run `replay_season` for the overlay. A mismatch raises, and the transaction rolls back.

### Idempotency

For a change whose `(overlay, season, component, event_index)` already exists:

- Stored `delta` and `reason` match, and `replay_season` already matches the rating: success, no write.
- Stored payload differs, or the rating does not match replay: raise, no write.

A call whose every change is an idempotent match writes nothing. A call that mixes a new index with a conflicting old index writes nothing.

### Model changes

Inserts `DevelopmentEvent` rows. Updates the open season's `current_*` values so they equal replay through the new indexes.

### Failure

A completed season, a delta outside −20..20, a backfilled index, a rating that has drifted from replay, or a failed post-write replay rolls back every event and rating change in the call. The stored history for that component is not rewritten.

## `advance_season`

Moves a completed season to the next season: retire the requested players, copy frozen ratings, and hand continuing memberships forward.

### Inputs

- `completed_season`
- `next_season`
- `retirements`: a list of records, each with `player` and `end_week`
- `continuations`: a list of records, each with `player`, `end_week`, `franchise`, `role`, `position`, `availability`, and `start_week`

`end_week` closes the completed season's still-open membership. `start_week` opens the next membership. Neither is defaulted.

### Preconditions

- The timeline is `active`.
- `completed_season.phase` is `complete`.
- `verify_season_replay(completed_season)` succeeds.
- `next_season.timeline` is the same timeline, `next_season.season_year` is `completed_season.season_year + 1`, and `next_season.phase` is not `complete`.
- No player appears in both lists.
- Every overlay whose open membership is in `completed_season` appears in exactly one list. That requirement is about the source state. A named player who already matches the requested retirement or continuation is rule 1 and is not required to still have that open row.
- The source state for a named retirement or continuation is an open membership in `completed_season`.
- A prospect is in neither list. Prospects are copied below and keep zero memberships.
- An overlay whose `retired_year` is a year other than `completed_season.season_year` is in neither list. A player already retired in the completed season may appear in `retirements` and is then rule 1.
- Every non-retired overlay that has a rating, membership, or development event in `completed_season` or in an earlier season has a `PlayerSeasonRating` in `completed_season`. A missing rating fails the call. An overlay whose rows exist only in `next_season` is outside this handoff. It is not copied and it is not a missing-rating failure.
- Each continuation's franchise, when the role is not `free_agent`, is active in `next_season.season_year`. A `free_agent` continuation has a null franchise. `position` is a shared primary or secondary. `role` is legal in `next_season.phase`. `reserve` is legal there only from `roster_cut` onward.

This operation does not decide who retires. It does not copy the previous club, role, or position into the new row. The model proposal keeps memberships from being copied automatically.

### Transaction

One outer `transaction.atomic()` with one outer `deferred_lifecycle()` around every status and membership write. Rating deletes and rating inserts sit in the same atomic block. Nested `deferred_lifecycle()` calls yield. Lifecycle validation runs once at the outer exit.

Order:

1. Finish the read-only preconditions, including replay. A failure here writes nothing.
2. Retire each requested player from `completed_season` through the `retire_player` steps, before any next-season rating is inserted. The retirement `end_week` is the one in the request.
3. For every remaining non-retired overlay that belongs to `completed_season`, including prospects, insert `PlayerSeasonRating` in `next_season` by copying the frozen `current_*` values, when that next rating does not already exist. Leave next-season-only overlays untouched.
4. For each continuation, close the open membership with `season_complete` and the requested `end_week`, then insert the next season's open membership and set `status` from the new role.

A player who is already retired is skipped, including the rating copy.

### Idempotency

The call succeeds with no write when all of the following are already true:

- Each retirement has `retired_year` equal to `completed_season.season_year`, a membership in that season closed with `retired` at the requested `end_week`, and no rating, membership, or event in a later season.
- Each continuation's completed membership is closed with `season_complete` at the requested `end_week`, and the open membership in `next_season` equals the requested franchise, role, position, availability, and `start_week`.
- Each non-retired overlay that belongs to `completed_season` already has a next-season rating. That rating is left as stored. A retry after later development does not copy over it. An overlay whose rows exist only in `next_season` is unchanged.

If some players already match and the others are still in the source state, the call applies only the remaining work in the same transaction. If any player is in a third state, the call raises and rolls back, including the remaining work it had staged.

A next-season rating that already exists is never overwritten. When the continuation is not yet applied, that existing rating must still equal the frozen completed row and the next season must still have no membership and no development event for that overlay. Otherwise the call fails. When the continuation is already applied, later development may have changed the next rating; the retry leaves that rating and writes nothing for that player.

### Model changes

- Retirees: membership closed with `retired`, `status` `retired`, `retired_year` set to the completed season. A premature next-season rating is deleted only under the guards in `retire_player`.
- Everyone else who is not already retired and who belongs to `completed_season`: a next-season rating copied from the frozen row, when absent.
- Continuers: completed membership closed with `season_complete`; one new open membership; `status` from the new role.
- Prospects: a next rating only. Status stays `prospect`. Membership count stays zero.

### Failure

Replay failure, a missing next season, a player in both lists, an open-membership player in neither list, a prospect named as a retiree, a failed retirement guard, or a continuation the model rejects rolls the whole advance back. The completed season's frozen ratings and events stay as they were. The service does not mark the next season complete and does not create the next `TimelineSeason`.

## Roster transitions

These operations record one player at a time. A caller who applies them in a loop gets per-call atomicity. A failure on the second player leaves the first player's committed call as it was. The caller retries the second. `initialize_opening_state`, `place_drafted_players`, and `advance_season` are the batch exceptions, and each of those is one transaction.

Shared source state for a close-and-open transition: exactly one open membership, in the named season, and a saved status that already matches that row. The season phase is not `complete`, except for `retire_player` and the `season_complete` close inside `advance_season`, which are allowed to fill `end_week` and `reason_ended` on a completed season. Club, position, role, `start_week`, and `availability` stay fixed once the phase is `complete`.

Shared write order inside `deferred_lifecycle()`:

1. Save the open row with `end_week` and `reason_ended` when a row is ending.
2. Save the replacement row when the reason is not `retired` and the player is not remaining a prospect.
3. Save `TimelinePlayer.status` from the replacement role, or `retired` with `retired_year`.

### `cut_player`

#### Inputs

`overlay`, `season`, `end_week`, `start_week`, `availability`.

#### Preconditions

The source state is an open club row in `season` (`franchise` set, role not `free_agent`). `end_week` is at least that row's `start_week`. `start_week` does not overlap the closed span. Position on the new row is the closed row's position. The idempotent target below is rule 1 and does not have to still be that open club row.

#### Transaction

One `deferred_lifecycle()`.

#### Idempotency

Success with no write when the club row is already closed with `cut` at `end_week` and the open row is `free_agent`, null franchise, same position, requested availability, and requested `start_week`, with status `free_agent`.

#### Model changes

Closes the club row with `cut`. Opens a `free_agent` row. Sets `status` to `free_agent`.

#### Failure

A free-agent source, a position change requested through this call, an overlap, or a third state rolls back. A position change is `reassign_player`.

### `sign_player`

#### Inputs

`overlay`, `season`, `franchise`, `role`, `position`, `availability`, `start_week`, and, when a free-agent row is closing, `end_week`.

#### Preconditions

Two source states are legal. The idempotent target below is rule 1 and does not have to still be one of these sources.

- Prospect: saved status `prospect`, zero memberships. `end_week` is omitted. No row closes.
- Free agent: the open row is `free_agent` in `season`. `end_week` closes it.

The new role is `starter`, `backup`, `taxi`, or `reserve` subject to the phase rule. `franchise` is set. `position` is a shared primary or secondary.

#### Transaction

One `deferred_lifecycle()`.

#### Idempotency

Success with no write when the open membership equals the request and the status matches the role. When the request supplies `end_week`, a same-season row is already closed with `transaction` at that week. When `end_week` is omitted, that season has no closed predecessor.

#### Model changes

Opens the club membership. Sets status from the role. A free-agent predecessor closes with `transaction`. A prospect predecessor does not exist, so no close reason is written.

#### Failure

Any other source, including an open club row, rolls back. Moving a club player is `reassign_player` or `expansion_move`. Cutting a club player is `cut_player`.

### `reassign_player`

#### Inputs

`overlay`, `season`, `end_week`, `franchise`, `role`, `position`, `availability`, `start_week`.

#### Preconditions

The source state is an open club membership in `season`. The replacement differs in franchise, role, or position. The new role is not `free_agent`. The new franchise is set and is active that season. `reserve` follows the phase rule. The idempotent target below is rule 1. A franchise change uses this operation when the caller names `transaction`. `expansion_move` is the operation that records `expansion`.

#### Transaction

One `deferred_lifecycle()`.

#### Idempotency

Success with no write when the previous row is closed with `transaction` at `end_week` and the open row equals the request.

#### Model changes

Closes the old row with `transaction`. Opens the new row. Sets status from the new role. Same-club position changes and same-club role changes use this path because `position` and `role` are immutable on a row.

#### Failure

An unchanged target, a free-agent target, or a third state rolls back. The reason `transaction` records the roster result. It does not record a trade. Trade agreements are a later service, and that service must call this operation in its own transaction if the agreement and the roster are to commit together.

### `expansion_move`

#### Inputs

`overlay`, `season`, `end_week`, `franchise`, `role`, `position`, `availability`, `start_week`.

#### Preconditions

The source state is an open club membership in `season`. The new `franchise` is a different franchise, active in the season year. The new role is a club role. The caller has already decided that this player was selected. This operation does not read `ExpansionPin`, `ExpansionEvent`, or `TimelineExpansionChoice` to make that decision. The idempotent target below is rule 1.

#### Transaction

One `deferred_lifecycle()`.

#### Idempotency

Success with no write when the previous row is closed with `expansion` at `end_week` and the open row equals the request.

#### Model changes

Closes the old row with `expansion`. Opens the new club row. Sets status from the new role.

#### Failure

A same-franchise move, a move onto a null franchise, or a third state rolls back. A same-franchise role or position change is `reassign_player`.

### `set_availability`

#### Inputs

`overlay`, `season`, `availability`.

#### Preconditions

An open membership exists in `season`. The season phase is not `complete`. `availability` is `available` or `unavailable`.

#### Transaction

One `transaction.atomic()` around one `RosterMembership.save()`. `deferred_lifecycle()` is not used. Status and role stay as they are.

#### Idempotency

Success with no write when the open row already has that availability.

#### Model changes

Updates `availability` on the open row.

#### Failure

No open row, a completed season, or an illegal value rolls back. Unavailability does not close the row and does not change status.

### `retire_player`

Used on its own and by `advance_season`.

#### Inputs

`overlay`, `season`, `end_week`.

`season` is the season of the membership being closed. `retired_year` becomes `season.season_year`.

#### Preconditions

The timeline is `active`. The source state is an open membership in `season`. The idempotent target below is rule 1 and has no open membership. The player is not already retired in another year.

If any `PlayerSeasonRating`, `RosterMembership`, or `DevelopmentEvent` exists with `season_year` greater than this season, all of the following must be true or the call raises:

- The only later rows are `PlayerSeasonRating` rows, and there is exactly one, for `season_year + 1`.
- That later season's phase is not `complete`.
- That later season has no `DevelopmentEvent` and no `RosterMembership` for this overlay.
- That later rating's 33 `current_*` values still equal the frozen rating of `season`.

When those guards pass, the later rating is an early copy and is removed. When any guard fails, the player has entered the later season. The approved `retired_year` is the season of the membership being closed, so this call does not switch the year forward on its own. The caller retires that later season by naming it, once its open membership is the one being closed.

A prospect cannot be retired. Leaving membership count at zero does not mean retired.

#### Transaction

One `deferred_lifecycle()`. When `advance_season` has already entered that context, this call joins the outer block and does not commit on its own.

Order:

1. If the guarded later rating exists, `delete()` that instance. Instance delete runs the timeline-open check. Queryset delete is forbidden.
2. Close the open membership with `retired` and `end_week`.
3. Save `status` `retired` and `retired_year`.

The delete happens before the player save because `extra_clean` rejects `retired_year` while a later rating still exists, and that check is not deferred.

The retirement season's own rating stays.

#### Idempotency

Success with no write when `retired_year` is already this season, the membership in this season is closed with `retired` at `end_week`, no open membership exists, and no later rating, membership, or event exists.

#### Model changes

Closes the last membership with `retired`. Sets `status` and `retired_year`. Deletes at most the unmodified next-season rating described above.

#### Failure

A failed guard, a missing open membership when the player is not already in the target state, a second retirement year, or a closed timeline rolls back. The early rating remains if the retirement itself cannot be saved.

## What is atomic together

| Work | Boundary |
|------|----------|
| One opening player's status, initial rating, and membership | The single `deferred_lifecycle()` around the whole opening cohort, so one bad assignment rolls the cohort back |
| One draft class of prospect inserts | One `transaction.atomic()`, without deferred lifecycle |
| One list of draft placements | One `deferred_lifecycle()` |
| One development call's events and rating update | One `transaction.atomic()`, including the replay before commit |
| One season advance: retirements, rating copy, and continuations | One outer atomic and one outer `deferred_lifecycle()` |
| One cut, sign, reassignment, expansion move, or retirement | One `deferred_lifecycle()` |
| One availability change | One atomic save, without deferred lifecycle |
| Replay verification | No write |

`deferred_lifecycle()` does not make two top-level calls atomic with each other. A cut committed in one call and a signing committed in a later call are two transactions. `advance_season` is the operation that retires and hands off in one transaction so a player is not both copied forward and retired.

Child saves note the player id after `save()`. The fresh opening bulk insert notes the reloaded overlay ids the same way. The outer block then reloads those players and checks only the lifecycle table. Week overlap, the open-key uniqueness, the potential ceiling, and the retirement-year later-row rule have already run, either on each `save()` or in the opening bulk checks.

## Dependencies this design does not specify

These callers must exist later. Their behavior is named here only so the lifecycle boundary stays clear.

- **Clock.** Creates `TimelineSeason`, including phase, `expansion_gate`, and era profile. Calls `verify_season_replay` and requires success before it sets phase `complete`. Does not insert player rows itself.
- **Draft execution.** Builds order from standings and writes timeline pick rows (`DraftSlot`, `TimelineDraftPick` in the model proposal). Passes the resulting placements into `place_drafted_players`. Does not add or remove shared class members.
- **Expansion execution.** Decides who moves and then calls `expansion_move`. May later consume `ExpansionPin` in that same transaction. This workflow neither reads the pin to decide nor clears it.
- **Simulation.** Chooses development deltas and calls `record_development`. Copies a reconstructed rating into `GameKickoffState.inputs` at kickoff. HMAC and snap draws stay on that later service. `persist_snap_draws` stays unrelated to these tables.
- **Roster policy.** Active and taxi quotas, the cutdown to the era's active and taxi limits, injured-reserve minimum stay, and return limits. Those may call `cut_player`, `reassign_player`, and `sign_player`. They are not checks inside a single membership save.
- **Contracts, trades, staff, scouting, depth charts, the injury ledger, schedules, and standings.** Later. A trade that should commit with its roster result needs its own transaction around the agreement and `reassign_player`.

`OwnerDirective` may later influence which role a roster policy requests. These operations do not read directives, and bots still have no directive row.

## Unresolved questions

These are left open. The workflow requires the caller to pass the value, or it refuses, rather than inventing a game rule.

1. **Opening availability.** `OpeningAssignment` has no availability. `initialize_opening_state` requires the caller to pass `available` or `unavailable` for every opening player. Which value the campaign should use, and whether players can differ, is not decided here.
2. **Close week.** `end_week` for `cut`, `transaction`, `expansion`, `retired`, and `season_complete` is caller-supplied and is only required to be at least `start_week`. The service does not compare it with `schedule_length` and does not choose a week number.
3. **Handoff membership.** `advance_season` requires the caller to supply the next franchise, role, position, availability, and `start_week`. It does not default them from the previous open row. The model sets status from the new role, so this service accepts a role that differs from the closed row, and it accepts the franchise and position the caller supplies, including when they differ. Whether product policy should require the previous club, role, and position, or should reject a difference and require `reassign_player` before the season ends, is unresolved. Whether that `start_week` should always be 0 is also unresolved. The opening copy uses 0; later memberships only require a legal week.
4. **Who is drafted, and onto which role.** `place_drafted_players` accepts an explicit list. Order, rounds, and the choice of `starter`, `backup`, or `taxi` are not computed here.
5. **Who retires.** `advance_season` and `retire_player` accept an explicit player. Age, snaps, and durability rules are not applied here.
6. **Who expansion selects.** `expansion_move` accepts an explicit destination. Pins, protected lists, and the veteran counts on `ExpansionEvent` are not applied here.
7. **How a delta is chosen.** `record_development` stores the caller's delta and reason. The design's formula (gap to potential, age against peak, snaps, durability, coaching, stable draw) belongs to simulation.
8. **Event index allocation.** The caller supplies `event_index`. The service rejects an index that would sit at or below an existing index, other than an exact retry. It does not define which counter the simulator should use beyond "strictly after the stored maximum."
9. **Season clock.** This workflow refuses to create `TimelineSeason` or to set its phase. What the next season's phase and `expansion_gate` should be at creation stays with the clock.
10. **Injured reserve duration.** `reserve` is a legal role from `roster_cut` onward. Minimum stay and return limits are still marked as needing approval in the universe design. This workflow does not enforce them.
11. **Roster quotas.** Era active and taxi limits are not checked on a single membership. A roster can be over or under the era limit after one of these calls. A later policy service owns that check.
12. **Gap weeks.** The model permits a gap between `end_week` and the next `start_week`. This workflow accepts that pair. Whether a cut or signing should forbid a gap is unresolved.
13. **Entered-season retirement.** If an offseason retirement finds a later membership, a later event, a modified later rating, or a completed later season, the call fails. It does not automatically retire the player in that later season. The caller has to name that season once it is the membership being closed.

## Non-goals

This phase does not design or implement contracts, staff overlays, scouting, simulation, schedules, standings, admin, API, UI, or deployment. It does not change `timelines/models.py`, `timelines/migrations/`, universe models or migrations, settings, or database state. It does not run migrations and it does not contact a database.

The implementation keeps one transaction around each operation above and treats any universe write as a defect. Ordinary rows still go through `save()` and `delete()`. The fresh 1,378-player opening copy is the bulk exception described under `initialize_opening_state`.

## Confirmation

This document is the only intended change on the design branch. It does not modify application code, models, migrations, settings, or database state.
