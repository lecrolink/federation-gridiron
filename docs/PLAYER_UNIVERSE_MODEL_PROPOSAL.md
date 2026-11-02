# Federation Gridiron — Player Universe Model Proposal

This document translates [PLAYER_UNIVERSE_DESIGN.md](PLAYER_UNIVERSE_DESIGN.md) into a concrete Django model architecture. Phase 1 is the `universe` app and its five models. Later phases in this file are still proposals: they do not add models, migrations, services, seed files, generated people, tests, or database rows.

The project on `main` is Django 5.2 with `DEFAULT_AUTO_FIELD = BigAutoField` and MySQL. `universe` holds the shared release. `timelines` is still only proposed.

| App | Owns | Who writes it |
|-----|------|----------------|
| `universe` | One published release and the people, rules, and franchise history shared by every timeline | A generator, once, at publication |
| `timelines` | One owner's playthrough | Timeline services. They reference universe rows and never update them |

`leagues.Team` stays a franchise-catalog record. A user's roster, contracts, staff, and results are timeline rows. They are never columns on `Team`, and timeline code does not insert `League`, `Conference`, `Division`, or `Team` or update `Team.league`, `Team.conference`, or `Team.division`.

## Architecture rules

1. Shared identity and timeline state live in different tables. There is no table that holds both a person's biography and one user's current ratings, contract, and statistics.
2. A second timeline does not copy `UniversePlayer`, `UniverseGM`, or `UniverseCoach`. It inserts overlay rows that store a foreign key to the shared person and only the values that can diverge.
3. A future draft class remains shared. A timeline creates player overlays for that class when the class becomes relevant: the opening draft for 1970, and each later class at season entry. Creating a timeline does not insert overlays for a later class. The archive cohort never receives an overlay.
4. The 33 player components are explicit columns on a one-row rating profile, described below. They are not an entity-attribute-value store.
5. Deleting one timeline deletes that timeline's state only. It does not delete shared people, franchises, releases, catalog teams, or another user's timeline.
6. Numeric coefficients, the salary cap, the minimum salary, and rookie-slot percents are fields that may stay null until configured. Their absence does not require a different table.

"Immutable after publish" in this document is a service rule. Phase 1 enforces it in `clean()`, `save()`, and instance `delete()`. Queryset `update`, `bulk_update`, `bulk_create` on a published release, and direct SQL bypass those methods. A generator may use bulk operations only while the release is `draft`. Published rows change only through an approved service. Application code must not call queryset `update` or `bulk_update` on published release data. Future admin tools are read-only for a published release. `catalog_fingerprint` and `validation_fingerprint` are how a later check confirms the stored rows. Database constraints enforce the single-table rules they can express. This architecture adds no database triggers. The confirmed 1970–2029 horizon, the 24/28/32 expansion schedule, and the conference and division shapes are publication data for that release. They are not check constraints on every future row.

### Deletion policy

Default, unless a model below names a different behavior:

- Every foreign key from a timeline-owned row to `Timeline`, and every foreign key from one timeline-owned row to another, uses `on_delete=CASCADE`. A timeline-to-timeline `PROTECT` would make Django raise `ProtectedError` while deleting the campaign. That link is not used.
- Every foreign key from a timeline-owned row to a shared row uses `on_delete=PROTECT`. Deleting the timeline removes the referencing row. It does not remove the shared row.
- Every foreign key from a shared child to `UniverseRelease` uses `on_delete=CASCADE`. Sibling links between shared children, such as a player pointing at a college or a draft class, use `PROTECT`, so deleting a college cannot delete players. A release that still has a timeline cannot be deleted, because `Timeline.release` is `PROTECT`. Shared people therefore survive for as long as any timeline pins the release. An unpublished release with no timeline is removed by deleting children from the leaves inward, then the release. A single `release.delete()` is not the rollback path, because those sibling `PROTECT` links would raise `ProtectedError`.
- `Timeline.owner` points at `settings.AUTH_USER_MODEL` with `on_delete=CASCADE`. Deleting that user deletes that user's timelines and, through them, their timeline state. It does not delete another user's timelines or any shared row.
- No shared person uses `on_delete=CASCADE` toward a timeline row. A shared delete cannot wipe a campaign.
- `UniverseFranchise.catalog_team` points at `leagues.Team` with `on_delete=PROTECT` and `null=True`. It stays null while names are placeholders. Setting it never grants timeline code permission to write the `Team` row.

MySQL unique indexes treat `NULL` as distinct. An open-stint rule therefore cannot be a unique key on a nullable end year. Where one open row is required, the model adds a stored generated column that is the owner id while the row is open and `NULL` when it is closed, and a unique constraint on that column. Closed rows do not collide. Application `full_clean()` checks the same rule.

### When rows are created

| Moment | What appears |
|--------|----------------|
| Release publication | Every shared row for that release. The release is `draft` while the generator writes, then `published` after validation. |
| Timeline creation | The timeline header, the 1970 season shell, opening player and staff overlays, copied contracts, opening directives, and scout reports for people already relevant. |
| Season entry | The next `TimelineSeason`, that year's schedule shells, that year's draft class overlays, incoming staff overlays, and the draft-slot board. Expansion years also create the choice row's pending state if it does not exist yet. |
| During simulation | Results, injuries, development, transactions, stat lines, awards, standings, kickoff inputs, and simulation audit records. |

### Row counts

Counts below are for one release, or for one timeline that plays 1970 through 2029, using the era defaults in the design. They are planning counts, not rows this document creates. Expansion clubs may leave taxi empty, so realized roster and stat rows can be lower than the full-taxi totals. The archive mean of 1,032 players is an assumption; franchise-seasons, 1,792, are exact.

## How the 33 components are stored

Three shapes were compared for `speed`, `acceleration`, `agility`, `strength`, `stamina`, `durability`, `awareness`, `discipline`, `processing`, `throw_power`, `throw_accuracy`, `catching`, `route_running`, `carrying`, `elusiveness`, `run_blocking`, `pass_blocking`, `tackling`, `coverage`, `pass_rush`, `run_defense`, `kick_power`, `kick_accuracy`, `punt_power`, `punt_accuracy`, `return_vision`, `special_teams`, `work_ethic`, `leadership`, `temperament`, `clutch`, `consistency`, and `adaptability`.

| Shape | Validation | Query speed | Migration safety | Simulation |
|-------|------------|-------------|------------------|------------|
| Explicit columns on `UniversePlayer` and again on the timeline overlay | A `0–100` check sits next to the name, the body, and the contract. One missing column is easy to miss in a very wide row. | One read returns identity and talent, including columns the match does not want. | Adding component 34 rewrites the person row and the overlay row, which are the tables with the most foreign keys. | The hot loop is simple, but the identity row is no longer stable. |
| One-row profile with explicit fields. `PlayerRatingProfile` holds `initial_*` and `potential_*`. `PlayerCurrentRating` holds `current_*`. | Each profile has one job. `CheckConstraint` can require every component to be `0–100`. "All 33 are present" is the row existing. `current <= potential` crosses two tables, so the timeline service enforces it in `full_clean()`; it is not a single-table SQL check. | The match loads one profile row of 33 small integers. Identity and pre-1970 history stay off that read. | A new component adds columns only to the profile tables. Person keys, stints, and stats do not move. | One row per player is the natural vector. No join per component inside a snap. |
| Normalized component rows, one row per player per component | A unique `(player, component)` constraint can require 33 rows only with a count check outside the database, or with 33 partial indexes. A player with 32 rows looks valid until a query notices. | A snap that needs several components either joins 33 rows or pivots them. At 21,948 players, initial plus potential is about 1.45 million rating rows before any timeline exists. | A new component is an inserted code, which is easy, and a backfill of every person, which is not free. | The inner loop pays a join or a pivot on every player on the field. |

Recommendation: the one-row profile with explicit fields.

`PlayerRatingProfile` is a `OneToOneField` to `UniversePlayer`. `PlayerCurrentRating` is a `OneToOneField` to `TimelinePlayer`. The same pattern is used for GM and coach abilities, which are a different and smaller list. Scout readings are the exception: `TimelineScoutComponent` is normalized because the simulator does not read it, many potential readings stay empty, and the owner screen asks for one component at a time. That is a display table, not the match vector.

Overall is not a column. It is a position-weighted display of `current`, or of the scout reveal.

## Shared models

### UniverseRelease

One publication of the fictional universe.

- Ownership: shared.
- Primary key: `BigAutoField`. Public key: `slug`, `CharField(max_length=64)`, unique. Example: `fg-1970-1`.
- Foreign keys: `root_league` to `leagues.League`, `PROTECT`.
- Fields: `generator_version` `CharField(64)`; `seed` `BigIntegerField`, the integer from the design, never a Django setting; `founding_franchise_count` `PositiveSmallIntegerField`; `expansion_schedule_version` `PositiveIntegerField`; `catalog_fingerprint` `CharField(64)`; `validation_fingerprint` `CharField(64)`; `first_season_year` `PositiveSmallIntegerField`; `season_count` `PositiveSmallIntegerField`; `last_season_year` `PositiveSmallIntegerField`; `status` `CharField` choices `draft` or `published`; `created_at` and `published_at` `DateTimeField`. The confirmed release stores 24, 1970, 60, and 2029 in these columns. Those numbers are not database checks.
- Unique: `slug`.
- Checks: `founding_franchise_count` is at least 1; `season_count` is at least 1; `last_season_year` is at least `first_season_year`; `first_season_year + season_count - 1 = last_season_year`; `status = published` implies both fingerprints and `published_at` are set.
- Indexes: `status`.
- Application guard after `published`: seed, generator version, years, fingerprints, founding count, expansion schedule version, slug, and root league. Mutable while `draft`: the generator may rebuild, including with bulk writes. A schedule change after publish is a new release. The guard is not a database trigger.
- Rows: one per release. This product expects one published release until a later release is authorized.
- Created at release publication.

### EraRuleProfile

Versioned rules for a contiguous span of seasons. Nine working rows: `y1970`, `y1974`, `y1976`, `y1978`, `y1987`, `y1995`, `y1996`, `y2012`, `y2021`.

