"""Generation-scoped SQLite resume points, separate from the fresh In Progress feed."""
import sqlite3
from pathlib import Path
from time import time
from .account_state import current_account,persist_if_current
from .prime_diagnostics import exception_details


class ResumePoints:
    def __init__(self,profile,epoch,report):
        self.profile,self.epoch,self.report=Path(profile),epoch,report
        self.filename=self.profile/'playback-state.sqlite'
    def read(self,identifier):
        if not self.filename.exists() or not current_account(self.profile,self.epoch):return None
        connection=sqlite3.connect(str(self.filename),timeout=5)
        try:
            row=connection.execute('SELECT position,duration FROM resume WHERE epoch=? AND title=?',
                (self.epoch,identifier)).fetchone()
            return {'position':row[0],'duration':row[1]} if row else None
        except sqlite3.Error as error:
            self.report({'event':'resume_read_failed','stage':'resume_store','exception':exception_details(error),'result':'failure'})
            return None
        finally:connection.close()
    def write(self,identifier,position,duration,completed=False):
        def store():
            connection=sqlite3.connect(str(self.filename),timeout=5)
            try:
                connection.execute('CREATE TABLE IF NOT EXISTS resume (epoch TEXT,title TEXT,position REAL,duration REAL,updated REAL,PRIMARY KEY(epoch,title))')
                connection.execute('DELETE FROM resume WHERE epoch<>?',(self.epoch,))
                if completed:connection.execute('DELETE FROM resume WHERE epoch=? AND title=?',(self.epoch,identifier))
                else:connection.execute('INSERT OR REPLACE INTO resume VALUES (?,?,?,?,?)',
                    (self.epoch,identifier,max(0,position),max(0,duration),time()))
                connection.commit()
            finally:connection.close()
        try:
            stored=persist_if_current(self.profile,self.epoch,store)
            self.report({'event':'resume_saved','stage':'resume_store','completed':completed,
                'position_seconds':round(position,2),'result':'success' if stored else 'cancelled'})
        except Exception as error:
            self.report({'event':'resume_save_failed','stage':'resume_store','exception':exception_details(error),'result':'failure'})
