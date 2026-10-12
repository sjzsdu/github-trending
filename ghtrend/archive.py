"""SQLite 归档 + 选题（去重 + 角度轮换）。

归档表 trending_archive：(repo, language, first_seen, last_seen,
times_trending, covered_angles, article_url)。

选题规则（调研结论 F2）：
  - 只选仍有未使用角度的 repo（已覆盖 repo+angle 组合被过滤）；
  - 角度按配置顺序轮换（rotation）；
  - 优先 times_trending 少（新面孔）、last_seen 近（热度仍在）的 repo。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import SelectedTopic, TrendingRepo

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trending_archive (
    repo            TEXT PRIMARY KEY,
    language        TEXT NOT NULL DEFAULT '',
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    times_trending  INTEGER NOT NULL DEFAULT 1,
    covered_angles  TEXT NOT NULL DEFAULT '[]',
    article_url     TEXT NOT NULL DEFAULT ''
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA)
    return conn


def record_snapshot(conn: sqlite3.Connection, repos: list[TrendingRepo]) -> int:
    """把快照 upsert 进归档；返回新增 repo 数。

    幂等：同一快照（captured_at 不晚于已记录的 last_seen）重复归档不会
    重复递增 times_trending。
    """
    new = 0
    for r in repos:
        cur = conn.execute(
            "SELECT first_seen, last_seen FROM trending_archive WHERE repo = ?", (r.repo,)
        )
        row = cur.fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO trending_archive (repo, language, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?)",
                (r.repo, r.language or r.language_query, r.captured_at, r.captured_at),
            )
            new += 1
        elif r.captured_at > row["last_seen"]:
            conn.execute(
                "UPDATE trending_archive SET last_seen = ?, times_trending = times_trending + 1 "
                "WHERE repo = ?",
                (r.captured_at, r.repo),
            )
    conn.commit()
    return new


def archive_snapshots(snapshots_dir: str | Path, db_path: str | Path) -> tuple[int, int, int]:
    """归档目录下全部快照（按时间序），返回 (files, repos, new)。"""
    from .scrape import load_snapshot

    files = sorted(Path(snapshots_dir).glob("*.json"))
    conn = connect(db_path)
    try:
        repos_total, new_total = 0, 0
        for f in files:
            repos = load_snapshot(f)
            repos_total += len(repos)
            new_total += record_snapshot(conn, repos)
    finally:
        conn.close()
    return len(files), repos_total, new_total


def mark_covered(
    conn: sqlite3.Connection, repo: str, angle: str, article_url: str = ""
) -> None:
    """选题/成文后登记已覆盖角度。"""
    row = conn.execute(
        "SELECT covered_angles FROM trending_archive WHERE repo = ?", (repo,)
    ).fetchone()
    angles = json.loads(row["covered_angles"]) if row else []
    if angle not in angles:
        angles.append(angle)
    conn.execute(
        "UPDATE trending_archive SET covered_angles = ?, article_url = CASE WHEN ? = '' "
        "THEN article_url ELSE ? END WHERE repo = ?",
        (json.dumps(angles, ensure_ascii=False), article_url, article_url, repo),
    )
    conn.commit()


def all_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM trending_archive "
        "ORDER BY times_trending ASC, last_seen DESC, repo ASC"
    ).fetchall()


def select_topics(
    conn: sqlite3.Connection,
    angles: list[dict],
    limit: int = 1,
) -> list[SelectedTopic]:
    """选题：过滤已覆盖 repo+angle，角度轮换，取前 limit 个。

    angles: config 中的 angle profile 列表（[{"id": ..., "name": ...}, ...]），
    顺序即轮换顺序。
    """
    angle_ids = [a["id"] for a in angles]
    angle_names = {a["id"]: a.get("name", a["id"]) for a in angles}
    topics: list[SelectedTopic] = []
    for row in all_rows(conn):
        if len(topics) >= limit:
            break
        covered = set(json.loads(row["covered_angles"]))
        # 角度轮换：在配置顺序中找该 repo 尚未覆盖的第一个角度
        next_angle = next((aid for aid in angle_ids if aid not in covered), None)
        if next_angle is None:
            continue  # 全部角度写过的 repo 跳过
        topics.append(
            SelectedTopic(
                repo=row["repo"],
                angle=next_angle,
                angle_name=angle_names[next_angle],
                first_seen=row["first_seen"],
                times_trending=row["times_trending"],
            )
        )
    return topics