- Ownership: shared.
- Primary key: `BigAutoField`. Public key: `profile_key`, `CharField(32)`, unique with the release.
- Foreign keys: `release` to `UniverseRelease`, `CASCADE`.
- Fields: `first_year` and `last_year` `PositiveSmallIntegerField`; `market` choices `controlled`, `transitional`, `modern`; `style_key` choices `run-first`, `passing-rules`, `timing-specialists`, `cap-and-size`, `spread-influence`, `modern-passing`; `active_limit` and `taxi_limit` `PositiveSmallIntegerField`; nineteen `quota_active_*` and nineteen `quota_taxi_*` `PositiveSmallIntegerField` columns, one per primary position; `ir_max`, `ir_min_weeks`, `ir_returns_allowed` `PositiveSmallIntegerField`; `draft_rounds` `PositiveSmallIntegerField`; `rookie_contract_years_min` and `rookie_contract_years_max`; `veteran_contract_years_min` and `veteran_contract_years_max`; `accrued_seasons_for_ufa` `PositiveSmallIntegerField`, null in the controlled market; `trade_deadline_week` `PositiveSmallIntegerField`; `future_pick_years` `PositiveSmallIntegerField`; `waiver_rule`, `free_agency_type`, `fa_compensation`, `cap_status`, `franchise_tag`, `playoff_format`, `overtime_rule`, and `sack_leaderboard` as `CharField` choices from the design; `schedule_length` `PositiveSmallIntegerField`; `cap_limit` and `minimum_salary` `BigIntegerField`, null while unset; `rookie_slot_scale` `JSONField`, null until 1995 and until a percent table is configured. The slot table is the one JSON field on this row because its length is the draft, not a fixed 33.
- Unique: `(release, profile_key)`.
- Checks: `first_year <= last_year`; both years inside 1970–2029; the nineteen active quota columns sum to `active_limit`; the nineteen taxi quota columns sum to `taxi_limit`; `cap_status = on` implies `market = modern`; `cap_status = off` implies `cap_limit` is null. A modern profile with a null `cap_limit` is refused in the publication service. That last rule is not a database check, because the column is allowed to exist before a number is chosen.
- Indexes: `(release, first_year, last_year)`.
- Immutable after the release is published. Mutable while the release is `draft`.
- Rows: 9 per release.
- Created at release publication.

Overlapping year spans are refused by the publication service. A SQL exclusion constraint is not assumed on MySQL.

### College

- Ownership: shared.
- Primary key: `BigAutoField`. Public key: `slug`, `SlugField`, unique with the release.
- Foreign keys: `release` `CASCADE`.
- Fields: `name` `CharField(255)`.
- Unique: `(release, slug)`.
- Checks: none beyond non-empty name.
- Indexes: `slug`.
- Immutable after publish.
- Rows: about 48–80. The count is a generator input.
- Created at release publication.

### EraCollegeWeight

How often a college appears for players generated under a profile. Display and generation only.

- Ownership: shared.
- Primary key: `BigAutoField`. No separate public key.
- Foreign keys: `profile` and `college`, both `CASCADE`.
- Fields: `weight` `PositiveIntegerField`.
- Unique: `(profile, college)`.
- Checks: `weight > 0`.
- Indexes: the unique key.
- Immutable after publish.
- Rows: up to colleges times profiles, about 400–700.
- Created at release publication.

### UniverseFranchise

The franchise identity inside a release. This is not `leagues.Team`. Timeline roster, staff, and results point here.

- Ownership: shared.
- Primary key: `BigAutoField`. Public key: `placeholder_slug`, `SlugField`, unique with the release. Values: `founding-01` through `founding-24`, `expansion-1976-01` through `expansion-1976-04`, `expansion-1996-01` through `expansion-1996-04`.
- Foreign keys: `release` `CASCADE`; `catalog_team` to `leagues.Team`, `PROTECT`, null; `history_predecessor` to self, `PROTECT`, null. No predecessor is scheduled.
- Fields: `display_name` `CharField(255)` (the placeholder until a name is chosen); `origin` choices `founding` or `expansion`; `first_active_season` `PositiveSmallIntegerField`; `final_active_season` `PositiveSmallIntegerField`, null while the club remains active. `origin` is stored. Display code does not infer it from the debut year.
- Unique: `(release, placeholder_slug)`; `(release, catalog_team)` when `catalog_team` is not null. MySQL allows many null catalog links, which is what placeholder franchises need.
- Database checks: `origin` is `founding` or `expansion`; `final_active_season` is null or greater than or equal to `first_active_season`.
- Model validation, because it reads the parent release: `first_active_season` is inside the release's playable span; `final_active_season`, when set, is inside that span; a founding franchise begins in the release's first season; an expansion franchise begins after that season. The confirmed release's expansion years, 1976 and 1996, are checked when that release is published. They are not a check constraint on this table.
- Indexes: `(release, first_active_season)`.
- Application guard after publish: slug, origin, first season, display name, catalog link once set, predecessor. A null `catalog_team` may still be set once. `display_name` changes for a later release by a `FranchiseHistoryEvent`. This release schedules none. The guard is not a database trigger.
- Rows: 32.
- Created at release publication.

### FranchiseSeasonMembership

Conference and division for one franchise in one season. The same rows in every timeline. Not standings.

- Ownership: shared.
- Primary key: `BigAutoField`. Public key: `(franchise, season_year)`.
- Foreign keys: `franchise` `CASCADE`. Conference and division are nonempty slugs, not `leagues.Conference` or `leagues.Division` rows. The confirmed release uses `conference-1`, `conference-2`, and `d1` through `d4`. A design label such as `c1-d3` is `conference_slug` `conference-1` and `division_slug` `d3`. The schema does not limit every release to those two conferences, those four divisions, or a slot ceiling of 7.
- Fields: `season_year` `PositiveSmallIntegerField`; `conference_slug` `CharField(32)`; `division_slug` `CharField(32)`; `slot_order` `PositiveSmallIntegerField`; `owner_selectable` `BooleanField`, default true. `owner_selectable` means a human owner may control that franchise during that season. It does not mean a new timeline may begin with that franchise in that season. The confirmed release sets it true for every active franchise in every active season. False is reserved for a future bot-only franchise-season, and none are scheduled. A timeline that awards an expansion franchise to its owner changes timeline ownership only. It does not write this flag false.
- Unique: `(franchise, season_year)`.
- Database checks: `slot_order` is at least 1; `conference_slug` and `division_slug` are nonempty. Model validation also requires both values to be slugs, one slot per release, season, conference, and division, and a year inside the franchise's active span and the release's playable span. Conference count, division count, slot limit, and realignment are publication checks against the versioned schedule.
- Indexes: `(season_year, conference_slug, division_slug, slot_order)`.
- Application guard after publish. Not a database trigger.
- Rows: 1,792.
- Created at release publication.

### FranchiseHistoryEvent

Rename, relocation, or reorganization. The table exists so a later release can add one. This release inserts none.

- Ownership: shared.
- Primary key: `BigAutoField`.
- Foreign keys: `franchise` `CASCADE`.
- Fields: `season_year`; `kind` choices `rename`, `relocation`, `reorganization`; `previous_name`, `new_name`, `city` `CharField(255)`; `notes` `TextField`.
- Unique: `(franchise, season_year, kind)`.
- Checks: model validation requires the year to fall inside the parent release's playable span and inside the franchise's active span. The table does not hardcode 1970–2029.
- Indexes: `(franchise, season_year)`.
- Application guard after publish. Not a database trigger.
- Rows: 0 in this release.
- Created at release publication, only when an event is scheduled.

### ExpansionEvent

The 1976 and 1996 entry rules, including the active-roster arithmetic.

- Ownership: shared.
- Primary key: `BigAutoField`. Public key: `(release, season_year)`.
- Foreign keys: `release` `CASCADE`.
- Fields: `season_year`; `existing_club_count`; `new_club_count`; `active_target`; `taxi_capacity`; `max_losses_per_existing`; `min_losses_per_existing`; `clubs_at_max_loss`; `clubs_at_min_loss`; `veteran_picks_per_new`; `veteran_picks_total`; `rookie_picks_per_new`; `undrafted_multiplier`; `undrafted_pool`; `undrafted_reserved_per_new`; all of those `PositiveSmallIntegerField`. There is no free-agent reserve column. Rule text: `expansion_draft_order_rule`, `roster_building_rule`, `free_agent_rule`, `rookie_draft_slot_rule`, `gm_hiring_rule`, `hc_hiring_rule`, each a non-empty `TextField` before publish. `free_agent_rule` says free agents may replace an undrafted signing and are not required.
- Unique: `(release, season_year)`.
- Checks, all on the row, and none of them read a timeline free-agent list: `veteran_picks_per_new * new_club_count = veteran_picks_total`; `clubs_at_max_loss + clubs_at_min_loss = existing_club_count`; `clubs_at_max_loss * max_losses_per_existing + clubs_at_min_loss * min_losses_per_existing = veteran_picks_total`; `min_losses_per_existing < max_losses_per_existing`; `veteran_picks_per_new + rookie_picks_per_new + undrafted_reserved_per_new = active_target`; `undrafted_reserved_per_new * new_club_count <= undrafted_pool`; `undrafted_pool = undrafted_multiplier * (existing_club_count + new_club_count)`. Taxi capacity is not in the active-roster sum. The stored 1976 row is 24, 4, 40, 8, max 3, min 2, 16 clubs at 3, 8 clubs at 2, 16 veterans per new club, 64 total, 17 rookies, multiplier 2, pool 56, reserved 7. `16 + 17 + 7 = 40` and `16 × 3 + 8 × 2 = 64`. The stored 1996 row is 28, 4, 53, 8, max 4, min 3, 20 clubs at 4, 8 clubs at 3, 26 veterans per new club, 104 total, 7 rookies, multiplier 3, pool 96, reserved 20. `26 + 7 + 20 = 53` and `20 × 4 + 8 × 3 = 104`.
- Indexes: `season_year`.
- Immutable after publish.
- Rows: 2.
- Created at release publication.

The publication service also refuses the release if the generated undrafted class is smaller than `undrafted_pool`, or if the multiplier is not 2 in a normal year and 3 in 1996. It does not look for veteran free agents. A timeline season is not blocked by a free-agent shortage. The shared classes are already on the release.

### ExpansionClub

Which new franchises belong to an expansion event.

- Ownership: shared.
- Primary key: `BigAutoField`.
- Foreign keys: `event` and `franchise`, both `CASCADE`.
- Fields: `selection_order` `PositiveSmallIntegerField` (1–4, placeholder slug order).
- Unique: `(event, franchise)`; `(event, selection_order)`.
- Checks: the franchise's `first_active_season` equals the event year.
- Indexes: the unique keys.
- Immutable after publish.
- Rows: 8.
- Created at release publication.

### HistoricalStanding

Shared 1960–1969 record book used for display and for the 1970 draft order. Not a timeline standing.

- Ownership: shared.
- Primary key: `BigAutoField`.
- Foreign keys: `franchise` `CASCADE`.
- Fields: `season_year`; `wins`, `losses`, `ties` `PositiveSmallIntegerField`; `draft_order` `PositiveSmallIntegerField`, null except where the 1970 order needs it.
- Unique: `(franchise, season_year)`.
- Checks: wins, losses, and ties are nonnegative. A database check requires `draft_order` to be null or at least 1. Model validation places the year in the ten seasons immediately before the release's first playable season, allows `draft_order` only in the last of those seasons, and limits the rows to founding franchises. For the confirmed 1970 release that window is 1960–1969, and draft order is allowed only in 1969. Phase 1 does not check that wins + losses + ties equal a schedule length, because the approved design does not fix the 1960–1969 game count. That equality can be added when a length is approved, without a new table.
- Indexes: `(season_year, draft_order)`.
- Application guard after publish. Not a database trigger.
- Rows: 240 if 24 clubs played each of those ten years. The generator fills this from the same seed. It does not replay the seasons.
- Created at release publication.

