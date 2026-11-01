"""Who the operator is and where local state lives. Configuration, never constants in code.

Everything here comes from the environment (or the env file the fetch/send commands read for their
app credentials). A value written into source is documentation pretending to be config: the next
operator inherits the last one's identity, and every message they send is misattributed.

    NEO_STATE_DIR              corpus.db, tokens, the Telegram session   (default ~/.local/state/neo)
    NEO_ENV_FILE               app credentials file                       (default ~/.config/neo/.env)
    NEO_TELEGRAM_OPERATOR_ID   the operator's numeric Telegram user id    (required for Telegram)
    NEO_OPERATOR_EMAILS        the operator's mail addresses, comma-separated (required for mail)
    NEO_ASSISTANT_NAME         the name in the send footnote              (default "Neo")
"""

import os
from pathlib import Path

STATE_DIR = Path(os.environ.get("NEO_STATE_DIR", str(Path.home() / ".local/state/neo"))).expanduser()
ENV_FILE = Path(os.environ.get("NEO_ENV_FILE", str(Path.home() / ".config/neo/.env"))).expanduser()
ASSISTANT_NAME = os.environ.get("NEO_ASSISTANT_NAME", "Neo")
