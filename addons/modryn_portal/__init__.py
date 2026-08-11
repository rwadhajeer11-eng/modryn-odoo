from . import models
from . import controllers

# Odoo resolves manifest hooks with getattr() on the PACKAGE, not on a submodule,
# so the two names have to live here (odoo/odoo/modules/loading.py:182, :241).
from .schema_guard import pre_init_hook, post_init_hook