### DraftClass

Header for one rookie class, 1970 through 2029.

- Ownership: shared.
- Primary key: `BigAutoField`. Public key: `(release, season_year)`.
- Foreign keys: `release` `CASCADE`; `profile` to `EraRuleProfile`, `PROTECT`.
- Fields: `season_year`; `franchise_count`; `rounds`; `draftable_count`; `undrafted_multiplier` `PositiveSmallIntegerField`; `undrafted_count`.
- Unique: `(release, season_year)`.
- Checks: `draftable_count = rounds * franchise_count`; `undrafted_count = undrafted_multiplier * franchise_count`; the multiplier is 2 except in 1996, when it is 3; year in 1970–2029. Publication accepts that one expansion-year exception. It does not force every class through the two-per-franchise rule.
- Indexes: `season_year`.
- Immutable after publish.
- Rows: 60. Draftable prospects across those rows: 16,028. Undrafted: 3,616. All draft prospects: 19,644.
- Created at release publication.

Membership is `UniversePlayer.draft_class`, not a second copy of the person.

### UniversePlayer

One fictional player. Identity only. No current rating, contract, roster, or timeline statistic.

- Ownership: shared.
- Primary key: `BigAutoField`. Public key: `universe_key`, `CharField(64)`, unique. Forms: `p-{release}-opening-{index}`, `p-{release}-archive-{index}`, `p-{release}-draft-{year}-{index}`.
- Foreign keys: `release` `CASCADE`; `college` `PROTECT`; `draft_class` `PROTECT`, null for opening and archive; `origin_franchise` to `UniverseFranchise`, `PROTECT`, null; `origin_profile` to `EraRuleProfile`, `PROTECT`.
- Fields: `name` `CharField(255)`; `birth_year` and `birth_day` `PositiveSmallIntegerField`; `position` `CharField(4)` with the 19 position choices; `secondary_position_1` and `secondary_position_2` `CharField(4)`, null; `handedness` choices `left` or `right`; `height_in` and `weight_lb` `PositiveSmallIntegerField`; `cohort` choices `opening`, `archive`, `draft`; `draft_pool` choices `draftable` or `undrafted`, null outside the draft cohort; `first_pro_year`, `origin_draft_year`, `origin_draft_round`, `origin_draft_pick` `PositiveSmallIntegerField`, null where the design says null; `peak_age`, `development_rate`, `decline_rate` `PositiveSmallIntegerField`; `board_rank` `PositiveSmallIntegerField`, null except draftable prospects; `trait_1`, `trait_2`, `trait_3` `CharField(32)`, null, from `film_junkie`, `gym_rat`, `hothead`, `quiet_pro`, `mentor`, `big_play`.
- Unique: `universe_key`; `(draft_class, board_rank)` for non-null ranks.
- Checks: ratings are not on this row. `height_in` 64–84; `weight_lb` 160–360; `birth_day` 1–365; `birth_year` 1920–2008; `peak_age` 24–32; development and decline 1–100; secondary positions differ from the primary and from each other; at most one of the three trait columns repeats a value; 1970–2029 prospects keep `origin_draft_round`, `origin_draft_pick`, and `origin_franchise` null; archive players have `draft_class` null.
- Indexes: `(release, cohort)`; `(draft_class, board_rank)`; `(position)`.
- Immutable after publish: every field on this row. A timeline never updates it.
- Mutable: nothing after publish.
- Rows: 21,948 if the archive mean holds, of which 20,916 are playable and 1,032 are archive. Opening cohort 1,272. The extra 32 playable identities are the 1996 undrafted class.
- Created at release publication.

### PlayerRatingProfile

Original and potential talent. One row per player.

- Ownership: shared.
- Primary key: `BigAutoField`. Public key: the player's `universe_key`.
- Foreign keys: `player` `OneToOneField` to `UniversePlayer`, `CASCADE`.
- Fields: `initial_<component>` and `potential_<component>` `PositiveSmallIntegerField` for each of the 33 names.
- Unique: the one-to-one.
- Checks: every column is 0–100; each `initial_*` is less than or equal to the matching `potential_*`.
- Indexes: the one-to-one.
- Immutable after publish.
- Rows: same as `UniversePlayer`.
- Created at release publication.

### UniverseCollegeSeason

Display only. The simulator reads `board_rank` and the rating profile, not these lines.

- Ownership: shared.
- Primary key: `BigAutoField`.
- Foreign keys: `player` `CASCADE`.
- Fields: `season_year`; `class_standing` choices `freshman`, `sophomore`, `junior`, `senior`; `games` and `starts`; plus the season-stat columns that apply to college volume, listed under season statistics. Unused columns stay 0.
- Unique: `(player, season_year)`.
- Checks: four seasons before the player's anchor year when an anchor is stored; `starts <= games`. There is no permanent games maximum. College schedule length, including a later postseason, is a publication or era-rule check once those rules exist. `PositiveSmallIntegerField` already rejects a negative count.
- Indexes: `(player, season_year)`.
- Immutable after publish.
- Rows: 87,792 at four seasons per identity.
- Created at release publication.

### UniverseProStint

Continuous employment before 1970. Display. Not a timeline roster.

- Ownership: shared.
- Primary key: `BigAutoField`.
- Foreign keys: `player` and `franchise`, both `CASCADE`.
- Fields: `start_year`, `end_year`; `role` choices `starter`, `backup`, `taxi`.
- Unique: none beyond the primary key. A player may have successive stints.
- Checks: years inside 1960–1969; `end_year >= start_year`; player cohort is `opening` or `archive`.
- Indexes: `(player, start_year)`; `(franchise, start_year)`.
- Immutable after publish.
- Rows: on the order of a few thousand. The 5-year archive mean and the opening veterans' prior service determine it.
- Created at release publication.

### UniverseProSeason

Shared counting stats for 1960–1969. Same columns as `TimelinePlayerSeason`.

- Ownership: shared.
- Primary key: `BigAutoField`.
- Foreign keys: `player` and `franchise`, both `CASCADE`.
- Fields: `season_year` plus the season-stat block.
- Unique: `(player, season_year, franchise)`.
- Checks: year in 1960–1969.
- Indexes: `(franchise, season_year)`; `(player, season_year)`.
- Immutable after publish.
- Rows: 11,520 if the historical roster is 24 clubs times 48 players times 10 years.
- Created at release publication.

### UniverseAward

Honors for 1960–1969.

- Ownership: shared.
- Primary key: `BigAutoField`.
- Foreign keys: `franchise` `CASCADE`; `player` `CASCADE`, null; `gm` `CASCADE`, null; `coach` `CASCADE`, null.
- Fields: `season_year`; `kind` from `mvp`, `opoy`, `dpoy`, `oroy`, `droy`, `all_league_1`, `all_league_2`, `all_star`, `coach_of_year`, `executive_of_year`; `slot` position, null except all-league.
- Unique: `(season_year, kind, slot)` for all-league; one mvp, opoy, dpoy, oroy, droy, coach, and executive per year.
- Checks: year in 1960–1969; exactly one of player, gm, and coach is set; coach and executive awards set staff, not player.
- Indexes: `(season_year, kind)`.
- Immutable after publish.
- Rows: a few hundred for the historical book.
- Created at release publication.

### UniverseGM

- Ownership: shared.
- Primary key: `BigAutoField`. Public key: `universe_key` `CharField(64)`, unique, prefix `g-`.
- Foreign keys: `release` `CASCADE`.
- Fields: `name`; `birth_year`; `former_role` choices `player`, `scout`, `college_coach`, `personnel`, `cap_analyst`; `positional_priority_1` and `positional_priority_2` from `quarterback`, `skill`, `offensive_line`, `defensive_line`, `linebacker`, `defensive_back`, `special_teams`; `strength_1`, `strength_2`, `weakness` from the GM strength vocabulary; `trait_1`, `trait_2` from the GM trait vocabulary; `debut_year`.
- Unique: `universe_key`. Publication also rejects two of the 100 initial GMs with the same strengths, weakness, traits, and career-history signature.
- Checks: the two strengths differ; the two traits differ; priorities differ when both are set; birth year fits the age band in the design.
- Indexes: `(release, debut_year)`.
- Immutable after publish.
- Rows: 462 if the incoming floor holds (100 initial, the rest arriving 1971–2029).
- Created at release publication. A timeline does not mint a GM.

### GMRatingProfile

- Ownership: shared.
- Primary key: `BigAutoField`.
- Foreign keys: `gm` `OneToOneField`, `CASCADE`.
- Fields: `initial_*` and `potential_*` `PositiveSmallIntegerField` for `talent_evaluation`, `scheme_fit`, `negotiation`, `cap_management`, `draft_strategy`, `free_agency`, `roster_construction`, `player_development`, `discipline`, `leadership`, `risk_tolerance`, `patience`, `coach_evaluation`, `veteran_preference`, and `pick_trade_willingness`. `initial_reputation` only. Reputation has no potential.
- Unique: the one-to-one.
- Checks: every rating 0–100; each initial ability is less than or equal to its potential.
- Indexes: the one-to-one.
- Immutable after publish.
- Rows: 462.
- Created at release publication.

### UniverseCoach

Head coaches, coordinators, and historical position coaches are one person table. The chair is a stint role.

- Ownership: shared.
- Primary key: `BigAutoField`. Public key: `universe_key`, prefix `c-`.
- Foreign keys: `release` `CASCADE`.
- Fields: `name`; `birth_year`; `specialty` choices `offense`, `defense`, `both`; `former_role` choices `player`, `college_coach`, `coordinator`, `position_coach`; `preferred_offense` from `power_run`, `option`, `pro_style`, `west_coast`, `air_raid`; `preferred_defense` from `stop_run`, `balanced`, `pressure_quarterback`; `strength_1`, `strength_2`, `weakness`; `trait_1`, `trait_2`; `debut_year`. Specialty is background and orientation. It limits the coordinator chair and does not stop the person from becoming a head coach. A coach with specialty `offense` may hold a head-coach post and keeps `offense` after promotion. The generator may still give the original 1970 head-coach pool specialty `both`; that is not a schema rule. `west_coast` is legal only when `debut_year` is 1987 or later. `air_raid` is legal only when `debut_year` is 2004 or later. Specialty does not mean the two scheme ratings are equal.
- Unique: `universe_key`. The 100 initial head coaches have distinct strength, weakness, trait, and history signatures.
- Checks: strengths differ; traits differ; scheme enums respect the debut year.
- Indexes: `(release, debut_year, specialty)`.
- Immutable after publish, including `specialty` and the preferred schemes. Scheme ratings may rise later only inside the timeline current profile, and only up to potential.
- Rows: 1,148. That is 580 head-coach identities plus 568 coordinator identities. A person who is later promoted does not become a second row.
- Created at release publication.

### CoachRatingProfile

