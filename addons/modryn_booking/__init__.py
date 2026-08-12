from . import models
from . import controllers

# Odoo resolves manifest hooks with getattr() on the PACKAGE, not on a submodule,
# so the name has to live here (odoo/odoo/modules/loading.py:241).
from .schema_guard import post_init_hook
