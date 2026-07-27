# -*- coding: utf-8 -*-
"""Legacy compatibility wrapper for Guobu model audit.

The old one-shot script sent full system SN values to the vision model and
could auto-pass without the v2 photo compliance gate. Keep this filename for
existing commands, but route execution through the v2 hybrid CLI.
"""

from __future__ import annotations

try:
    from .run_guobu_model_audit_v2 import main
except ImportError:
    from run_guobu_model_audit_v2 import main


if __name__ == "__main__":
    main()
