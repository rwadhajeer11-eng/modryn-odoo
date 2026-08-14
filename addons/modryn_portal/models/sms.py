import logging
import os
import re

import requests

from odoo import api, models

_logger = logging.getLogger(__name__)

TWILIO_BASE = 'https://api.twilio.com/2010-04-01'
SEND_TIMEOUT = 10

# Config keys, still per-database — but these are now the OVERRIDE, not the
# default. The platform's own Twilio account lives in the process environment
# (see _twilio_config) and every database inherits it, so a boutique carries its
# own four here only when it wants its own sender identity.
P_ACCOUNT_SID = 'modryn.twilio.account_sid'
P_KEY_SID = 'modryn.twilio.api_key_sid'
P_KEY_SECRET = 'modryn.twilio.api_key_secret'
P_FROM = 'modryn.twilio.from_number'
# The per-tenant OFF switch, and it has to be its own key: get_param returns the
# default for a stored empty string, so a blanked-out credential reads exactly
# like one that was never set. There is no value you can write into the four
# above that means "off" rather than "unconfigured". Any non-empty value here
# counts — including the string '0', so turn a tenant back on by clearing the
# parameter, never by writing a falsey-looking value into it.
P_DISABLED = 'modryn.twilio.disabled'


def normalize_il_phone(raw):
    """Israeli number -> E.164, or None if it cannot be one.

    Twilio rejects anything that is not E.164, and customers type 052-123-4567,
    052 1234567 and +972-52-1234567 interchangeably.
    """
    digits = re.sub(r'[^\d+]', '', (raw or '').strip())
    if not digits:
        return None
    if digits.startswith('+'):
        return digits if re.fullmatch(r'\+\d{9,15}', digits) else None
    if digits.startswith('00'):
        digits = '+' + digits[2:]
        return digits if re.fullmatch(r'\+\d{9,15}', digits) else None
    if digits.startswith('0'):
        # Local Israeli form: drop the trunk 0, prepend the country code.
        return '+972' + digits[1:] if re.fullmatch(r'0\d{8,9}', digits) else None
    if digits.startswith('972'):
        # Same 9-15 check as every other branch. Without it this one accepted
        # the bare string '972' and stored '+972' as a valid phone — which then
        # re-parsed to None, so normalize_il_phone was not idempotent on its own
        # output. A waitlist entry holding '+972' could never be texted and, at
        # the time, dead-ended that day's whole queue.
        return '+' + digits if re.fullmatch(r'\d{9,15}', digits) else None
    return None


# Twilio 4xx codes that mean THIS RECIPIENT will never receive a message. Only
# these justify taking someone's place on the waitlist away.
#
#   21211 invalid 'To' number          21214 'To' cannot be reached
#   21217 number not valid per lookup  21610 recipient sent STOP (must not retry)
#   21612 no SMS route to this number  21614 'To' is not a mobile number
#
# Everything else is deliberately absent, and the classifier defaults to
# transient, because the two failure modes cost wildly different amounts. A
# wrong "transient" costs one slot held for its 2h window. A wrong "permanent"
# burns one waitlist entry per retry cycle, and account-scoped errors fail
# identically for EVERY recipient — so 401/403 (bad or revoked credentials),
# 404 (wrong account sid), 21606/21603 (From number not SMS-capable), 21408
# (region not enabled), 429 (rate limit), all 5xx, transport faults and
# unexpected exceptions would empty a ten-deep list inside an hour while the
# real fault was ours. Those all stay transient.
PERMANENT_TWILIO_CODES = frozenset({'21211', '21214', '21217', '21610', '21612', '21614'})


def is_permanent_rejection(detail):
    """True only if `detail` from _send_now means this number is dead for good.

    Reads the detail string rather than a status code because status alone is
    not enough: HTTP 400 covers both "her number is a landline" and "our From
    number is misconfigured", and only the first is hers to pay for.
    """
    if detail == 'invalid_number':
        return True
    parts = (detail or '').split('_')
    return len(parts) == 3 and parts[0] == 'twilio' and parts[2] in PERMANENT_TWILIO_CODES


