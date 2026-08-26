"""SQLite persistence with an explicit, inspectable schema."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterator


SCHEMA_VERSION = 10


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("pragma foreign_keys = on")
        connection.execute("pragma busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> None:
        with self.connect() as connection:
            connection.execute("pragma journal_mode = wal")
            connection.executescript("""
                create table if not exists metadata (key text primary key, value text not null);
                create table if not exists projects (
                    id integer primary key, name text not null, slug text not null unique,
                    description text not null default '', created_at real not null, deleted_at real);
                create table if not exists images (
                    id integer primary key, project_id integer not null references projects(id) on delete cascade,
                    sha256 text not null, filename text not null, stored_path text not null,
                    source_group text not null default '', mime text not null, width integer, height integer,
                    split text not null default '' check(split in ('','train','test')),
                    created_at real not null, deleted_at real, unique(project_id, sha256));
                create index if not exists idx_images_project on images(project_id, id);
                create table if not exists questions (
                    id integer primary key, project_id integer not null references projects(id) on delete cascade,
                    key text not null, label text not null, description text not null default '',
                    created_at real not null, unique(project_id, key));
                create table if not exists variants (
                    id integer primary key, question_id integer not null references questions(id) on delete cascade,
                    name text not null, language text not null default 'it', text text not null,
                    mutation_type text not null default 'manual', canonical integer not null default 0 check(canonical in (0,1)),
                    created_at real not null, unique(question_id, name));
                create unique index if not exists idx_one_canonical on variants(question_id) where canonical = 1;
                create table if not exists annotations (
                    image_id integer not null references images(id) on delete cascade,
                    question_id integer not null references questions(id) on delete cascade,
                    value text not null check(value in ('yes','no','uncertain','exclude')),
                    note text not null default '', annotator text not null default 'human', updated_at real not null,
                    agreement text not null default 'single', label_count integer not null default 1,
                    distinct_values integer not null default 1, adjudicated_by text,
                    primary key(image_id, question_id));
                create table if not exists annotation_labels (
                    id integer primary key,
                    image_id integer not null references images(id) on delete cascade,
                    question_id integer not null references questions(id) on delete cascade,
                    annotator text not null collate nocase,
                    value text not null check(value in ('yes','no','uncertain','exclude')),
                    note text not null default '',
                    is_adjudication integer not null default 0 check(is_adjudication in (0,1)),
                    updated_at real not null,
                    unique(image_id, question_id, annotator, is_adjudication));
                create index if not exists idx_labels_case on annotation_labels(question_id, image_id);
                create table if not exists providers (
                    id integer primary key, name text not null unique,
                    kind text not null check(kind in ('ollama','openai')),
                    endpoint text not null, model text not null, is_demo integer not null default 0
                    check(is_demo in (0,1)), created_at real not null);
                create table if not exists runs (
                    id integer primary key, project_id integer not null references projects(id) on delete cascade,
                    provider_id integer not null references providers(id), name text not null,
                    status text not null check(status in ('queued','running','paused','completed','failed','cancelled')),
                    config_json text not null, total integer not null default 0, completed integer not null default 0,
                    error text, runtime_seconds real not null default 0,
                    created_at real not null, started_at real, finished_at real);
                create table if not exists responses (
                    id integer primary key, run_id integer not null references runs(id) on delete cascade,
                    image_id integer not null references images(id) on delete cascade,
                    variant_id integer not null references variants(id) on delete cascade,
                    repetition integer not null default 0, answer text not null, raw text not null,
                    format_valid integer not null default 0, parser text not null, latency_ms integer,
                    prompt_tokens integer, completion_tokens integer, prompt_text text not null,
                    error text, input_sha256 text, input_mime text, input_width integer,
                    input_height integer, input_preprocess text not null default 'original', created_at real not null,
                    unique(run_id, image_id, variant_id, repetition));
                create index if not exists idx_responses_run on responses(run_id, variant_id, image_id);
                create table if not exists image_features (
                    image_id integer primary key references images(id) on delete cascade,
                    extractor_version text not null, brightness real, contrast real,
                    edge_density real, saturation real, phash text, analyzed_at real not null);
            """)
            provider_columns = {row[1] for row in connection.execute("pragma table_info(providers)")}
            if "is_demo" not in provider_columns:
                connection.execute("alter table providers add column is_demo integer not null default 0 check(is_demo in (0,1))")
            project_columns = {row[1] for row in connection.execute("pragma table_info(projects)")}
            if "deleted_at" not in project_columns:
                connection.execute("alter table projects add column deleted_at real")
            image_columns = {row[1] for row in connection.execute("pragma table_info(images)")}
            if "deleted_at" not in image_columns:
                connection.execute("alter table images add column deleted_at real")
            if "split" not in image_columns:
                connection.execute("alter table images add column split text not null default ''")
            feature_columns = {row[1] for row in connection.execute("pragma table_info(image_features)")}
            if "phash" not in feature_columns:
                connection.execute("alter table image_features add column phash text")
            response_columns = {row[1] for row in connection.execute("pragma table_info(responses)")}
            for name, definition in {
                "input_sha256": "text", "input_mime": "text", "input_width": "integer",
                "input_height": "integer", "input_preprocess": "text not null default 'original'",
            }.items():
                if name not in response_columns:
                    connection.execute(f"alter table responses add column {name} {definition}")
            annotation_columns = {row[1] for row in connection.execute("pragma table_info(annotations)")}
            for name, definition in {
                "agreement": "text not null default 'single'", "label_count": "integer not null default 1",
                "distinct_values": "integer not null default 1", "adjudicated_by": "text",
            }.items():
                if name not in annotation_columns:
                    connection.execute(f"alter table annotations add column {name} {definition}")
            # Existing ground truth was written by one reviewer with no record of
            # who else might have disagreed. It becomes that reviewer's own label,
            # so the panel starts from the judgements already made rather than
            # discarding them and asking for the work again.
            connection.execute("""insert into annotation_labels
                (image_id,question_id,annotator,value,note,is_adjudication,updated_at)
                select a.image_id,a.question_id,
                       case when trim(a.annotator)='' then 'human' else a.annotator end,
                       a.value,a.note,0,a.updated_at from annotations a
                where not exists (select 1 from annotation_labels l
                    where l.image_id=a.image_id and l.question_id=a.question_id)""")
            run_columns = {row[1] for row in connection.execute("pragma table_info(runs)")}
            if "runtime_seconds" not in run_columns:
                connection.execute("alter table runs add column runtime_seconds real not null default 0")
            # One best-effort memory sample per run, taken right after its first
            # settled response so the model is definitely loaded. Not a
            # continuous measurement — see fragilevision/performance.py.
            for name, definition in {
                "memory_bytes": "integer", "memory_vram_bytes": "integer", "memory_sampled_at": "real",
            }.items():
                if name not in run_columns:
                    connection.execute(f"alter table runs add column {name} {definition}")
            if "archived_at" not in run_columns:
                # Set aside, not deleted: an archived run keeps every response and
                # stays fully replayable, it just stops cluttering the live ledger
                # and the Arena/Atlas pickers.
                connection.execute("alter table runs add column archived_at real")
            connection.execute("insert into metadata(key,value) values('schema_version',?) on conflict(key) do update set value=excluded.value", (str(SCHEMA_VERSION),))
        self.allow_paused_runs()

    def allow_paused_runs(self) -> None:
        """Widen the runs status check to include 'paused'.

        SQLite cannot alter a CHECK constraint in place, so an existing database
        needs the table rebuilt. Foreign keys are disabled for the swap only:
        responses reference runs by name, and the rename restores that link.
        """
        connection = sqlite3.connect(self.path, timeout=30)
        connection.isolation_level = None
        try:
            definition = connection.execute(
                "select sql from sqlite_master where type='table' and name='runs'").fetchone()
            if not definition or "'paused'" in definition[0]:
                return
            connection.execute("pragma foreign_keys = off")
            columns = ",".join(row[1] for row in connection.execute("pragma table_info(runs)"))
            # executescript() commits any open transaction first, so the rebuild
            # runs as individual statements inside one explicit transaction.
            connection.execute("begin immediate")
            try:
                connection.execute("""create table runs_rebuilt (
                    id integer primary key, project_id integer not null references projects(id) on delete cascade,
                    provider_id integer not null references providers(id), name text not null,
                    status text not null check(status in ('queued','running','paused','completed','failed','cancelled')),
                    config_json text not null, total integer not null default 0, completed integer not null default 0,
                    error text, runtime_seconds real not null default 0,
                    created_at real not null, started_at real, finished_at real)""")
                connection.execute(f"insert into runs_rebuilt ({columns}) select {columns} from runs")
                connection.execute("drop table runs")
                connection.execute("alter table runs_rebuilt rename to runs")
            except Exception:
                connection.execute("rollback")
                raise
            connection.execute("commit")
            broken = connection.execute("pragma foreign_key_check").fetchall()
            if broken:
                raise sqlite3.IntegrityError(f"Migrazione runs incoerente: {broken[:3]}")
            connection.execute("pragma foreign_keys = on")
        finally:
            connection.close()

    def rows(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql, parameters).fetchall()]

    def row(self, sql: str, parameters: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.connect() as connection:
            result = connection.execute(sql, parameters).fetchone()
            return dict(result) if result else None

    def execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> int:
        with self.connect() as connection:
            cursor = connection.execute(sql, parameters)
            return int(cursor.lastrowid)

    def state(self) -> dict[str, Any]:
        projects = self.rows("""
            select p.*,
              (select count(*) from images i where i.project_id=p.id and i.deleted_at is null) image_count,
              (select count(*) from questions q where q.project_id=p.id) question_count,
              (select count(*) from annotations a join images i on i.id=a.image_id where i.project_id=p.id and i.deleted_at is null) annotation_count
            from projects p where p.deleted_at is null order by p.created_at desc""")
        providers = self.rows("select * from providers order by name")
        runs = self.rows("""select r.*, p.name provider_name, p.model provider_model,
            p.is_demo provider_is_demo, pr.name project_name
            from runs r join providers p on p.id=r.provider_id join projects pr on pr.id=r.project_id
            where pr.deleted_at is null and r.archived_at is null
            order by r.created_at desc limit 50""")
        for run in runs:
            run["config"] = json.loads(run.pop("config_json"))
        return {"projects": projects, "providers": providers, "runs": runs, "schema_version": SCHEMA_VERSION}

    def project_detail(self, project_id: int) -> dict[str, Any] | None:
        project = self.row("select * from projects where id=? and deleted_at is null", (project_id,))
        if not project:
            return None
        project["images"] = self.rows("""select i.*,
            (select count(*) from annotations a where a.image_id=i.id) annotation_count
            from images i where project_id=? and deleted_at is null order by id""", (project_id,))
        questions = self.rows("select * from questions where project_id=? order by id", (project_id,))
        for question in questions:
            question["variants"] = self.rows("select * from variants where question_id=? order by canonical desc,id", (question["id"],))
            question["annotation_counts"] = {row["value"]: row["n"] for row in self.rows(
                "select value,count(*) n from annotations where question_id=? group by value", (question["id"],))}
            question["agreement_counts"] = {row["agreement"]: row["n"] for row in self.rows(
                "select agreement,count(*) n from annotations where question_id=? group by agreement", (question["id"],))}
        project["questions"] = questions
        project["annotators"] = self.annotators(project_id)
        return project

    def annotators(self, project_id: int) -> list[dict[str, Any]]:
        """Everyone who has judged anything in this project, busiest first."""
        return self.rows("""select l.annotator,
            count(*) filter (where l.is_adjudication=0) labels,
            count(*) filter (where l.is_adjudication=1) adjudications,
            max(l.updated_at) last_seen
            from annotation_labels l join images i on i.id=l.image_id
            where i.project_id=? and i.deleted_at is null
            group by l.annotator order by labels desc, l.annotator""", (project_id,))

    def labels(self, project_id: int, question_id: int | None = None) -> list[dict[str, Any]]:
        """Every independent judgement and adjudication, one row per reviewer."""
        clause = " and l.question_id=?" if question_id else ""
        parameters: tuple[Any, ...] = (project_id, question_id) if question_id else (project_id,)
        return self.rows(f"""select l.image_id,l.question_id,l.annotator,l.value,l.note,
            l.is_adjudication,l.updated_at,i.filename,i.sha256
            from annotation_labels l join images i on i.id=l.image_id
            where i.project_id=? and i.deleted_at is null{clause}
            order by l.question_id,l.image_id,l.annotator""", parameters)

    def trash(self) -> dict[str, Any]:
        """List recoverable items without exposing or removing their local files."""
        projects = self.rows("""select p.id,p.name,p.slug,p.deleted_at,
            (select count(*) from images i where i.project_id=p.id) image_count
            from projects p where p.deleted_at is not null order by p.deleted_at desc""")
        images = self.rows("""select i.id,i.project_id,i.filename,i.deleted_at,p.name project_name
            from images i join projects p on p.id=i.project_id
            where i.deleted_at is not null and p.deleted_at is null order by i.deleted_at desc""")
        return {"projects": projects, "images": images}

    @staticmethod
    def now() -> float:
        return time.time()