- Ownership: shared.
- Primary key: `BigAutoField`.
- Foreign keys: `coach` `OneToOneField`, `CASCADE`.
- Fields: `initial_*` and `potential_*` for `offensive_scheme`, `defensive_scheme`, `game_management`, `clock_management`, `fourth_down_aggression`, `aggressiveness`, `in_game_adjustments`, `adaptability`, `player_development`, `preparation`, `motivation`, `leadership`, `discipline`, `fatigue_management`, `injury_management`, `staff_management`, and `veteran_preference`. `initial_reputation` only.
- Unique: the one-to-one.
- Checks: 0–100, and initial less than or equal to potential, except reputation.
- Indexes: the one-to-one.
- Immutable after publish.
- Rows: 1,148.
- Created at release publication.

### UniverseStaffPost

Shared employment before the person can be hired. Display. It does not change when a timeline fires someone, and it is not a playable-timeline appointment.

- Ownership: shared.
- Primary key: `BigAutoField`.
- Foreign keys: `gm` null, `coach` null, both `CASCADE`. Exactly one of gm or coach is set. `franchise` to `UniverseFranchise`, `PROTECT`, null. `college` to `College`, `PROTECT`, null.
- Fields: `organization_name` `CharField(255)`, blank when another employer is set; `role` choices `gm`, `personnel_director`, `scout`, `college_coach`, `head_coach`, `offensive_coordinator`, `defensive_coordinator`, `position_coach`, `college_head_coach`; `start_year`, `end_year`; `wins`, `losses`, `ties`, `playoff_appearances`, `championships` `PositiveSmallIntegerField`; `ended_by` choices `resigned`, `fired`, `promoted`, `retired`, `post_completed`.
- Employer: exactly one of `franchise`, `college`, or a nonempty `organization_name`. A college role (`college_coach`, `college_head_coach`) does not name a Federation franchise. A pro chair (`gm`, `personnel_director`, `head_coach`, `offensive_coordinator`, `defensive_coordinator`) does not name a shared college. `scout` and `position_coach` may use any one employer. `organization_name` is the outside organization when the employer is not a shared college and not a Federation franchise.
- Unique: none required. Successive posts are allowed.
- Checks: `end_year >= start_year`; `end_year` is no later than the person's `debut_year - 1`; a post that names a Federation franchise also ends before the release's first playable season. An external or college post may continue through the season before debut, including years after play has begun for other people. `offensive_coordinator` requires specialty `offense` or `both`. `defensive_coordinator` requires specialty `defense` or `both`. `head_coach`, `position_coach`, and `college_head_coach` permit `offense`, `defense`, or `both`. Promotion to head coach does not change specialty. Biography values that stay off this post enum: GM `former_role` `player`, `personnel`, and `cap_analyst` (the dated personnel job is `personnel_director`); coach `former_role` `player`, `college_coach`, and `coordinator` (dated posts use `college_head_coach`, `offensive_coordinator`, and `defensive_coordinator`). No post role was added or renamed for this correction.
- Indexes: `(gm, start_year)`; `(coach, start_year)`; `(franchise, start_year)`; `(college, start_year)`.
- Immutable after publish.
- Rows: several thousand across 1,610 people. The exact count is a generator output, not a schedule identity.
- Created at release publication.

### OpeningAssignment

The 1970 pre-draft roster and free-agent pool. A template. `start_timeline` copies it.

- Ownership: shared.
- Primary key: `BigAutoField`.
- Foreign keys: `release` `CASCADE`; `player` `CASCADE`; `franchise` `CASCADE`, null for a free agent.
- Fields: `role` choices `starter`, `backup`, `taxi`, `free_agent`; `depth` `PositiveSmallIntegerField`, null for a free agent.
- Unique: `(release, player)`. Not unique on `(release, franchise, position, depth)` when franchise is null, because MySQL would not collapse those nulls anyway. The publication service checks depth uniqueness on a club.
- Checks: free agents have null franchise, null depth, and role `free_agent`; rostered players have a founding franchise; the player is in the opening cohort.
- Indexes: `(franchise, role)`.
- Immutable after publish.
- Rows: 1,272 if 24 clubs times 48 slots plus 5 free agents per club. Taxi slots in this template are filled. Expansion taxi later is not.
- Created at release publication.

### OpeningContract

Shared template for opening veterans. Free agents have none.

- Ownership: shared.
- Primary key: `BigAutoField`.
- Foreign keys: `assignment` `OneToOneField`, `CASCADE`.
- Fields: `years` `PositiveSmallIntegerField`; `salary_per_year`, `guaranteed`, `signing_bonus` `BigIntegerField`; `start_year`, `end_year`.
- Unique: the one-to-one.
- Checks: years 1–5; `end_year >= 1970`; `start_year <= 1970`; `guaranteed` between 0 and salary times years. Amounts are ledger units. The scale may be configured later without a new column.
- Indexes: the one-to-one.
- Immutable after publish. The timeline copy is the contract that can change.
- Rows: 1,152.
- Created at release publication.

### OpeningStaffAssignment

Who is employed in 1970, before the owner keeps or replaces anyone.

- Ownership: shared.
- Primary key: `BigAutoField`.
- Foreign keys: `release` `CASCADE`; `franchise` `CASCADE`; `gm` null; `coach` null. Exactly one staff foreign key is set.
- Fields: `role` choices `gm`, `head_coach`, `offensive_coordinator`, `defensive_coordinator`.
- Unique: `(release, franchise, role)`; one assignment per GM; one assignment per coach.
- Checks: 24 of each role; each franchise is founding; coordinator specialty matches the chair.
- Indexes: `(release, role)`.
- Immutable after publish.
- Rows: 96.
- Created at release publication.

## Season statistic columns

`UniverseCollegeSeason`, `UniverseProSeason`, and `TimelinePlayerSeason` use one column list so a career is one shape. Counts are `PositiveSmallIntegerField` unless noted. Unused columns stay 0. Passer rating, yards per punt, and net average are not stored.

Common: `games`, `starts`, `offensive_snaps`, `defensive_snaps`, `special_teams_snaps`, `penalties`, `penalty_yards`, `fumbles`, `fumbles_lost`.

Passing and rushing: `pass_attempts`, `completions`, `pass_yards` `IntegerField`, `pass_touchdowns`, `interceptions_thrown`, `sacks_taken`, `sack_yards`, `rush_attempts`, `rush_yards` `IntegerField`, `rush_touchdowns`.

Receiving: `targets`, `receptions`, `receiving_yards` `IntegerField`, `receiving_touchdowns`, `drops`, `run_block_snaps`, `pass_block_snaps`.

Defense: `tackles`, `tackles_for_loss`, `sacks` in tenths, `quarterback_hits`, `forced_fumbles`, `fumble_recoveries`, `passes_defended`, `defensive_touchdowns`, `coverage_snaps`, `targets_in_coverage`, `completions_allowed`, `interceptions`, `yards_allowed` `IntegerField`, `sacks_allowed`.

Kicking: `fg_made`, `fg_attempts`, `fg_long`, `xp_made`, `xp_attempts`, `kickoff_touchbacks`, `punts`, `punt_yards` `IntegerField`, `punts_inside_20`, `punt_touchbacks`, `punt_long`.

Checks on a season row: `starts <= games`; `completions <= pass_attempts`; `fumbles_lost <= fumbles`; made kicks do not exceed attempts; the other numerator-versus-denominator checks in the phase 2 migration; `sacks` is 0–250. Counts are nonnegative because they are `PositiveSmallIntegerField` values. `games` versus that season's `schedule_length`, and any college schedule length, belong to a later era-rule or publication-validation phase. Phase 2 does not store a permanent college games maximum. The service applies the tighter per-position ranges from the design.

## Timeline models

### Timeline

One owner's campaign. Not a `leagues.League`.

- Ownership: timeline.
- Primary key: `BigAutoField`. Public key: none required beyond the id. `name` is display, not unique.
- Foreign keys: `owner` to the auth user, `CASCADE`; `release` `PROTECT`; `owned_franchise` to `UniverseFranchise`, `PROTECT`.
- Fields: `name` `CharField(255)`; `season_index` `PositiveSmallIntegerField`; `phase` choices `choose_franchise`, `staff`, `direction`, `draft`, `instructions`, `roster_cut`, `regular`, `playoffs`, `offseason`, `complete`; `start_season_year` `PositiveSmallIntegerField`, default 1970, legal values 1970, 1976, 1996; `decision_audit` `CharField(64)`, null. This audit hash records decisions for inspection. It is not a random seed. `persist_snap_draws` `BooleanField`, default false; `created_at`.
- Unique: none across owners. Two timelines may share a name.
- Checks: `season_index` is 1–60; the derived season year `1969 + season_index` is 1970–2029; `owned_franchise` belongs to `release`; a 1970 start owns a founding franchise. Start-year eligibility is not read from `FranchiseSeasonMembership.owner_selectable`. The normal game begins in 1970. Rules for a later start belong in timeline-start configuration in a later phase. Phase 1 does not add that configuration. An expansion award may change `owned_franchise` without setting the shared `owner_selectable` flag false.
- Indexes: `(owner, release)`; `(release, phase)`.
- Immutable: `owner`, `release`, `start_season_year`, `created_at`. Mutable: `name`, `owned_franchise` (only when an expansion award moves the owner), `season_index`, `phase`, `decision_audit`, `persist_snap_draws`.
- Rows: one per campaign. Unbounded across users. Zero in this document.
- Created at timeline creation.

There is no stored random seed and no timeline id inside the draw. `decision_audit` may be recomputed. Changing it does not change HMAC inputs.

### TimelineSeason

The clock for one year of one timeline.

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `timeline` `CASCADE`; `profile` to `EraRuleProfile`, `PROTECT`.
- Fields: `season_index`; `season_year`; `phase` same family as the header, for that year; `expansion_gate` choices `not_expansion`, `ready`, `blocked`.
- Unique: `(timeline, season_year)`.
- Checks: `season_year = 1969 + season_index`; the profile's span contains the year. For 1976 and 1996, `expansion_gate` becomes `ready` from the shared expansion arithmetic: veterans, rookie picks, and reserved undrafted prospects. It is not `blocked` because the timeline has too few free agents. Taxi does not count. A failure of the shared arithmetic is a release-publication error, not a reason to invent a person at season entry.
- Indexes: `(timeline, season_year)`.
- Immutable: `season_index`, `season_year`, `profile`. Mutable: `phase`, `expansion_gate`.
- Rows: 60 per completed timeline. The 1970 row is created with the timeline. Later rows are created at season entry.
- Created at timeline creation for 1970, then at season entry.

### SimulationAuditRecord

The audit log for one kept event. The name is deliberately not `ReplayRecord`. A replay of a game is the kickoff snapshot plus this ordered log, not a row that already contains every snap.

