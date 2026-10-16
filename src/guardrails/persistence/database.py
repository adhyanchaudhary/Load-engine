"""Per-operation connections, WAL, short transactions and bounded SQLite waits."""
from contextlib import contextmanager,closing
from datetime import datetime,timezone
from functools import lru_cache
from pathlib import Path
import sqlite3
from filelock import FileLock
from guardrails.persistence.schema import DDL,SCHEMA_VERSION


def utc(value=None):
    if value is None:value=datetime.now(timezone.utc)
    if isinstance(value,str):value=datetime.fromisoformat(value.replace('Z','+00:00'))
    if value.tzinfo is None:return None
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds')


class Database:
    def __init__(self,state_dir):
        self.directory=Path(state_dir);self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.path=self.directory/'performance.db'
        if self.path.is_symlink():raise ValueError('Database symlinks are forbidden')
        self.initialize()

    def initialize(self):
        with FileLock(str(self.directory/'database-migration.lock'),timeout=5):
            with closing(sqlite3.connect(self.path,timeout=5)) as con:
                con.execute('PRAGMA foreign_keys=ON');con.execute('PRAGMA busy_timeout=5000')
                con.execute('PRAGMA journal_mode=WAL')
                version=con.execute('PRAGMA user_version').fetchone()[0]
                if version>SCHEMA_VERSION:raise ValueError('Database schema is newer than this application')
                con.executescript('BEGIN IMMEDIATE;\n'+DDL)
                con.execute('INSERT OR IGNORE INTO schema_migrations VALUES(?,?)',(SCHEMA_VERSION,utc()))
                con.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
                con.commit()
            self.path.chmod(0o600)

    @contextmanager
    def connect(self,*,write=False):
        if self.path.is_symlink():raise ValueError('Database symlinks are forbidden')
        con=sqlite3.connect(self.path,timeout=5,isolation_level=None)
        con.row_factory=sqlite3.Row
        try:
            con.execute('PRAGMA foreign_keys=ON');con.execute('PRAGMA busy_timeout=5000')
            if write:con.execute('BEGIN IMMEDIATE')
            else:con.execute('PRAGMA query_only=ON')
            yield con
            if write:con.commit()
        except BaseException:
            if write:con.rollback()
            raise
        finally:con.close()

    def rows(self,sql,params=()):
        with self.connect() as con:return [dict(r) for r in con.execute(sql,params).fetchall()]


@lru_cache(maxsize=64)
def database_for(state_dir):return Database(Path(state_dir))
