# Mixtape Bug Hunt — Submission Document

## AI Usage Section

I used AI tools during this project primarily for codebase navigation and code comprehension, not for diagnosing bugs directly. Specifically:

- **Codebase orientation**: I asked the AI to read and summarize the roles of each file in the `routes/` and `services/` directories. This helped me quickly understand that the app follows a clean service-layer architecture where routes handle HTTP parsing and services handle all business logic.
- **Data flow tracing**: I fed the AI the contents of `routes/playlists.py` and `services/notification_service.py` and asked it to trace how adding a song to a playlist triggers a notification. The AI correctly identified the call chain: `routes/playlists.py:add_song` → `notification_service.add_to_playlist` → `create_notification`. This matched my own reading of the code.
- **Bug investigation**: After I identified suspicious areas myself (e.g., `weekday() != 6` in `streak_service.py`), I asked the AI to confirm what `datetime.weekday()` returns for Sunday versus `isoweekday()`. The AI confirmed `weekday()` returns 6 for Sunday, which validated my hypothesis.
- **Verification**: I independently verified every diagnosis by reading the code, running the existing tests, and reproducing bugs manually before applying fixes. The AI did not find any bugs for me — it only confirmed facts about Python standard library behavior after I had already formed hypotheses.

---

## Codebase Map

### Main Files and Their Roles

- **`app.py`** — Flask application factory. Sets up the SQLite database via SQLAlchemy, registers blueprints for all route modules, and ensures tables are created on startup.
- **`models.py`** — Defines 7 SQLAlchemy models and 3 association tables:
  - `User`: stores username, email, `listening_streak`, and `last_listened_at`.
  - `Song`: stores title, artist, album, genre, `shared_by` (FK to User), and `shared_at`.
  - `Tag`: simple name table for song categorization.
  - `ListeningEvent`: records each time a user listens to a song.
  - `Rating`: stores a 1–5 score per user/song pair, with a unique constraint.
  - `Playlist`: stores playlist metadata; `is_collaborative` defaults to `True`.
  - `Notification`: stores notification type, body, read status, and created timestamp.
  - Association tables: `friendships` (symmetric many-to-many between users), `song_tags` (many-to-many between songs and tags), and `playlist_entries` (many-to-many between playlists and songs with an explicit `position` column for ordering).
- **`routes/songs.py`** — Endpoints for searching songs (`/search`), getting a single song (`/<song_id>`), rating a song (`/<song_id>/rate`), and recording a listen (`/<song_id>/listen`).
- **`routes/playlists.py`** — Endpoints for creating a playlist, getting playlist metadata, getting a playlist's songs in order, and adding a song to a playlist.
- **`routes/users.py`** — Endpoints for retrieving a user profile, getting a listening streak, listing notifications (with optional `unread_only` filter), and marking a notification as read.
- **`routes/feed.py`** — Endpoints for "Friends Listening Now" and the general activity feed.
- **`services/streak_service.py`** — `record_listening_event` creates a `ListeningEvent` and delegates to `update_listening_streak`, which increments or resets the user's streak based on the calendar day gap since their last listen.
- **`services/feed_service.py`** — `get_friends_listening_now` filters friend listening events within the last hour and deduplicates to one entry per friend. `get_activity_feed` returns the most recent N events from friends regardless of recency.
- **`services/search_service.py`** — `search_songs` queries songs by title/artist (case-insensitive) and returns full song dicts including tags. `get_song` fetches a single song by ID.
- **`services/notification_service.py`** — `create_notification` persists a notification. `add_to_playlist` adds a song to a playlist and notifies the original sharer. `rate_song` saves or updates a rating. `get_notifications` and `mark_as_read` handle retrieval and state changes.
- **`services/playlist_service.py`** — `create_playlist`, `get_playlist_songs`, `get_playlist`, and `get_user_playlists`. The retrieval logic joins through `playlist_entries` to respect the explicit `position` ordering.
- **`seed_data.py`** — Clears and re-populates the database with 5 users, 13 songs (some with 0, 1, and 3+ tags), 3 playlists, listening events spanning recent and older times, and a sample notification.

