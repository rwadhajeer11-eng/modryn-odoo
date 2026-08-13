from . import models
from . import controllers

# Odoo resolves manifest hooks with getattr() on the package, so the guard's
# entry points must be attributes here — same wiring as modryn_portal.
from .schema_guard import post_init_hook, pre_init_hook
