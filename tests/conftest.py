"""Isolation globale de la suite : les octets d'images et de documents générés
en test ne touchent jamais les vrais ``reflow-media/`` et ``reflow-docs/``.

Les classes ``unittest.TestCase`` ignorent les fixtures autouse, donc la
redirection est appliquée à l'import pour tout le processus pytest.
``test_media.py`` la remplace par test et restaure ce répertoire ensuite.
"""

import tempfile

import documents
import media

_SESSION_MEDIA = tempfile.TemporaryDirectory(prefix="reflow-media-tests-")
media.MEDIA_DIRECTORY = _SESSION_MEDIA.name

_SESSION_DOCS = tempfile.TemporaryDirectory(prefix="reflow-docs-tests-")
documents.DOCS_DIRECTORY = _SESSION_DOCS.name
