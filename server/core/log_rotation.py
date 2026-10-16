"""Size-bounded application logs also rotate at the UTC day boundary."""
import time
from logging.handlers import RotatingFileHandler


class DailySizeRotatingFileHandler(RotatingFileHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from pathlib import Path
        self._day = time.gmtime(Path(self.baseFilename).stat().st_mtime)[:3]
        self._next_day = self._day

    def shouldRollover(self, record):
        self._next_day = time.gmtime(record.created)[:3]
        return self._next_day != self._day or super().shouldRollover(record)

    def doRollover(self):
        super().doRollover()
        self._day = self._next_day
