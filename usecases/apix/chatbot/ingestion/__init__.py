"""ingestion — blob → SQLite weekly ingestion job and the ``/ingest`` API router.

The implementation lives in :mod:`chatbot.ingestion.jobs`; the public router and
helpers are re-exported here so callers keep using
``from chatbot.ingestion import router`` and ``python -m chatbot.ingestion``.
"""

from chatbot.ingestion.jobs import *  # noqa: F401,F403
from chatbot.ingestion.jobs import router, _cli  # noqa: F401