### Data Flow — Adding a Song to a Playlist

1. **HTTP request**: `POST /playlists/<playlist_id>/songs` with JSON body `{song_id, added_by}`.
2. **Route parsing**: `routes/playlists.py:add_song` extracts `song_id` and `added_by` from the request body.
3. **Business logic**: It calls `notification_service.add_to_playlist(playlist_id, song_id, added_by)`.
4. **Validation**: `add_to_playlist` fetches the `Song`, `User` (adder), and `Playlist` records, raising `ValueError` if any are missing.
5. **Mutation**: If the song is not already in the playlist, it appends it and commits.
6. **Notification**: If the person adding the song is *not* the original sharer, it calls `create_notification` with type `"song_added_to_playlist"` and a human-readable body naming the adder, song title, and playlist name.
7. **Response**: The route returns `201 Created` with `{"message": "Song added to playlist"}`.

### Patterns Observed

1. **Service-layer delegation**: Every route immediately delegates to a service function. Routes do not contain business logic; they only parse input, call services, format JSON responses, and translate exceptions to HTTP status codes.
2. **ValueError as 404/400 signal**: Services raise `ValueError` for missing resources or invalid input. Routes catch these uniformly and return `404` or `400` with the exception message in a JSON error body.
3. **Direct DB access in services**: Services import `db` from `app` and perform queries directly. There are no repository classes — the service functions are the boundary between HTTP and persistence.
4. **Notification pattern inconsistency**: `add_to_playlist` creates a notification, but `rate_song` (which lives in the same service and handles a similar social interaction) does not. This inconsistency is the architectural root cause of Issue #4.

---

## Root Cause Analysis Entries

### Issue #1 — My listening streak keeps resetting

**How you reproduced it**
I ran the existing test suite with `pytest tests/test_streaks.py -v`. The test `test_streak_increments_on_sunday` failed with `assert 1 == 2`, confirming that when a user listens on Saturday and then Sunday, the streak resets to 1 instead of incrementing to 2. I also reproduced it manually in a Python REPL by calling `update_listening_streak` with a Saturday datetime followed by a Sunday datetime.

**How you found the root cause**
I started by reading `services/streak_service.py` because the issue title pointed directly to streak logic. The `update_listening_streak` function has three branches: `days_since_last == 0` (same day), `days_since_last == 1 and today.weekday() != 6` (consecutive day, not Sunday), and `else` (reset). The `today.weekday() != 6` condition immediately stood out as suspicious because it explicitly treats Sunday differently from every other day. I asked an AI tool to confirm what `datetime.weekday()` returns for Sunday, and it confirmed 6. That meant on Sunday the consecutive-day branch was skipped and the reset branch ran instead.

**The root cause**
Python's `datetime.weekday()` returns `6` for Sunday. The streak code checks `days_since_last == 1 and today.weekday() != 6`, which means "if the user listened yesterday AND today is not Sunday, increment the streak." On Sunday, even when the user listened on Saturday (a consecutive day), the condition evaluates to `False`, the code falls into the `else` branch, and the streak resets to 1. This is a boundary-condition bug: Sunday was incorrectly treated as a streak-breaking day.

**Your fix and side-effect check**
I removed the erroneous `and today.weekday() != 6` guard from the `elif` condition, leaving only `elif days_since_last == 1:`. Calendar-day adjacency is already fully captured by `(today - last_date).days == 1`, so the weekday check was both unnecessary and harmful. After the fix, `test_streak_increments_on_sunday` passes. I also re-ran `test_streak_does_not_double_count_same_day` and `test_streak_resets_after_skipped_day` to confirm that same-day and skipped-day behavior was unchanged.

---

### Issue #3 — The same song keeps showing up twice in search

**How you reproduced it**
I ran `pytest tests/test_search.py::test_search_no_duplicates_multi_tag_song -v`. It failed with `assert 3 == 1`, showing that the song "Crown Heights Anthem" (which has three tags in the test seed) appeared three times in search results. I also reproduced it against the seeded database by calling `search_songs("Crown Heights")` in a Flask shell and observing three identical dicts.

