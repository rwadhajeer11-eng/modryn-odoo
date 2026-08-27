import logging

from odoo import models

_logger = logging.getLogger(__name__)


class ModrynStaffNotify(models.AbstractModel):
    """The one door every staff-assignment text leaves through.

    An AbstractModel rather than a helper function so callers in other modules
    reach it as env['modryn.staff.notify'] with no cross-module Python import —
    the same port shape as modryn.sms, and abstract models need no ACL.

    It decides exactly four things: skip the actor texting herself, resolve a
    phone, queue through the outbox, and log a refusal. The BODY is the
    caller's job, composed under with_context(lang=modryn_lang(employee)) —
    the task-escalation precedent — because only the caller knows what was
    assigned.
    """

    _name = 'modryn.staff.notify'
    _description = 'Staff assignment SMS'

    def modryn_lang(self, employee):
        return (employee.user_id.lang or 'he_IL') if employee.user_id else 'he_IL'

    def modryn_assigned(self, employee, body, record=None):
        """Text `employee` that work was just assigned to her.

        Raises the in-app notification AND queues the text. Returns True when
        the MESSAGE was queued - the bell is raised either way, so a False here
        no longer means she was not told.

        Never raises and never blocks: send_async hands the body to the outbox,
        so a Twilio outage cannot stall the assignment that triggered this.
        """
        if not employee:
            return False
        # She is watching the screen that just changed — the seamstress pulling
        # her own next task at the terminal does not need a text about it.
        actor = self.env['hr.employee'].sudo().search(
            [('user_id', '=', self.env.uid)], limit=1)
        if actor and actor.id == employee.id:
            return False

        # The bell, raised BEFORE the phone lookup below and never gated on it.
        # modryn_assigned returns early for a woman with no work_phone - and she
        # is exactly the person an in-app notification exists for. Every
        # portal-level hire on every tenant was phoneless for months and nothing
        # read a staff phone until this notifier existed; the bell must not
        # inherit that blind spot.
        self.env['modryn.staff.notification'].sudo().modryn_notify(
            employee, body, actor=actor, record=record)

        phone = employee.work_phone or employee.mobile_phone
        if not phone:
            _logger.info(
                '[modryn.staff] no phone for %s — assignment SMS skipped',
                employee.name)
            return False
        ok, detail = self.env['modryn.sms'].send_async(phone, body)
        if not ok:
            _logger.warning(
                '[modryn.staff] assignment SMS refused for %s: %s',
                employee.name, detail)
        return ok
