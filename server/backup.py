#!/usr/bin/env python3
"""Consistent SQLite online backup; unlike copying a live WAL database file."""
import argparse,os,sqlite3
from pathlib import Path

def backup(source,destination):
    source=Path(source).resolve();destination=Path(destination).resolve()
    if not source.is_file():raise ValueError('Source database does not exist')
    if destination.exists() or source==destination:raise ValueError('Destination must be a new file')
    source_uri=source.as_uri()+'?mode=ro'
    fd=os.open(destination,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    try:
        with sqlite3.connect(source_uri,uri=True) as src,sqlite3.connect(destination) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Backup integrity check failed')
    except Exception:
        destination.unlink(missing_ok=True);raise

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source');p.add_argument('destination');a=p.parse_args();backup(a.source,a.destination);print('Backup saved')