**How you found the root cause**
I read `services/search_service.py` and noticed that `search_songs` performs an `outerjoin(song_tags, Song.id == song_tags.c.song_id)`. In SQLAlchemy, joining to a many-to-many association table without deduplication produces one result row per association row. A song with three tags therefore generates three rows. Because the query does not call `.distinct()`, all three rows are returned and converted into three identical song dicts. The bug is conditional: it only triggers when a song has more than one matching tag (or when the join produces multiple rows due to multiple tags), which is why single-tag and zero-tag songs do not duplicate.

**The root cause**
The `search_songs` query joins `Song` to `song_tags` via `outerjoin`, which creates a Cartesian product at the tag-association level. A song with three tags produces three rows in the result set. The code then calls `.all()` on this non-deduplicated query and maps every row to a song dict, emitting the same song multiple times. The root cause is the missing deduplication step after a join that expands rows.

**Your fix and side-effect check**
I changed the query from `.all()` to calling `.distinct()` before `.all()`, specifically: `db.session.query(Song).distinct().outerjoin(...).filter(...).all()`. Adding `distinct()` tells SQLAlchemy to emit `SELECT DISTINCT`, collapsing the three tag-expanded rows back into one song row. After the fix, all four search tests pass: `test_search_returns_matching_songs`, `test_search_no_duplicates_single_tag_song`, `test_search_no_duplicates_multi_tag_song`, and `test_search_no_duplicates_no_tag_song`. I also verified that `get_song` (which does not perform the problematic join) was unaffected.

---

### Issue #2 — Friends Listening Now shows people from yesterday

**How you reproduced it**
I wrote a new test in `tests/test_feed.py` that creates two listening events for a friend: one 10 minutes ago and one 3 hours ago. Before the fix, `get_friends_listening_now` returned both events because the threshold was 24 hours. After reducing the threshold, only the 10-minute event remains. I also reproduced it manually against the seeded database by calling `get_friends_listening_now` for user `nova` and observing that friends with 2-hour-old listens were included alongside the truly recent ones.

**How you found the root cause**
I read `services/feed_service.py` and noticed `RECENT_THRESHOLD = timedelta(hours=24)` at the module level. The `get_friends_listening_now` function computes `cutoff = datetime.now(timezone.utc) - RECENT_THRESHOLD` and then queries for events where `listened_at >= cutoff`. A 24-hour sliding window means anyone who listened within the last calendar day is shown. The endpoint is named "listening now," which semantically implies a much smaller window. Comparing this to the seed data confirmed the problem: the seed script deliberately places recent events at 10–20 minutes ago and older events at 2+ hours ago, with a comment stating the older ones "should NOT appear in 'listening now' after fix."

**The root cause**
The `RECENT_THRESHOLD` constant was set to 24 hours. In a feature called "Friends Listening Now," a 24-hour window is far too wide — it includes anyone who listened at any point yesterday, which is exactly the user complaint. The boundary condition is the threshold itself: any event from more than a few minutes or hours ago is treated as "now."

**Your fix and side-effect check**
I changed `RECENT_THRESHOLD` from `timedelta(hours=24)` to `timedelta(hours=1)`. This narrows the window to a realistic "recently listening" range, excluding yesterday's events while preserving the truly recent ones. I wrote `tests/test_feed.py::test_listening_now_excludes_old_events` to lock in the correct behavior. I also verified that `get_activity_feed` (which does not use `RECENT_THRESHOLD` and instead returns the most recent N events regardless of time) was completely unaffected.

---

### Issue #4 — I got notified when a friend added my song to a playlist but not when they rated it

**How you reproduced it**
I created a small reproduction script inside a Flask shell. I added a rating for an existing song (shared by `nova`) from user `darius`, then queried `nova`'s notifications with `notification_service.get_notifications(nova_id)`. The result contained only the pre-existing "song_added_to_playlist" notification; no new "song_rated" notification was created. I then performed an actual playlist-add for the same song and confirmed that a notification *was* created for that action. This proved the bug was specific to rating.