class ModrynSms(models.AbstractModel):
    """The sender port.

    One seam, two implementations: Twilio when credentials exist, a logger
    otherwise. Nothing else in the codebase knows which one is live.

    That used to end "so tests and demos never text a real person and never
    need an account", and it was true only because credentials were per-database
    and a fresh one held none. They now live in the server's environment and
    every database inherits them, so the sentence is inverted: a test or demo
    database is safe only if it carries modryn.twilio.disabled. On a process
    that exports TWILIO_*, an unflagged fixture texts a real handset.
    """

    _name = 'modryn.sms'
    _description = 'MODRYN SMS sender'

    @api.model
    def _twilio_config(self):
        """The four credentials to send with, or None meaning "log instead".

        Three levels, in order: the tenant's OFF switch, the tenant's own four
        parameters, the platform's four environment variables.

        Each level is all-or-nothing, and a half-filled tenant falls through
        whole rather than borrowing the pieces it is missing. Mixing this
        boutique's account_sid with the platform's from_number produces a send
        that authenticates as one boutique and arrives from another — and the
        recipient sees only the second, so the wrong salon gets the reply.

        The environment is read here, per call, rather than captured at import,
        so scripts/verify.sh can flip it in the running process and walk every
        branch without a restart.
        """
        icp = self.env['ir.config_parameter'].sudo()
        if icp.get_param(P_DISABLED):
            # Until the environment fallback existed, "this database holds zero
            # modryn.twilio.* parameters" WAS the property that made a tenant
            # unable to text a real person, and qa/lib/guard.js plus the two
            # loadtest seeders that hold the gate (gen_tenants.sh and
            # reset_tenants.sh; seed_tenant.py's own guard is the 11-digit phone
            # scheme, a separate defence) each refused to run on it. A platform-wide
            # default empties that count of meaning — every database can send
            # now — so the guards key on this flag instead. Name the direction
            # change rather than let someone rediscover it: a tenant used to be
            # safe until somebody opted it in, and is live until somebody opts
            # it out.
            return None
        cfg = {
            'account_sid': icp.get_param(P_ACCOUNT_SID),
            'key_sid': icp.get_param(P_KEY_SID),
            'key_secret': icp.get_param(P_KEY_SECRET),
            'from': icp.get_param(P_FROM),
        }
        if all(cfg.values()):
            return cfg
        cfg = {
            'account_sid': os.environ.get('TWILIO_ACCOUNT_SID'),
            'key_sid': os.environ.get('TWILIO_API_KEY_SID'),
            'key_secret': os.environ.get('TWILIO_API_KEY_SECRET'),
            'from': os.environ.get('TWILIO_FROM_NUMBER'),
        }
        return cfg if all(cfg.values()) else None

    @api.model
    def send(self, to, body):
        """Send one message NOW, blocking. Returns (ok: bool, detail: str).

        The synchronous door. TWO callers need it, for different reasons, and
        neither is a leftover:

        - the OTP path, where she is staring at the screen waiting for the code
          and there is nothing to show her until Twilio answers;
        - the 24h reminder cron in booking_comms, which stamps
          modryn_reminder_sent_at on success ONLY. That stamp is its retry
          ledger. Move it to send_async and the stamp starts meaning "enqueued",
          so a row that later exhausts its attempts leaves an event marked
          reminded that nobody was reminded about — silently, forever.

        Everything else wants send_async, which keeps a slow Twilio from pinning
        an HTTP worker for SEND_TIMEOUT.
        """
        return self._send_now(to, body)

    @api.model
    def send_async(self, to, body, waitlist_id=None):
        """Queue one message. Returns (ok: bool, detail: str), same contract.

        ok=False means the number can never work and no amount of retrying will
        help — the one failure a caller can still act on the moment it enqueues.
        A queued message that Twilio later rejects is the outbox's problem, and
        it lands in modryn.sms.outbox with the error on the row.

        `waitlist_id` lets a caller whose own state depends on delivery say so:
        the outbox calls that entry back when the message finally gives up. Only
        the waitlist offer needs it, so it is an optional id and not a callback.
        """
        number = normalize_il_phone(to)
        if not number:
            return False, 'invalid_number'
        # Store the normalized form: the drain must not depend on re-parsing
        # whatever the customer originally typed.
        row = self.env['modryn.sms.outbox']._enqueue(number, body, waitlist_id=waitlist_id)
        return True, 'queued_%s' % row.id

    @api.model
    def _send_now(self, to, body):
        """Send one message. Returns (ok: bool, detail: str).

        Never raises for anything Twilio (or a proxy in front of it) can answer
        with: a failed text must not lose the user's request or return a 500 —
        the caller decides what to tell her. The drain keeps its own guard for
        the case this promise is broken by a later edit.
        """
        number = normalize_il_phone(to)
        if not number:
            return False, 'invalid_number'

        cfg = self._twilio_config()
        if not cfg:
            # Development path. The code is logged so the flow is fully testable
            # with no account, no cost and no message to a real handset.
            _logger.info('[modryn.sms] (no Twilio configured) to=%s body=%s', number, body)
            return True, 'logged'

        url = '%s/Accounts/%s/Messages.json' % (TWILIO_BASE, cfg['account_sid'])
        try:
            # API key + secret as basic auth, but the URL still carries the
            # ACCOUNT sid — Twilio scopes the key to the account, it does not
            # replace it.
            response = requests.post(
                url,
                auth=(cfg['key_sid'], cfg['key_secret']),
                data={'From': cfg['from'], 'To': number, 'Body': body},
                timeout=SEND_TIMEOUT,
            )
            if response.status_code in (200, 201):
                # The status IS the answer: Twilio has accepted the message and
                # will deliver it. The sid is a log handle, nothing more, so a
                # body we cannot read must not turn an accepted send into a
                # failure — the outbox would retry and she would get the same
                # text twice. Parsing lives inside the request guard because
                # requests.exceptions.JSONDecodeError is also a RequestException;
                # escaping it wedged the drain on a poison row (_order='id asc'
                # re-picked it first every run), which is why the inner except
                # is ValueError and not the broad class.
                try:
                    return True, response.json().get('sid', 'sent')
                except (ValueError, AttributeError):
                    _logger.warning('[modryn.sms] twilio accepted to=%s (%s) with an '
                                    'unreadable body: %s', number, response.status_code,
                                    response.text[:500])
                    return True, 'sent_unparsed'
        except requests.RequestException as exc:
            _logger.warning('[modryn.sms] transport failure to=%s: %s', number, exc)
            return False, 'transport_error'

        # Twilio explains itself in the body; log it or the failure is undebuggable.
        _logger.warning('[modryn.sms] twilio rejected to=%s status=%s body=%s',
                        number, response.status_code, response.text[:500])
        # Carry Twilio's own error code, not just the status. is_permanent_rejection
        # needs it: HTTP 400 alone cannot tell "her number is a landline" (hers,
        # permanent) from "our From number is not SMS-capable" (ours, transient).
        code = None
        try:
            code = response.json().get('code')
        except (ValueError, AttributeError):
            pass
        if code is None:
            return False, 'twilio_%s' % response.status_code
        return False, 'twilio_%s_%s' % (response.status_code, code)