The HMAC is not stored. It is recomputed from the release seed and the coordinates. That recomputation reproduces the random draw only. It does not reproduce the snap outcome. The outcome also depends on the pre-snap state, and this row does not store that state. `pre_state_hash` and `post_state_hash` can verify a reconstruction. They cannot rebuild the roster, fatigue, score, or field position.

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `timeline` `CASCADE`; `game` to `ScheduleGame`, `CASCADE`, null for a development or transaction event that is not inside a game.
- Fields: `season_year`; `week` `PositiveSmallIntegerField`; `franchise_key_a` and `franchise_key_b` `CharField(64)`, stored in sorted order; `event_index` `PositiveIntegerField`; `event_type` `CharField(32)`; `outcome_code` `CharField(64)`; `participant_keys` `CharField(255)`; `pre_state_hash` and `post_state_hash` `CharField(64)`, null; `snap_detail` `JSONField`, null. `snap_detail` is written only when `Timeline.persist_snap_draws` is true.
- Unique: `(timeline, season_year, week, franchise_key_a, franchise_key_b, event_index, event_type)`.
- Checks: franchise keys belong to the release; week is 0 through the schedule length; `snap_detail` is null when the timeline is not persisting snap draws.
- Indexes: that unique key; `(game, event_index)`.
- Immutable once written. A changed decision does not rewrite earlier rows. If the new game produces a different event sequence, later indexes are a different log. A hash that does not match a reconstruction is a failed verification.
- Rows: the default log is the ordered plays or events the product keeps, plus persistent results such as injuries, scoring plays, development, and transactions. That is tens of thousands of rows per timeline, not one full snapshot per snap. Snap-detail rows are written only when `persist_snap_draws` is true, which can reach the low millions and is off by default. Mid-game checkpoints are not a table in this proposal. They can be added later without changing the draw coordinates.
- Created during simulation.

What a debugger can do with this row:

1. Recompute HMAC-SHA256 from the release seed, season, week, franchise keys in sorted order, game id, event index, and event type. Leave out the timeline id, the owner id, and `decision_audit`. That step returns the same draw the game used.
2. To obtain the snap outcome, start from `GameKickoffState` for that game, or from a checkpoint if one was stored later, and apply the ordered audit log up to this index. The draw is then applied to that reconstructed state.
3. Compare `pre_state_hash` with a hash of the reconstructed state. A match verifies the reconstruction. The hash itself is not enough to run the snap.

Scout noise uses the same HMAC with a different coordinate string: player key, component, `ability` or `potential`, season, and week. The GM's `talent_evaluation` changes the band width only. The GM key is not in the HMAC. Replacing the GM does not draw new noise.

### TimelineExpansionChoice

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `timeline` `CASCADE`; `event` to `ExpansionEvent`, `PROTECT`; `target_franchise` `PROTECT`, null.
- Fields: `decision` choices `stay` or `apply`; `protection_priority_1` through `_3`, null; `draft_priority_1` through `_3`, null; `outcome` choices `pending`, `awarded`, `declined`, `stayed`; `selection_method` choices `not_needed`, `automatic`, `fair_draw`.
- Unique: `(timeline, event)`.
- Checks: `apply` requires a target franchise that belongs to the event; `stay` leaves the target null; at most three priorities are set. Pinned names are `ExpansionPin`, below, so the five-player cap is a count check rather than five sparse columns mixed with priorities.
- Indexes: `(event, target_franchise, decision)`.
- Immutable: `event`. Mutable until the award is recorded: decision, target, priorities. `outcome` and `selection_method` change once, from `pending` to the result.
- Rows: 2 per timeline that reaches both expansion years. None until that offseason.
- Created at season entry for 1976 and 1996.

### ExpansionPin

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `choice` `CASCADE`; `player` to `TimelinePlayer`, `CASCADE`.
- Fields: none beyond the keys. Present only in involved mode.
- Unique: `(choice, player)`.
- Checks: at most 5 pins per choice, enforced in `full_clean()`.
- Indexes: the unique key.
- Immutable once the expansion draft starts.
- Rows: 0–5 per expansion choice.
- Created at season entry, when the owner pins.

### OwnerDirective

Append-only instructions. The current row is the latest effective week. AI clubs get a starting row derived from shared staff tags.

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `timeline` `CASCADE`; `franchise` `PROTECT`.
- Fields: `role` choices `gm` or `head_coach`; `effective_year`; `effective_week`; `draft_approach`, `veteran_preference`, `free_agency`, `trade_willingness` null on coach rows; `offensive_emphasis`, `defensive_emphasis`, `fourth_down`, `playing_time` null on GM rows. Choices match the design.
- Unique: `(timeline, franchise, role, effective_year, effective_week)`.
- Checks: GM rows leave coach fields null and the reverse; the first owned-franchise row is 1970 week 0 before the season simulates.
- Indexes: `(timeline, franchise, role, effective_year, effective_week)`.
- Immutable: every field after insert. A change inserts a new row.
- Rows: a handful per franchise per timeline, more if the owner edits often. Planning size is under a few thousand per timeline.
- Created at timeline creation for the opening instruction, then whenever the owner or an AI club changes course.

### TimelineStaff

Overlay for one GM or one coach in one timeline. Holds the divergent reputation and employment pointer, not the biography.

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `timeline` `CASCADE`; `gm` `PROTECT`, null; `coach` `PROTECT`, null. Exactly one is set.
- Fields: `reputation` `PositiveSmallIntegerField`; `status` choices `unemployed`, `employed`, `retired`.
- Unique: `(timeline, gm)`; `(timeline, coach)`.
- Checks: reputation 0–100; exactly one person key.
- Indexes: `(timeline, status)`.
- Immutable: the person foreign key and `timeline`. Mutable: `reputation`, `status`.
- Rows: the opening staff pool at timeline creation (100 GMs, 100 head coaches, 80 coordinators). Each later incoming class at the season it debuts. A completed timeline holds 1,610 overlays if every class is materialized when it becomes hireable. It does not hold a second copy of the shared person.
- Created at timeline creation and at season entry.

### GMCurrentRating

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `staff` `OneToOneField` to `TimelineStaff`, `CASCADE`.
- Fields: `current_*` `PositiveSmallIntegerField` for the 15 GM abilities plus `current_reputation`.
- Unique: the one-to-one.
- Checks: 0–100. The service checks each ability against shared potential.
- Indexes: the one-to-one.
- Immutable: the link. Mutable: every `current_*`.
- Rows: one per GM overlay.
- Created with the overlay. Values start as the shared initials.

### CoachCurrentRating

Same shape as `GMCurrentRating`, using the 17 coach abilities plus reputation.

- Ownership: timeline.
- Rows: one per coach overlay.
- Created with the overlay.

### StaffStint

A job inside the timeline. Promotion closes one stint and opens another on the same `universe_key`.

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `staff` to `TimelineStaff`, `CASCADE`; `franchise` `PROTECT`, null if between jobs.
- Fields: `role` choices `gm`, `head_coach`, `offensive_coordinator`, `defensive_coordinator`, `position_coach`; `start_year`, `start_week`; `end_year`, `end_week` null while open; `reason_ended` choices `opening_replace`, `fired`, `retired`, `hired_elsewhere`, `promoted`, `timeline_complete`, null while open; `open_key` stored generated column.
- Unique: `(staff, open_key)`.
- Checks: `end` is null or at least `start`; reason is null exactly while the stint is open; a coordinator role matches specialty.
- Indexes: `(franchise, role, end_year)`; `(staff, start_year)`.
- Immutable: staff, role, start, franchise. Mutable: end and `reason_ended`, written once when the stint closes. Win-loss is not stored; it is derived from standings.
- Rows: about 896 GM and head-coach stints at a 4-year mean, or about 1,792 if coordinators are seated the same way. One timeline.
- Created at timeline creation for the opening jobs, including an `opening_replace` close when the owner hires. Later hires, firings, and promotions append rows during the offseason.

### TimelinePlayer

The overlay. Created when the person becomes relevant. Retirement is `status = retired` plus `retired_year`. There is no second retirement ledger.

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `timeline` `CASCADE`; `player` to `UniversePlayer`, `PROTECT`.
- Fields: `status` choices `prospect`, `active`, `taxi`, `reserve`, `free_agent`, `retired`; `seasons_played` `PositiveSmallIntegerField`; `career_altering_injuries` `PositiveSmallIntegerField`; `retired_year` and `retired_week`, null until retirement.
- Unique: `(timeline, player)`.
- Checks: the player is not archive; `seasons_played` 0–60; career-altering count 0–10; `retired` status matches a non-null `retired_year`.
- Indexes: `(timeline, status)`; `(player)`.
- Immutable: `timeline`, `player`. Mutable: status, seasons played, injury count, retirement year.
- Rows: 1,272 at timeline creation for the opening cohort. The 1970 class during the opening draft. Each later class at season entry. A completed timeline holds 20,916 overlays. A second timeline holds its own 20,916 and no extra `UniversePlayer` rows.
- Created at timeline creation, the opening draft, and season entry. Not during an ordinary snap.

### PlayerCurrentRating

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `overlay` `OneToOneField` to `TimelinePlayer`, `CASCADE`.
- Fields: `current_<component>` `PositiveSmallIntegerField` for all 33.
- Unique: the one-to-one.
- Checks: each column 0–100. The service rejects a value above the shared `potential_*`.
- Indexes: the one-to-one.
- Immutable: the link. Mutable: the 33 currents, by development or by a career-altering injury.
- Rows: one per overlay.
- Created with the overlay, copied from `initial_*`.

### TimelineScoutReport

Display. The simulator does not read it.

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `overlay` `OneToOneField`, `CASCADE`.
- Fields: `reported_tag_1`, `reported_tag_2`, `reported_tag_3` null; `tag_uncertainty` `PositiveSmallIntegerField`.
- Unique: the one-to-one.
- Checks: uncertainty 0–100.
- Indexes: the one-to-one.
- Immutable: the link. Mutable: tags and uncertainty as evaluation proceeds.
- Rows: one per overlay.
- Created with the overlay.

### TimelineScoutComponent

Normalized because it is not on the match path. Kinds `ability` and `potential` can appear and disappear independently.

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `report` `CASCADE`.
- Fields: `component` `CharField(32)` with the 33 names; `kind` choices `ability` or `potential`; `revealed_value` `PositiveSmallIntegerField`, null while unknown; `uncertainty` `PositiveSmallIntegerField`.
- Unique: `(report, component, kind)`.
- Checks: revealed value 0–100 when set; uncertainty 0–100; ability uncertainty 0 implies `revealed_value` equals the overlay's current component.
- Indexes: the unique key.
- Immutable: report, component, kind. Mutable: revealed value and uncertainty.
- Rows: up to 66 per overlay. Potential rows may be absent. A full timeline is on the order of one million scout rows if both kinds are stored for every playable player. That is acceptable because snaps do not join this table.
- Created with the overlay, then updated on report refresh.

### TimelineStaffScoutReport and TimelineStaffScoutComponent

The same two shapes for a `TimelineStaff` overlay. Abilities are the GM list or the coach list, including reputation. Public win-loss is not stored here.

- Ownership: timeline.
- Created with the staff overlay. The simulator reads `GMCurrentRating` and `CoachCurrentRating`, not these rows.

### RosterStint