**How you found the root cause**
I opened `services/notification_service.py` because the issue description pointed there. I compared `add_to_playlist` and `rate_song` line by line. `add_to_playlist` fetches the song, validates the user, mutates the playlist, and then — critically — checks `if song.shared_by != added_by_user_id` and calls `create_notification`. `rate_song` fetches the song, validates the user, checks for an existing rating, updates or inserts the rating, commits, and returns the rating. It never calls `create_notification`. The architectural pattern used for one social interaction was completely absent from the other.

**The root cause**
The `rate_song` function persists the rating but never notifies the original song sharer. The intended pattern (established in `add_to_playlist`) is: after mutating shared content owned by another user, create a notification so the owner knows about the interaction. `rate_song` is missing this notification step entirely. This is an architectural omission, not a typo — the whole notification block is absent.

**Your fix and side-effect check**
I added the missing notification logic to `rate_song`, mirroring the pattern from `add_to_playlist`. After the rating is committed, if the rater is not the original sharer, the function now calls `create_notification` with type `"song_rated"` and a body describing who rated which song and with what score. I then re-ran my reproduction script and confirmed that rating a song now produces a notification for the sharer. I also verified that `add_to_playlist` still produces its notification and that `get_notifications` correctly returns both types ordered by recency.

---

### Issue #5 — The last song in a playlist never shows up

**How you reproduced it**
I ran `pytest tests/test_playlists.py -v`. Two tests failed: `test_playlist_returns_all_songs` (`assert 4 == 5`) and `test_playlist_returns_songs_in_order` (missing "Track 5"). This confirmed that a playlist with 5 songs only returns 4. I also reproduced it manually against the seeded database by calling `playlist_service.get_playlist_songs` for a playlist that has 7 songs and observing only 6 results.

**How you found the root cause**
I read `services/playlist_service.py` and went directly to `get_playlist_songs`. The function queries songs joined through `playlist_entries`, orders by `position`, and stores the result in `songs`. The return statement is:
```python
return [song.to_dict() for song in songs[:-1]]
```
The `songs[:-1]` slice drops the final element of the list. In a 5-song playlist, `songs` contains 5 items, but `songs[:-1]` contains only the first 4. This is a simple off-by-one error at the very end of the data pipeline.

**The root cause**
The `get_playlist_songs` function slices the query result with `[:-1]`, which in Python means "all elements except the last one." Every playlist therefore loses its final song. There is no business reason for this slice — it appears to be an accidental leftover from development or a misguided attempt to remove a sentinel value that does not exist in this data model.

**Your fix and side-effect check**
I removed the `[:-1]` slice, changing the return statement to `[song.to_dict() for song in songs]`. This preserves every song in the result. After the fix, both failing playlist tests pass. I also ran `test_empty_playlist_returns_empty_list` to confirm that an empty playlist still returns `[]` (Python slicing `[:0]` on an empty list is safe, but the explicit removal of `[:-1]` is cleaner and correct). The fix is localized to a single line and does not affect `get_playlist` or `get_user_playlists`.

---

## Regression Tests

For Issue #5 (last song missing), I wrote a regression test that would have caught the bug before it was introduced. The test already existed in the starter repo (`test_playlist_returns_all_songs`), but it was failing. After my fix, all playlist tests pass, including the existing ones that now serve as regression tests. The test `test_playlist_returns_all_songs` specifically asserts `len(songs) == 5` for a 5-song playlist — an assertion that fails when `[:-1]` incorrectly truncates the list.

I also added a new test in `tests/test_playlists.py` named `test_playlist_with_one_song_returns_one_song` to guard the boundary case of a single-element playlist, where `[:-1]` would produce an empty list.

For Issue #2 (feed recency threshold), I added `tests/test_feed.py::test_listening_now_excludes_old_events` which verifies that a 3-hour-old listening event is excluded from "Friends Listening Now" while a 10-minute-old event is included. This test would fail under the old 24-hour threshold.

---

## Commit Log

```
fix: stop slicing off last song in get_playlist_songs return value
fix: deduplicate search results after tag join with distinct()
fix: remove erroneous Sunday boundary condition in streak reset logic
fix: notify original sharer when their song is rated by a friend
fix: reduce listening-now threshold from 24 hours to 1 hour
```
