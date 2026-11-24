import json
import sqlite3
from contextlib import contextmanager, closing
from pathlib import Path

from app.errors import SurveyProblem

from .identity import canonical


class Archive:
    def __init__(self, filename):
        self.filename = filename
        Path(filename).parent.mkdir(exist_ok=True,parents=True)
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS runs(
                id TEXT PRIMARY KEY, created TEXT NOT NULL, document TEXT NOT NULL, results TEXT NOT NULL
            ); CREATE TABLE IF NOT EXISTS originals(
                id TEXT REFERENCES runs(id), name TEXT NOT NULL, csv TEXT NOT NULL, PRIMARY KEY(id,name)
            ); CREATE TABLE IF NOT EXISTS withdrawals(
                ticket TEXT PRIMARY KEY, created TEXT NOT NULL
            ); CREATE TABLE IF NOT EXISTS withdrawal_runs(
                run TEXT NOT NULL REFERENCES runs(id),
                ticket TEXT NOT NULL REFERENCES withdrawals(ticket),
                created TEXT NOT NULL, receipt TEXT NOT NULL,
                PRIMARY KEY(run,ticket)
            );''')

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.filename,timeout=10)) as db, db:
            db.execute('PRAGMA foreign_keys=ON')
            yield db

    def add(self, manifest, tables, results, assumed=None):
        key = manifest['id']
        with self.connect() as db:
            if assumed is not None and set(assumed) != self._tickets(db):
                raise SurveyProblem('WITHDRAWN_CHANGED','撤回名单已变化，请重试',409)
            created = db.execute('INSERT OR IGNORE INTO runs VALUES (?,?,?,?)',
                                 (key,manifest['created'],canonical(manifest),canonical(results))).rowcount
            if created:
                db.executemany('INSERT INTO originals VALUES (?,?,?)',[(key,name,csv) for name,csv in tables.items()])
        return bool(created)

    def read(self,key):
        with self.connect() as db:
            row = db.execute('SELECT document,results FROM runs WHERE id=?',(key,)).fetchone()
            tables = dict(db.execute('SELECT name,csv FROM originals WHERE id=?',(key,)))
        if not row:
            raise SurveyProblem('NO_RUN','找不到扩推归档',404)
        manifest, results = map(json.loads,row)
        return manifest,tables,results

    def recent(self):
        with self.connect() as db:
            rows = db.execute('SELECT document FROM runs ORDER BY created DESC,id LIMIT 40').fetchall()
        return [json.loads(row[0]) for row in rows]

    @staticmethod
    def _tickets(db):
        return {row[0] for row in db.execute('SELECT ticket FROM withdrawals')}

    def withdrawn(self):
        with self.connect() as db:
            return self._tickets(db)

    def withdrawal(self, ticket):
        with self.connect() as db:
            row = db.execute('SELECT created FROM withdrawals WHERE ticket=?',(ticket,)).fetchone()
            if not row:
                return None
            runs = [r[0] for r in db.execute('SELECT run FROM withdrawal_runs WHERE ticket=? ORDER BY rowid',(ticket,))]
        return dict(ticket=ticket, created=row[0], runs=runs)

    def receipts(self, key):
        with self.connect() as db:
            rows = db.execute('SELECT receipt FROM withdrawal_runs WHERE run=? ORDER BY rowid',(key,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def apply_withdrawal(self, ticket, stamp, transform):
        '''登记撤回并逐批改写留档，全程一个事务。transform(manifest,tables,results,receipts)
        返回 (tables,results,receipt) 或 None（该批不含该票）。已撤回返回 None。'''
        with self.connect() as db:
            if not db.execute('INSERT OR IGNORE INTO withdrawals VALUES (?,?)',(ticket,stamp)).rowcount:
                return None
            affected = []
            for key, in db.execute('SELECT id FROM runs ORDER BY created,id').fetchall():
                document, stored = db.execute('SELECT document,results FROM runs WHERE id=?',(key,)).fetchone()
                tables = dict(db.execute('SELECT name,csv FROM originals WHERE id=?',(key,)))
                receipts = [json.loads(r[0]) for r in
                            db.execute('SELECT receipt FROM withdrawal_runs WHERE run=? ORDER BY rowid',(key,))]
                outcome = transform(json.loads(document),tables,json.loads(stored),receipts)
                if outcome is None:
                    continue
                tables, results, receipt = outcome
                db.execute('UPDATE runs SET results=? WHERE id=?',(canonical(results),key))
                db.execute('DELETE FROM originals WHERE id=? AND name=?',(key,'returns'))
                db.execute('INSERT INTO originals VALUES (?,?,?)',(key,'returns',tables['returns']))
                db.execute('INSERT INTO withdrawal_runs VALUES (?,?,?,?)',(key,ticket,stamp,canonical(receipt)))
                affected.append(key)
            return affected