Where the player is. A cut, signing, or trade closes one stint and opens another. This is the roster result, not the trade agreement.

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `overlay` `CASCADE`; `franchise` `PROTECT`, null when the player is a free agent.
- Fields: `role` choices `starter`, `backup`, `taxi`, `reserve`, `free_agent`; `start_year`, `start_week`; `end_year`, `end_week` null while open; `open_key` generated.
- Unique: `(overlay, open_key)`.
- Checks: one open stint; free-agent role matches a null franchise; years inside 1970–2029.
- Indexes: `(franchise, start_year)`; `(overlay, start_year)`.
- Immutable: overlay, franchise, role, start. Mutable: the end, written once. A depth-chart change that does not change the club does not require a new stint.
- Rows: several per player. The 103,352 figure is maximum roster capacity, counting every taxi slot, not a guaranteed number of occupied seasons. Slot-seasons themselves are not stint rows.
- Created at timeline creation from `OpeningAssignment`, then during transactions.

### DepthChartEntry

Weekly offensive or defensive order. Separate from the stint so a coach can reorder without a transaction.

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `overlay` `CASCADE`; `franchise` `PROTECT`.
- Fields: `season_year`; `week`; `position`; `depth` `PositiveSmallIntegerField`.
- Unique: `(franchise, season_year, week, position, depth)`; `(overlay, season_year, week, position)`.
- Checks: the player's stint is on that franchise for that week; depth is at least 1.
- Indexes: `(franchise, season_year, week)`.
- Immutable once the week is played. Mutable before the week is played.
- Rows: one per active player per week played. On the order of active players times games' weeks, roughly 1–2 million per full timeline if every week is stored. The service may store the chart only when it changes and copy forward. That optimization does not change the columns.
- Created during simulation, or at the roster cut for week 1.

### SpecialTeamsRole

Weekly kick returner, punt returner, long snapper, coverage, and emergency kicker or punter.

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `overlay` `CASCADE`; `franchise` `PROTECT`.
- Fields: `season_year`; `week`; `role` choices `kick_returner`, `punt_returner`, `long_snapper`, `kick_coverage`, `punt_coverage`, `emergency_kicker`, `emergency_punter`; `depth` `PositiveSmallIntegerField`.
- Unique: `(franchise, season_year, week, role, depth)`.
- Checks: depth limits from the design (returners 2, snappers 1, coverage 10, emergency 1). The player remains his primary position.
- Indexes: `(franchise, season_year, week, role)`.
- Immutable once the week is played.
- Rows: about 26 roles times weeks times franchise-seasons if stored every week. The same copy-forward optimization as the depth chart applies.
- Created during simulation.

### TimelineContract

The living deal. Cap math reads this from 1995 on. The cap limit stays on `EraRuleProfile`.

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `overlay` `CASCADE`; `franchise` `PROTECT`.
- Fields: `years`; `salary_per_year`, `guaranteed`, `signing_bonus` `BigIntegerField`; `start_year`, `end_year`; `status` choices `active`, `expired`, `cut`, `retired`; `is_rookie_deal` `BooleanField`; `open_key` generated for the active row.
- Unique: `(overlay, open_key)` so only one active deal exists.
- Checks: years 1–7; `end_year` from 1970 through 2035; guaranteed between 0 and salary times years; status `active` matches a null close.
- Indexes: `(franchise, status, start_year)`; `(overlay, start_year)`.
- Immutable: overlay, franchise, years, money, start, `is_rookie_deal`, once signed. Mutable: `status`, and `end_year` only if a cut shortens it. A renegotiation is a new row, not an edit of the signed numbers.
- Rows: about 51,676 at one deal per two full-taxi slot-seasons. Fewer if taxi stays empty.
- Created at timeline creation from `OpeningContract`, and when a player signs.

### Injury

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `overlay` `CASCADE`.
- Fields: `season_year`; `week`; `kind` choices `sprain`, `break`, `concussion`, `tear`, `illness`, `wear`; `body_part` choices `head`, `shoulder`, `arm`, `hand`, `back`, `hip`, `thigh`, `knee`, `ankle`, `foot`; `weeks_out` `PositiveSmallIntegerField`; `season_ending` and `career_altering` `BooleanField`.
- Unique: none. Two injuries in one week are unlikely but not forbidden.
- Checks: week 0–schedule length; `weeks_out` 0–36.
- Indexes: `(overlay, season_year, week)`.
- Immutable after insert. The rating drop, if `career_altering`, is applied to `PlayerCurrentRating` in the same service. The injury row does not store the new ratings.
- Rows: about 53,760 at 30 per franchise-season.
- Created during simulation.

### DraftSlot

The right to pick, including a pick that was traded. Created before the draft. `TimelineDraftPick` is the selection.

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `timeline` `CASCADE`; `original_franchise` and `current_franchise` to `UniverseFranchise`, `PROTECT`.
- Fields: `season_year`; `round`; `slot_in_round`; `overall_pick`.
- Unique: `(timeline, season_year, overall_pick)`.
- Checks: round does not exceed the profile's `draft_rounds`; overall pick does not exceed `draftable_count`.
- Indexes: `(timeline, season_year, current_franchise)`.
- Immutable: year, round, slot, original franchise, overall pick. Mutable: `current_franchise`, until the pick is used.
- Rows: 16,028 per completed timeline, equal to the draftable prospects.
- Created at season entry from the previous standings and the shared order rule. The 1970 board is created during the opening draft from `HistoricalStanding`.

### TimelineDraftPick

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `slot` `OneToOneField` to `DraftSlot`, `CASCADE`; `prospect` to `TimelinePlayer`, `CASCADE`. Both sides are timeline rows. The shared identity stays on `UniversePlayer` through the overlay.
- Fields: none beyond the links. Round, club, and number live on the slot as it stood when the pick was made. The service copies `current_franchise` onto the pick by refusing further slot edits after the one-to-one exists.
- Unique: the slot; `(prospect)` inside the timeline, because the prospect overlay is already timeline-scoped and a prospect is drafted once.
- Checks: the prospect's class year matches the slot year; the prospect is `draftable`.
- Indexes: `(prospect)`.
- Immutable once written.
- Rows: 16,028 per completed timeline.
- Created during the draft, which is the opening sequence in 1970 and the offseason afterward.

Undrafted signings do not insert a pick. They insert a contract and a stint.

### Trade

The agreement. A pick-for-pick deal has no roster stint, so the stint list cannot be the only record. Assets live on `TradeAsset`.

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `timeline` `CASCADE`.
- Fields: `season_year`; `week`; `franchise_a` and `franchise_b` as `CharField` keys stored through foreign keys to `UniverseFranchise`, `PROTECT`.
- Unique: none.
- Checks: the two franchises differ; the week is on or before `trade_deadline_week`; future picks stay inside `future_pick_years`.
- Indexes: `(timeline, season_year)`.
- Immutable once written.
- Rows: varies. A few hundred to a few thousand per timeline is the planning range. The table does not invent trades.
- Created during the offseason or before the deadline, in simulation.

### TradeAsset

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `trade` `CASCADE`; `from_franchise` and `to_franchise` to `UniverseFranchise`, `PROTECT`; `player` to `TimelinePlayer`, `CASCADE`, null; `slot` to `DraftSlot`, `CASCADE`, null.
- Fields: none beyond the links.
- Unique: `(trade, player)`; `(trade, slot)`.
- Checks: exactly one of player or slot is set; from and to match the trade's two clubs.
- Indexes: `(player)`; `(slot)`.
- Immutable once written.
- Rows: two or more per trade.
- Created with the trade. The same service closes and opens `RosterStint` rows and moves `DraftSlot.current_franchise`.

### ExpansionProtection

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `timeline` `CASCADE`; `event` `PROTECT`; `franchise` `PROTECT`; `overlay` `CASCADE`.
- Fields: `protected` `BooleanField`.
- Unique: `(timeline, event, overlay)`.
- Checks: the player's open stint is on that franchise; a club with exactly one quarterback, kicker, or punter has that player protected; the number unprotected is at least `max_losses_per_existing`; the number lost later cannot exceed that maximum.
- Indexes: `(event, franchise, protected)`.
- Immutable once the expansion draft starts.
- Rows: one per rostered player on existing clubs in that offseason. About 1,152 in 1976 if every active and taxi spot is full, and about 1,700 in 1996. Taxi players may be on the list. They are not required to exist.
- Created at season entry for the expansion offseason.

### TimelineExpansionPick

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `event` to `ExpansionEvent`, `PROTECT`; `timeline` `CASCADE`; `selecting_franchise` and `from_franchise` to `UniverseFranchise`, `PROTECT`; `overlay` to `TimelinePlayer`, `CASCADE`.
- Fields: `pick_number` `PositiveSmallIntegerField`.
- Unique: `(timeline, event, pick_number)`; `(timeline, event, overlay)`.
- Checks: pick number is 1 through `veteran_picks_total`; the selecting franchise is one of the event's new clubs; the player was unprotected; each existing franchise loses at most `max_losses_per_existing` players; the finished losses are only the minimum or the maximum, and the counts match `clubs_at_max_loss` and `clubs_at_min_loss`.
- Indexes: `(event, selecting_franchise)`.
- Immutable once written.
- Rows: 64 in 1976 and 104 in 1996, 168 per timeline that completes both drafts.
- Created during the expansion draft. The row does not create a `UniversePlayer`.

### FreeAgentOffer

A bid. Acceptance creates `TimelineContract` and a `RosterStint`. The offer row is the negotiation record.

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `timeline` `CASCADE`; `overlay` `CASCADE`; `franchise` `PROTECT`.
- Fields: `season_year`; `week`; `years`; `salary_per_year`, `guaranteed`, `signing_bonus` `BigIntegerField`; `status` choices `offered`, `accepted`, `rejected`, `expired`.
- Unique: one `accepted` offer per overlay per signing window, via `full_clean()`.
- Checks: money is non-negative; years fit the profile's veteran or rookie bounds.
- Indexes: `(timeline, season_year, franchise, status)`; `(overlay, status)`.
- Immutable: the bid numbers. Mutable: `status`, once, from `offered` to a terminal value.
- Rows: varies. Accepted signings are the contracts counted above. Rejected bids may be several times that if they are kept.
- Created during free agency.

Free-agent offers are optional during an expansion offseason. An accepted offer may take a roster spot that would otherwise have gone to a reserved undrafted prospect. A timeline with no acceptable free agents still fills each new active roster from the shared veteran picks, rookie picks, and reserved undrafted prospects. Taxi signings, if any, come after that active roster is full.

### ScheduleGame

The fixture. Its identity is the replay game id.

