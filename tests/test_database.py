from concurrent.futures import ThreadPoolExecutor
import sqlite3
import pytest
from guardrails.persistence.database import Database,utc


def test_database_initialization_and_foreign_keys(tmp_path):
    db=Database(tmp_path)
    assert db.path.name=='performance.db' and db.path.exists()
    with db.connect() as con:
        assert con.execute('PRAGMA foreign_keys').fetchone()[0]==1
        assert con.execute('PRAGMA user_version').fetchone()[0]==1
    with pytest.raises(sqlite3.IntegrityError):
        with db.connect(write=True) as con:
            con.execute("INSERT INTO resource_samples(test_id,sample_index) VALUES('missing',0)")


def test_short_concurrent_writes_and_read_only_queries(tmp_path):
    db=Database(tmp_path)
    def write(i):
        with db.connect(write=True) as con:con.execute('INSERT INTO audit_events VALUES(?,?,?,?,?,?,?,?)',(str(i),'owner',utc(),'test','action',None,'OK',None))
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(write,range(24)))
    assert db.rows('SELECT COUNT(*) AS n FROM audit_events')[0]['n']==24
    with pytest.raises(sqlite3.OperationalError):
        with db.connect() as con:con.execute('DELETE FROM audit_events')


def test_schema_future_version_and_symlink_rejected(tmp_path):
    db=Database(tmp_path)
    with db.connect(write=True) as con:con.execute('PRAGMA user_version=999')
    with pytest.raises(ValueError,match='newer'):Database(tmp_path)
    other=tmp_path/'other';other.mkdir();(other/'performance.db').symlink_to(db.path)
    with pytest.raises(ValueError,match='symlink'):Database(other)
