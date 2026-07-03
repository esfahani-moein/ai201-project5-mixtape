"""
tests/test_feed.py — Mixtape

Tests for feed logic.
"""

import pytest
from datetime import datetime, timedelta, timezone
from app import create_app, db
from models import User, Song, ListeningEvent
from services.feed_service import get_friends_listening_now


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def seed_feed(app):
    with app.app_context():
        user = User(username="nova", email="nova@example.com")
        friend = User(username="darius", email="darius@example.com")
        db.session.add_all([user, friend])
        db.session.flush()

        # Establish friendship (bidirectional)
        from models import friendships
        db.session.execute(friendships.insert().values(user_id=user.id, friend_id=friend.id))
        db.session.execute(friendships.insert().values(user_id=friend.id, friend_id=user.id))

        song = Song(title="Recent Hit", artist="Band", shared_by=user.id)
        db.session.add(song)
        db.session.flush()

        now = datetime.now(timezone.utc)

        # Friend listened 10 minutes ago — should appear
        recent_event = ListeningEvent(
            user_id=friend.id,
            song_id=song.id,
            listened_at=now - timedelta(minutes=10),
        )
        db.session.add(recent_event)

        # Friend listened 3 hours ago — should NOT appear
        old_event = ListeningEvent(
            user_id=friend.id,
            song_id=song.id,
            listened_at=now - timedelta(hours=3),
        )
        db.session.add(old_event)

        db.session.commit()
        yield {"user": user, "friend": friend, "song": song}


def test_listening_now_excludes_old_events(app, seed_feed):
    """Friends Listening Now should not include events from hours ago."""
    with app.app_context():
        feed = get_friends_listening_now(seed_feed["user"].id)
        assert len(feed) == 1
        assert feed[0]["friend"]["username"] == "darius"
        assert feed[0]["song"]["title"] == "Recent Hit"