- Ownership: timeline.
- Primary key: `BigAutoField`. The replay id is `(timeline, season_year, week, home, away)`.
- Foreign keys: `timeline` `CASCADE`; `home` and `away` to `UniverseFranchise`, `PROTECT`.
- Fields: `season_year`; `week`; `kind` choices `regular` or `playoff`; `played` `BooleanField`.
- Unique: `(timeline, season_year, week, home, away)`.
- Checks: home and away differ; both memberships exist for that shared season; week is inside the schedule length for regular games.
- Indexes: `(timeline, season_year, week)`.
- Immutable: teams, year, week, kind. Mutable: `played`.
- Rows: 14,280 regular-season games per completed timeline if the schedule lengths hold (1,008 + 392 + 3,808 + 224 + 6,400 + 2,448). Playoff games add a few hundred more, depending on byes and the field.
- Created at season entry for the regular season. Playoff fixtures are created when the bracket is known.

### GameKickoffState

The inputs required to start one game. This is the MVP snapshot. It is written once, at kickoff, not before every snap. Replaying the game applies `SimulationAuditRecord` rows, in `event_index` order, to this state and to the deterministic draws.

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `game` `OneToOneField` to `ScheduleGame`, `CASCADE`; `profile` to `EraRuleProfile`, `PROTECT`.
- Fields: `inputs` `JSONField`. Each entry names a participant `universe_key`, side, availability, fatigue, injury flag, and the 33 current ratings at kickoff. `input_hash` `CharField(64)` is the SHA-256 of `inputs`. JSON is used here because the snapshot is immutable, one document per game, and not the live rating table. `PlayerCurrentRating` remains the authority during the season. The snapshot is the copy the replay needs after those live values have moved on.
- Unique: the one-to-one.
- Checks: both clubs on the game are represented; every listed player belongs to the timeline.
- Indexes: the one-to-one.
- Immutable once written.
- Rows: one per played game.
- Created during simulation, at kickoff.

A hash of `inputs` verifies a copy. It does not replace `inputs`. Without this row or a later checkpoint, a single snap's outcome cannot be rebuilt from its draw coordinates.

### GameResult

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `game` `OneToOneField`, `CASCADE`.
- Fields: `home_score` and `away_score` `PositiveSmallIntegerField`; `overtime` `BooleanField`; `verified` `BooleanField`.
- Unique: the one-to-one.
- Checks: a tie is legal only when the profile's overtime rule is `tie`, or when the rule produces a tie. The service applies that era rule.
- Indexes: the one-to-one.
- Immutable once `verified` is set. Mutable while the game is being written.
- Rows: one per played game.
- Created during simulation.

`verified` means the stored score was reproduced by replaying from `GameKickoffState` with the deterministic draws and the ordered audit log, or was just produced by that same function. The draw coordinates alone do not verify the score. Verification does not use a second random stream.

### Standing

One row per franchise per season. Rebuildable from `GameResult`. Stored so the draft order and the standings page do not rescan every game.

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `timeline` `CASCADE`; `franchise` `PROTECT`.
- Fields: `season_year`; `wins`, `losses`, `ties`; `points_for`, `points_against` `PositiveSmallIntegerField`; `conference_slug`; `division_slug`.
- Unique: `(timeline, franchise, season_year)`.
- Checks: the membership for that franchise-season matches the slugs; wins + losses + ties does not exceed the games played so far.
- Indexes: `(timeline, season_year, conference_slug, division_slug)`.
- Immutable: timeline, franchise, year, slugs. Mutable: the counting columns, until the season is complete.
- Rows: 1,792 per completed timeline.
- Created at season entry, then updated during simulation.

### PlayoffBerth

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `timeline` `CASCADE`; `franchise` `PROTECT`.
- Fields: `season_year`; `seed` `PositiveSmallIntegerField`; `berth` choices `division`, `wild_card`.
- Unique: `(timeline, season_year, franchise)`; `(timeline, season_year, seed)`.
- Checks: the count of berths matches the profile's playoff format.
- Indexes: `(timeline, season_year)`.
- Immutable once the bracket is built.
- Rows: 8 or 12 per season, a few hundred per timeline.
- Created at the end of the regular season, during simulation.

### TimelinePlayerSeason

Counting stats for 1970–2029 in one timeline. The design's `TimelineSeason` stat name is this model. The clock keeps the name `TimelineSeason`.

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `overlay` `CASCADE`; `franchise` `PROTECT`.
- Fields: `season_year` plus the season-stat block.
- Unique: `(overlay, season_year, franchise)`.
- Checks: the stat-block checks; the year is inside the timeline.
- Indexes: `(franchise, season_year)`; `(overlay, season_year)`.
- Immutable after the season is complete. Mutable while that season is in progress.
- Rows: at most 103,352. That number is maximum capacity, with every taxi slot occupied. Rows are created for occupants, so a timeline that leaves taxi empty stores fewer. Expansion clubs are expected to leave some taxi spots empty.
- Created during simulation, the first week the player participates.

### TimelineAward

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `timeline` `CASCADE`; `franchise` to `UniverseFranchise`, `PROTECT`; `overlay` to `TimelinePlayer`, `CASCADE`, null; `staff` to `TimelineStaff`, `CASCADE`, null.
- Fields: `season_year`; `kind`; `slot` null except all-league.
- Unique: the same per-year uniqueness as `UniverseAward`, scoped by timeline.
- Checks: year in 1970–2029; exactly one recipient.
- Indexes: `(timeline, season_year, kind)`.
- Immutable once written.
- Rows: about 3,500 per timeline if the honor list stays fixed.
- Created at the end of the season, during simulation.

### DevelopmentEvent

The audit trail. The authority for the rating is `PlayerCurrentRating`. The owner is not shown the delta.

- Ownership: timeline.
- Primary key: `BigAutoField`.
- Foreign keys: `overlay` `CASCADE`.
- Fields: `season_year`; `component` `CharField(32)`; `delta` `SmallIntegerField`; `reason` choices `age`, `snaps`, `injury`, `coaching`; `event_index` `PositiveIntegerField`.
- Unique: `(overlay, season_year, component, event_index)`.
- Checks: delta between −20 and 20; component is one of the 33.
- Indexes: `(overlay, season_year)`.
- Immutable once written. The draw at `event_index` uses the stable coordinates. It is not a private timeline seed.
- Rows: a few per player-season when a component actually moves. Not one per snap.
- Created during simulation.

## What is intentionally not a table

| Idea | Why it is not a model |
|------|------------------------|
| One person table with shared and timeline columns | A second timeline would duplicate biography or collide with the first timeline's ratings. |
| Copying every future prospect into a new timeline | Violates the overlay rule. Class rows stay on `DraftClass` and `UniversePlayer`. |
| Writing the roster onto `leagues.Team` | The catalog row is shared. Two owners would overwrite each other. |
| Entity-attribute-value ratings | Rejected for the 33 components. Scout readings are the only normalized component rows. |
| A generic transaction ledger | Stints, contracts, draft picks, expansion picks, injuries, awards, and the explicit `Trade` / `TradeAsset` pair cover movement. `Trade` exists because a pick-only deal has no stint. |
| A retirement table | `TimelinePlayer.status` and `retired_year`, plus a closed stint and a retired contract. |
| A stored season seed or decision fingerprint seed | The draw is recomputed from the release seed and the coordinates on `SimulationAuditRecord`. The snap outcome is replayed from `GameKickoffState` and the ordered log. |
| Overall rating | Derived at read time. |

## Implementation phases

Each phase is a reviewable schema change in the two new apps. No phase migrates `leagues` data, writes `Team`, or generates people. Rollback of a phase is reversing its migrations, and only when later phases that foreign-key into it have already been reversed. The safe boundary is the database after that phase's migrations and before the next phase starts.

Existing `League`, `Conference`, `Division`, `Team`, and `MovementPath` rows remain untouched in every phase.

### 1. Shared release and franchise history

- Models: `UniverseRelease`, `UniverseFranchise`, `FranchiseSeasonMembership`, `FranchiseHistoryEvent`, `HistoricalStanding`.
- Depends on: `leagues.League` existing. `catalog_team` stays null.
- Validation in this phase: generic structure only. A founding franchise begins in its release's first season, an expansion franchise begins later, membership and history years stay inside the franchise and the release, and historical years are the ten seasons before the release starts. The confirmed schedule is not a database check.
- Confirmed release, checked at publication in phase 8 and not by this migration: 24 founding franchises; playable seasons 1970–2029; expansion to 28 franchises in 1976; expansion to 32 franchises in 1996; the accepted conference and division alignment; 1,792 franchise-season memberships; `owner_selectable` true on every one of those active memberships; no bot-only franchise-season; no history events.
- Rows: 1 release, 32 franchises, 1,792 memberships, 0 history events, about 240 historical standings.
- League data: untouched.
- Rollback: drop these tables. The catalog is unchanged.

### 2. Shared people and rating profiles

- Models: `College`, `UniversePlayer`, `PlayerRatingProfile`, `UniverseGM`, `GMRatingProfile`, `UniverseCoach`, `CoachRatingProfile`, `UniverseCollegeSeason`, `UniverseProStint`, `UniverseProSeason`, `UniverseStaffPost`, `UniverseAward`.
- Depends on: phase 1 franchises, for origin franchise and pro stints.
- Validation: one profile per person; every component 0–100 and initial at or under potential; 19-position enum; 1970–2029 prospects have null origin pick fields; trait and staff-signature checks; college seasons are four per player when that default is on. Phase 2 does not make that count of four a database invariant.
- Technical notes from implementation, not game-rule changes: `draft_class` and `origin_profile` are added with phase 3, when `DraftClass` and `EraRuleProfile` exist. The `(draft_class, board_rank)` unique constraint arrives with that foreign key. `UniverseAward` stores `release` so one honor per release, season, kind, and slot can be a real unique key; non-all-league rows use an empty `slot` because MySQL treats nulls as distinct. History rows point at `UniverseFranchise` with `PROTECT`. Shared pro years are the ten seasons before the release's first playable season, not a hardcoded 1960–1969 check. `UniverseStaffPost.role` accepts every role named in the design or in this proposal: GM posts may be `gm`, `personnel_director`, `scout`, or `college_coach`; coach posts may be `head_coach`, `offensive_coordinator`, `defensive_coordinator`, `position_coach`, or `college_head_coach`. The design's `origin_draft_franchise` is `UniversePlayer.origin_franchise`. The design's single award `staff` reference is `gm` or `coach`. Django's cross-database index name limit is 30 characters, so the three franchise indexes are `universe_prostint_fr_idx`, `universe_proseason_fr_idx`, and `universe_staffpost_fr_idx` on the columns this proposal lists. Every shared staff post ends no later than `debut_year - 1`. A post that names a Federation franchise also ends before the release's first playable season. An incoming person may keep an external or college post through the season before debut. `games` versus `schedule_length`, including college schedule length, waits for a later rule phase. Phase 2 does not cap college games at 12. Initial-staff signature uniqueness and the exact debut age bands stay publication checks. The database age check is only that debut falls between 20 and 80 years after birth. Published-release immutability remains `clean`, `save`, and instance `delete`, not a database trigger. Bounded lists use the explicit ordered columns already named in this proposal.
- Rows: 21,948 players and the same number of rating profiles, 87,792 college seasons, 11,520 historical pro seasons, 462 GMs, 1,148 coaches, and their profiles, once a generator runs. The migration itself inserts nothing.
- League data: untouched.
- Rollback: drop the people tables. Phase 1 remains.

