"""归档去重 + 选题轮换测试。"""

import json

from ghtrend.archive import connect, mark_covered, record_snapshot, select_topics
from ghtrend.models import TrendingRepo

ANGLES = [{"id": a, "name": a} for a in ("intro", "architecture", "success_story", "use_case")]

T = "2026-10-12T12:00:00+00:00"


def _repo(slug, ts=T):
    return TrendingRepo(repo=slug, url=f"https://github.com/{slug}", captured_at=ts)


def _conn(tmp_path):
    return connect(tmp_path / "archive.db")


def test_record_snapshot_dedup(tmp_path):
    conn = _conn(tmp_path)
    assert record_snapshot(conn, [_repo("a/b"), _repo("c/d")]) == 2
    # 同批重跑（同一 captured_at）：不再新增，也不重复递增计数
    assert record_snapshot(conn, [_repo("a/b"), _repo("c/d")]) == 0
    # 再上榜（更新的 captured_at）：times_trending 递增、first_seen 稳定、last_seen 更新
    t2 = "2026-10-13T12:00:00+00:00"
    record_snapshot(conn, [_repo("a/b", t2)])
    row = conn.execute("SELECT * FROM trending_archive WHERE repo='a/b'").fetchone()
    assert row["times_trending"] == 2
    assert row["first_seen"] == T
    assert row["last_seen"] == t2
    # 旧快照迟到归档：不影响计数与 last_seen
    record_snapshot(conn, [_repo("a/b", "2026-10-11T00:00:00+00:00")])
    row = conn.execute("SELECT * FROM trending_archive WHERE repo='a/b'").fetchone()
    assert row["times_trending"] == 2
    assert row["last_seen"] == t2


def test_select_filters_fully_covered(tmp_path):
    conn = _conn(tmp_path)
    record_snapshot(conn, [_repo("a/b")])
    mark_covered(conn, "a/b", "intro")
    mark_covered(conn, "a/b", "architecture")
    mark_covered(conn, "a/b", "success_story")
    mark_covered(conn, "a/b", "use_case")
    assert select_topics(conn, ANGLES, limit=5) == []


def test_select_rotates_angles(tmp_path):
    conn = _conn(tmp_path)
    record_snapshot(conn, [_repo("a/b")])
    first = select_topics(conn, ANGLES, limit=1)[0]
    assert first.repo == "a/b"
    assert first.angle == "intro"
    mark_covered(conn, "a/b", first.angle)
    second = select_topics(conn, ANGLES, limit=1)[0]
    assert second.angle == "architecture"  # 角度轮换，不重复


def test_select_prefers_fewer_times_trending(tmp_path):
    conn = _conn(tmp_path)
    t2 = "2026-10-13T12:00:00+00:00"
    record_snapshot(conn, [_repo("old/one")])
    record_snapshot(conn, [_repo("old/one", t2)])  # times_trending = 2
    record_snapshot(conn, [_repo("new/two", t2)])  # times_trending = 1
    topics = select_topics(conn, ANGLES, limit=1)
    assert topics[0].repo == "new/two"


def test_mark_covered_persists_and_sets_url(tmp_path):
    conn = _conn(tmp_path)
    record_snapshot(conn, [_repo("a/b")])
    mark_covered(conn, "a/b", "intro", article_url="https://x/post")
    row = conn.execute("SELECT * FROM trending_archive WHERE repo='a/b'").fetchone()
    assert json.loads(row["covered_angles"]) == ["intro"]
    assert row["article_url"] == "https://x/post"
    # 重复 mark 不重复登记
    mark_covered(conn, "a/b", "intro")
    row = conn.execute("SELECT covered_angles FROM trending_archive WHERE repo='a/b'").fetchone()
    assert json.loads(row["covered_angles"]) == ["intro"]
