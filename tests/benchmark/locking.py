import shutil
from pathlib import Path

from pyinstrument import profiler

import dictdatabase as DDB
from dictdatabase import locking

DDB.config.storage_directory = "./.benchmark_locking"
path = Path(DDB.config.storage_directory)
path.mkdir(exist_ok=True, parents=True)


# 05.11.22: 4520ms
# 25.11.22: 4156ms
with profiler.Profiler() as p:
	for _ in range(25_000):
		l = locking.ReadLock("db")
		l._lock()
		l._unlock()
p.open_in_browser()


# 05.11.22: 4884ms
# 25.11.22: 4159ms
with profiler.Profiler() as p:
	for _ in range(25_000):
		l = locking.WriteLock("db")
		l._lock()
		l._unlock()
p.open_in_browser()


l = locking.WriteLock("db/test.some")
l._lock()


shutil.rmtree(DDB.config.storage_directory)