### 3. Era and expansion rules

- Models: `EraRuleProfile`, `EraCollegeWeight`, `DraftClass`, `ExpansionEvent`, `ExpansionClub`, `OpeningAssignment`, `OpeningContract`, `OpeningStaffAssignment`.
- Depends on: phases 1 and 2.
- Validation: nine profiles; quota columns sum to 40, 45, 47, or 53 and taxi columns sum to 8; spans do not overlap; expansion checks `16 + 17 + 7 = 40` and `26 + 7 + 20 = 53`, with veteran totals 64 and 104; the undrafted multiplier is 2 except for 3 in 1996; no free-agent reserve is required; opening assignment counts; a 1995-or-later profile with an empty `cap_limit` fails the publish service and does not fail merely because the column exists.
- Rows: 9 profiles, 60 draft-class headers, 2 expansion events, 8 expansion clubs, 1,272 assignments, 1,152 opening contracts, 96 staff assignments, plus college weights.
- League data: untouched.
- Rollback: drop these tables. People and franchises remain. Draft-class foreign keys on players must be added in this phase, so reversing it also clears `UniversePlayer.draft_class`.

### 4. Timeline core

- Models: `Timeline`, `TimelineSeason`, `OwnerDirective`, `TimelineExpansionChoice`, `ExpansionPin`. `SimulationAuditRecord` waits for phase 7, because it points at `ScheduleGame`.
- Depends on: phase 1 and phase 3.
- Validation: season index maps to 1970–2029; release is published; `decision_audit` is not required to create a timeline; the timeline has no seed column, so a decision hash cannot reroll a season.
- Rows: none until a user starts a campaign. Then 1 timeline, 1 season, and the opening directives.
- League data: untouched.
- Rollback: drop the timeline app's core tables. Shared rows remain. Any later timeline tables must already be gone.

### 5. Timeline player and staff overlays

- Models: `TimelinePlayer`, `PlayerCurrentRating`, `TimelineScoutReport`, `TimelineScoutComponent`, `TimelineStaff`, `GMCurrentRating`, `CoachCurrentRating`, `TimelineStaffScoutReport`, `TimelineStaffScoutComponent`, `StaffStint`.
- Depends on: phase 4 and the shared people.
- Validation: unique `(timeline, player)`; no archive overlays; current values start equal to initial and never exceed potential; a new timeline has overlays only for the opening cohort and, once the opening draft starts, the 1970 class.
- Rows: none in the migration. A started timeline has 1,272 player overlays and 280 staff overlays before later classes exist.
- League data: untouched.
- Rollback: drop overlay tables. Timeline headers remain. Shared people remain.

### 6. Contracts, injuries, drafts, and transactions

- Models: `RosterStint`, `DepthChartEntry`, `SpecialTeamsRole`, `TimelineContract`, `Injury`, `DraftSlot`, `TimelineDraftPick`, `Trade`, `TradeAsset`, `ExpansionProtection`, `TimelineExpansionPick`, `FreeAgentOffer`.
- Depends on: phase 5.
- Validation: one open stint and one active contract; expansion losses are 2 or 3 in 1976 and 3 or 4 in 1996, with no club above the maximum; expansion picks are 64 and 104; the active-roster gate uses shared veterans, rookies, and reserved undrafted prospects, and it does not treat taxi or free agents as required; draft slots equal that year's class size.
- Rows: none in the migration. A full timeline later holds 16,028 draft picks, 168 expansion picks, and the contract and injury volumes in the design.
- League data: untouched.
- Rollback: drop these tables. Overlays and shared identity remain. No roster has been written to `Team`.

### 7. Statistics and awards

- Models: `ScheduleGame`, `GameKickoffState`, `GameResult`, `SimulationAuditRecord`, `Standing`, `PlayoffBerth`, `TimelinePlayerSeason`, `TimelineAward`, `DevelopmentEvent`.
- Depends on: phase 4 franchises-in-a-timeline, phase 5 overlays, and phase 3 profiles for schedule length and playoff size.
- Validation: regular-season game count matches `N(year) * schedule_length / 2`; `games` and `starts` fit the profile; award kinds match the shared list; development deltas fit −20 to 20; a played game has one kickoff state; audit hashes are not treated as a substitute for that state; snap detail is absent unless `persist_snap_draws` is on.
- Rows: none in the migration. A full timeline later holds 14,280 regular-season games plus playoffs, the same number of kickoff states, 1,792 standings, and at most 103,352 player-seasons if every taxi slot is occupied.
- League data: untouched.
- Rollback: drop the stat tables. Transactions and people remain. Results on a user's timeline disappear with the drop and are not stored anywhere else.

### 8. Deterministic generation and validation

- Models: none. This phase fills `catalog_fingerprint` and `validation_fingerprint` and refuses `status = published` when a check fails.
- Depends on: phases 1–3. It does not need timeline tables to publish a release. Timeline season entry reuses the expansion inequality.
- Validation: the confirmed first release must contain 24 founding franchises, playable seasons 1970–2029, expansion to 28 franchises in 1976, expansion to 32 franchises in 1996, the accepted conference and division alignment, and 1,792 franchise-season memberships with `owner_selectable` true on every active row. No bot-only franchise-season is scheduled. Expansion franchises in that release enter in 1976 or 1996. The same gate checks the franchise fingerprint, quota sums, staff signatures, expansion arithmetic with taxi excluded, prospect counts, and the rule that no origin pick is baked onto a 1970–2029 prospect. HMAC construction for person keys uses the release seed and a named key. It does not use `random.Random`. Phase 1 does not implement this validator. The schema stays usable for a corrected or extended release without a migration that only changes the horizon.
- Rows: updates the one release row's fingerprint columns when a generator eventually runs. This document does not run it.
- League data: untouched.
- Rollback: set the release back to `draft` and clear the fingerprints. No table is dropped.

### 9. Administrative inspection tools

- Models: none. Read-only Django admin for the shared tables, and read-only admin for a timeline's overlays, contracts, and results.
- Depends on: whichever earlier phases have been migrated. Admin registration can follow each phase; it does not lead it.
- Validation: an admin save on a published universe row is rejected. Inspecting a timeline cannot write `UniversePlayer` or `Team`.
- Rows: none.
- League data: untouched. The existing league admin stays as it is.
- Rollback: remove the admin registrations. Tables stay.

### 10. Release generation

- Models: none. A management command, not written here, would build one `draft` release, write the shared rows, run phase 8, and publish.
- Depends on: phases 1–3 and 8. Timeline phases are not required to publish.
- Validation: the same publication gate. A failed gate leaves the release in `draft` and does not create timeline rows.
- Rows: the shared counts in phases 1–3, only when that command is later run on purpose.
- League data: untouched. The command must not call the 32-team upsert as a substitute for the 24, 28, and 32 season memberships.
- Rollback: if no timeline pins the release, delete shared children from the leaves inward and then delete the release. Sibling `PROTECT` links mean this is an ordered delete, not one `release.delete()` call. A published release pinned by a timeline cannot be deleted.

## Decisions that still need approval

These do not block the tables above. The columns already store either outcome.

| Decision | What the model does now | What still needs a product choice |
|----------|-------------------------|-----------------------------------|
| Snap-level audit detail | `SimulationAuditRecord` stores the ordered event and optional hashes. `GameKickoffState` stores one snapshot per game. `Timeline.persist_snap_draws` defaults to false. | Whether debugging should also persist every snap's detail. Turning it on changes volume, not the seed, and it is not required to replay from kickoff. |
| Multi-human expansion award | `selection_method` can store `automatic` or `fair_draw`. Single-player apply is `automatic`. | Whether the fair HMAC draw is the method when several humans apply. |
| A campaign that starts in 1976 or 1996 | The normal game begins in 1970. Phase 1 does not store later-start rules. | Whether that mode exists, and what happens to the skipped seasons. Store the rules in later-phase timeline-start configuration. Do not infer them from `owner_selectable`. |
| Permanent franchise names | `display_name` is the placeholder slug text. `catalog_team` is null. | The real names, cities, and slugs, and when a catalog `Team` row may be linked. Linking still must not put roster data on `Team`. |
| Cap, minimum salary, rookie-slot percents, and sim coefficients | `cap_limit`, `minimum_salary`, and `rookie_slot_scale` are nullable. Weights and development coefficients are not person columns. | The numbers. A 1995 profile still cannot publish with an empty cap. That is a data check, not a schema change. |

## Phase 1 readiness

Phase 1 adds only these five models:

- `UniverseRelease`
- `UniverseFranchise`, including stored `origin` (`founding` or `expansion`)
- `FranchiseSeasonMembership`, including stored `owner_selectable`
- `FranchiseHistoryEvent`
- `HistoricalStanding`

The migration creates the tables and leaves them empty. A later generator fills the confirmed release with `founding-01` through `founding-24`, `expansion-1976-01` through `expansion-1976-04`, and `expansion-1996-01` through `expansion-1996-04`. `catalog_team` stays null until a catalog link is chosen. `display_name` can hold the placeholder slug. `FranchiseHistoryEvent` has no scheduled rows. `HistoricalStanding` for that release is one row per founding franchise per year from 1960 through 1969, 240 rows. `seed` is a `BigIntegerField`. Conference and division membership are slug strings plus `slot_order` and `owner_selectable`. Every active membership in that release has `owner_selectable` true. The 1970 and 1976 shapes are not the live 32-team generator tree, and they are not check constraints on the membership table.

The database does not require 24 founding clubs, a 1970–2029 horizon, debut years of 1970, 1976, or 1996, two named conferences, divisions `d1`–`d4`, or the era slot limits. Those rules remain the confirmed release. Phase 8 is the publication gate that refuses any other shape for that release. Phase 1 does not implement that gate.

No open decision changes the columns of those five models. Permanent names and cities are strings written into columns that already exist, and they can wait. The salary cap, minimum salary, rookie-slot percents, simulation coefficients, development formulas, expansion-draft counts, and replay storage do not appear on these five models. Expansion rules are phase 3. Replay tables are phase 7.

## Document status

- Phase 1 models, the `universe` app, and its initial migration are implemented. Later models in this file remain proposals. The migration inserts no rows.
- Keeps `leagues.Team` as catalog data.
- Stores the 33 components as explicit columns on `PlayerRatingProfile` and `PlayerCurrentRating`.
- Uses the expansion arithmetic from the design: 16 + 17 + 7 = 40 in 1976, and 26 + 7 + 20 = 53 in 1996. Taxi capacity is optional. Timeline free agents are not part of the publication check.
- Uses stable draw coordinates. `GameKickoffState` plus `SimulationAuditRecord` are the MVP replay records. A draw can be recomputed from the coordinates. A snap outcome cannot, unless that kickoff state or a checkpoint is available.
- Phase 1 stores `origin` and `owner_selectable`. Publication of the confirmed 1970–2029 release, including its 24, 28, and 32 franchise schedule and 1,792 memberships, waits for phase 8.
