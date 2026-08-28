import hashlib
import hmac
import logging
import secrets
from datetime import datetime

from psycopg2.errors import UniqueViolation

from odoo import _, api, fields, models

from odoo.addons.modryn_portal.models.sms import normalize_il_phone

_logger = logging.getLogger(__name__)

# One channel per database is enough: a tenant IS a database here, so there is
# no cross-boutique leak to design around — the websocket is already scoped.
QUEUE_CHANNEL = 'modryn_queue'

# A ticket is live from the moment she scans until she is served or sent home.
OPEN_STATES = ('pending', 'waiting', 'called')

# Postgres reports this in diag.constraint_name. Compared by name, never by
# catching the class alone: the table also carries a pkey, and a violation from
# that is a different bug that must not be swallowed. 40 chars — safely under
# Postgres's 63-char identifier limit, so the name is used verbatim, not
# truncated-and-hashed.
OPEN_PHONE_INDEX = 'modryn_queue_entry_modryn_open_phone_uniq'


class ModrynQueueEntry(models.Model):
    _name = 'modryn.queue.entry'
    _description = 'Walk-in queue entry'
    # Fair order is the whole point of a queue: first to submit is first served.
    _order = 'create_date asc, id asc'

    name = fields.Char(required=True)
    phone = fields.Char()
    # The CODE stays 'evening' - every row in every tenant carries it, and
    # renaming it would be a migration for a word. Only the label changes, to
    # the one the boutique actually uses: a customer is a bride or she is not.
    client_type = fields.Selection(
        selection=[('bride', 'Bride'), ('evening', 'Regular customer')],
        default='bride',
        required=True,
    )

    # What the floor needs to remember about her that no field covers: "allergic
    # to the veil pins", "mother arriving at 4".
    #
    # STAFF-ONLY, and that is not enforced by this field - it is enforced by
    # nobody rendering it on /q/<token>. That route hands the WHOLE entry record
    # to its template, so this note is one careless t-out away from the
    # customer's own screen. verify.sh plants a note and asserts it never
    # appears there; if you add it to that template, the gate will say so.
    staff_note = fields.Text(string="Note for the team")
    # A verified check-in joins the line directly. The old `pending` front door —
    # a scan parked her here until a staff member accepted her — is gone: proving
    # she holds the number is now the gate, and it is a better one than a human
    # glancing at a name.
    #
    # `pending` STAYS in the selection, and the reason is the readers, not the
    # rows: OPEN_STATES, modryn_accept, modryn_redirect, /floor's arrivals panel
    # and four branches of the ticket template all still name it. The rows are
    # the weaker argument and would have misled — noga holds none at all, and
    # bella's two are in OPEN_STATES, so the closing cron rewrites them to
    # `expired` on its next run.
    state = fields.Selection(
        selection=[
            ('pending', 'Pending'),
            ('waiting', 'Waiting'),
            ('called', 'Called'),
            ('done', 'Finished'),
            ('redirected', 'Invited to book'),
            ('expired', 'Expired'),
        ],
        default='waiting',
        required=True,
        index=True,
    )

    # Her private page. Random, not derived from the id: two customers must not
    # be able to guess each other's ticket by counting.
    access_token = fields.Char(index=True, copy=False)
    next_notified_at = fields.Datetime(readonly=True)
    turn_notified_at = fields.Datetime(readonly=True)

    # One place in the line per number, decided by the database. The search in
    # modryn_check_in is a read-then-write with no lock: two verifies for the
    # same number landing together both see zero and both create. The state
    # list is OPEN_STATES spelled as literals — an index predicate cannot read
    # a Python tuple, so the two must be kept in step by hand.
    # NULL phones are exempt: they can never be texted, and only non-web
    # callers can produce one (the form validates the number first).
    _modryn_open_phone_uniq = models.UniqueIndex(
        "(phone) WHERE state IN ('pending', 'waiting', 'called')"
        " AND phone IS NOT NULL",
        "That number is already in the line.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals.setdefault('access_token', secrets.token_urlsafe(16))
        entries = super().create(vals_list)
        entries._notify_board()
        return entries

    def _payload(self):
        return {
            'id': self.id,
            'name': self.name,
            'phone': self.phone or '',
            'client_type': self.client_type,
            'staff_note': self.staff_note or '',
            'state': self.state,
        }

    def _notify_board(self):
        """Ring every open board. A SIGNAL, never the customer.

        QUEUE_CHANNEL is a plain string, and Odoo's
        ir.websocket._build_bus_channel_list returns the channel list the CLIENT
        sent, unfiltered - so anybody who knows or guesses the name receives
        everything published on it. Odoo's own _sendone docstring says a string
        target "should not be guessable by an attacker".

        This used to push _payload(): the customer's name, her phone number and
        the staff note about her, to any listener at all. It carries her id and
        nothing else now. Both boards re-read through their own
        permission-checked path anyway - the floor board's handler is
        `() => this.refresh()`, which never looked at the payload - so nothing
        is lost by not sending it.
        """
        for entry in self:
            self.env['bus.bus']._sendone(
                QUEUE_CHANNEL, 'modryn_queue/update', {'id': entry.id})

    # Fields the board actually draws. A write to anything else is noise and
    # must not wake every open terminal in the shop.
    BOARD_FIELDS = ('state', 'staff_note', 'client_type')

    def write(self, vals):
        res = super().write(vals)
        if any(f in vals for f in self.BOARD_FIELDS):
            self._notify_board()
        return res

    # --------------------------------------------------------------- lifecycle
    @api.model
    def modryn_check_in(self, name, phone, client_type='bride'):
        """Take a verified walk-in, or hand back the ticket she already has.

        Returns ``(entry, created)`` — the caller needs to know which, because
        a re-check-in is told "this is your place" rather than joined again.

        Re-scanning the sign, or a second person typing the same number, must
        never create a rival ticket — she would appear twice in the line and
        lose her real place.

        Handing back an existing ticket used to be a way to READ a stranger's:
        type her number, get her access_token. Every caller now proves it is her
        number before reaching this method, so the same line is safe. It costs
        one code on a re-scan, which is the price of that.
        """
        normalized = normalize_il_phone(phone) if phone else None
        if normalized:
            existing = self.sudo().search([
                ('phone', '=', normalized), ('state', 'in', OPEN_STATES),
            ], limit=1)
            if existing:
                return existing, False
        try:
            # Two verifies for the same number landed together; the search
            # above cannot see the other one until it commits. Savepoint, not a
            # bare try: a rejected INSERT aborts the whole transaction, so the
            # re-search below would fail too.
            with self.env.cr.savepoint():
                entry = self.sudo().create({
                    'name': name,
                    # NULL over raw garbage: an unnormalizable phone can never
                    # be texted, and storing it in whatever format it arrived
                    # is what let format-mismatched duplicates past the search.
                    'phone': normalized or False,
                    'client_type': client_type,
                })
        except UniqueViolation as exc:
            if exc.diag.constraint_name != OPEN_PHONE_INDEX:
                raise
            existing = self.sudo().search([
                ('phone', '=', normalized), ('state', 'in', OPEN_STATES),
            ], limit=1)
            if not existing:
                # The rival row vanished between the refusal and this read —
                # only reachable if it closed in that window. Retrying the
                # create here could livelock; one honest failure is better.
                raise
            return existing, False
        entry._notify_joined()
        return entry, True

    def modryn_accept(self):
        """Staff let her into the line. Idempotent: two managers, one transition."""
        for entry in self.filtered(lambda e: e.state == 'pending'):
            entry.state = 'waiting'
        self._notify_next_in_line()
        return self

    def modryn_redirect(self, notify=True):
        """Too busy today — invite her to book instead, warmly."""
        for entry in self.filtered(lambda e: e.state in ('pending', 'waiting')):
            entry.state = 'redirected'
            if notify and entry.phone:
                entry._localised()._send_full_today()
        # Sending the front of the line home promotes whoever was behind her, so
        # her heads-up goes out now. Every other exit from the line already does
        # this; redirect did not, and acceptance used to cover for it.
        self._notify_next_in_line()
        return self

    # ------------------------------------------------------------------- comms
    def _base_url(self):
        return self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')

    def _ticket_url(self):
        self.ensure_one()
        return '%s/q/%s' % (self._base_url(), self.access_token)

    def _modryn_lang(self):
        """The language the boutique writes to its customers in.

        A walk-in leaves a name and a phone and nothing else, so there is no
        customer language to read. The boutique's own default is the honest
        answer and - unlike what was here before - it is the SAME for every
        customer instead of depending on who is holding the screen.
        """
        website = self.env['website'].sudo().search([], limit=1)
        return (website.default_lang_id.code if website.default_lang_id
                else 'he_IL')

    def _localised(self):
        """This entry, in the language her texts should be written in.

        Every other outbound message in this product already does this -
        modryn_portal's booking_comms._localised, day_waitlist, the task
        escalation - and the walk-in queue was the one that did not. Its four
        SMS bodies were composed with a bare _(), which resolves against
        whoever is holding the screen: a stylist who switched her own interface
        to English started texting every waiting customer in English on a
        Hebrew-first boutique, and nothing anywhere reported it.

        The switch has to happen BEFORE the sentence is composed. _() finds its
        language by looking at the calling frame's `self`, so a string built at
        the call site and passed in here has already been resolved in the wrong
        language and no later context can change it. That is why each body
        below is a method rather than an argument.
        """
        return self.with_context(lang=self._modryn_lang())

    # Each body is a method so that `self` inside it is the LOCALISED recordset.
    def _send_joined(self, next_up):
        self.ensure_one()
        if next_up:
            self._send(_(
                "You're in the queue — and you're next. We'll be with you in a "
                "moment. Your ticket: %(link)s"
            ) % {'link': self._ticket_url()})
        else:
            self._send(_(
                "You're in the queue. We'll text you when you're next. "
                "Your ticket: %(link)s"
            ) % {'link': self._ticket_url()})

    def _send_next(self):
        self.ensure_one()
        self._send(_("You're next — we'll be with you in a moment."))

    def _send_full_today(self):
        self.ensure_one()
        self._send(_(
            "We're fully booked today. Book a fitting with us here: %(link)s"
        ) % {'link': '%s/book' % self._base_url()})

    def _send(self, body):
        self.ensure_one()
        if not self.phone:
            return
        # Every boutique now sends from ONE shared Twilio number, so an
        # unbranded text arrives from a number she does not know and that may
        # also be texting her about a different store. booking_comms.py weaves
        # %(boutique)s into each sentence; here it is a prefix, because changing
        # these msgids invalidates their existing he and ar translations, and
        # sync_translations.py rewrites all eight addons on every run — seven of
        # them would then need `git checkout --`. A prefix touches no .po at all.
        # ponytail: prefix, not woven — weave it into each sentence if the copy
        # ever needs to read better.
        body = '%s: %s' % (self.env.company.name, body)
        # The one chokepoint every queue text goes through — redirect, you're
        # next, and your turn — so queueing here covers all three at once.
        # Staff clicking "call next" is an HTTP request like any other, and the
        # notified-at stamps above are written regardless of the result, so no
        # decision here depends on waiting for Twilio.
        ok, detail = self.env['modryn.sms'].send_async(self.phone, body)
        if not ok:
            _logger.warning('[modryn.queue] sms not sent for %s: %s', self.id, detail)

    def _notify_joined(self):
        """Tell her she is in the line, and fold "you're next" in when she is.

        An empty boutique would otherwise fire two texts one second apart: the
        join, and then whoever next promotes the queue finding her already at
        the front. Stamping next_notified_at makes _notify_next_in_line's
        once-ever guard skip her, so the fold is the only thing she gets.

        The ticket link is always included. Since the floor terminal can check
        her in, this SMS may be the only place she ever sees it.
        """
        self.ensure_one()
        first = self.sudo().search(
            [('state', '=', 'waiting')], order='create_date asc', limit=1)
        if first == self:
            self._localised()._send_joined(next_up=True)
            self.next_notified_at = fields.Datetime.now()
        else:
            self._localised()._send_joined(next_up=False)

    @api.model
    def _notify_next_in_line(self):
        """Text whoever is now at the front — once, ever.

        Sent on the transition INTO first place rather than on a timer, so she
        gets the heads-up while she is still browsing the racks.
        """
        first = self.sudo().search([('state', '=', 'waiting')], order='create_date asc', limit=1)
        if first and not first.next_notified_at:
            first._localised()._send_next()
            first.next_notified_at = fields.Datetime.now()

    def modryn_call(self, employee=None):
        """Her turn. The SMS names the stylist so she knows who to look for."""
        self.ensure_one()
        values = {'state': 'called'}
        if employee:
            values['modryn_employee_id'] = employee.id
        self.write(values)
        if not self.turn_notified_at:
            stylist = employee or self.modryn_employee_id
            if stylist:
                body = _("We're ready for you — %(stylist)s is waiting.") % {
                    'stylist': stylist.name}
            else:
                body = _("We're ready for you — please come to the desk.")
            self._send(body)
            self.turn_notified_at = fields.Datetime.now()
        # Serving one customer promotes the next, so her heads-up goes out now.
        self._notify_next_in_line()
        return self

    def action_call_next(self):
        for entry in self:
            entry.modryn_call()

    def action_done(self):
        self.write({'state': 'done'})
        self._notify_next_in_line()

    # -------------------------------------------------------------------- cron
    @api.model
    def _modryn_expire_open_tickets(self):
        """Close the floor: nobody should wake up tomorrow still 'waiting'.

        Runs after closing time; anything still open becomes expired, and her
        page turns into a warm invitation to book instead.
        """
        stale = self.sudo().search([('state', 'in', OPEN_STATES)])
        if stale:
            stale.write({'state': 'expired'})
