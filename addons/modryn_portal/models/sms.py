import logging
import re

import requests

from odoo import api, models

_logger = logging.getLogger(__name__)

TWILIO_BASE = 'https://api.twilio.com/2010-04-01'
SEND_TIMEOUT = 10

# Config keys. Per-database, so each boutique could eventually carry its own
# sender identity without a code change.
P_ACCOUNT_SID = 'modryn.twilio.account_sid'
P_KEY_SID = 'modryn.twilio.api_key_sid'
P_KEY_SECRET = 'modryn.twilio.api_key_secret'
P_FROM = 'modryn.twilio.from_number'


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
        return '+' + digits
    return None


class ModrynSms(models.AbstractModel):
    """The sender port.

    One seam, two implementations: Twilio when credentials exist, a logger
    otherwise. Nothing else in the codebase knows which one is live, so tests
    and demos never text a real person and never need an account.
    """

    _name = 'modryn.sms'
    _description = 'MODRYN SMS sender'

    @api.model
    def _twilio_config(self):
        icp = self.env['ir.config_parameter'].sudo()
        cfg = {
            'account_sid': icp.get_param(P_ACCOUNT_SID),
            'key_sid': icp.get_param(P_KEY_SID),
            'key_secret': icp.get_param(P_KEY_SECRET),
            'from': icp.get_param(P_FROM),
        }
        return cfg if all(cfg.values()) else None

    @api.model
    def send(self, to, body):
        """Send one message. Returns (ok: bool, detail: str).

        Never raises: a failed text must not lose the user's request or return a
        500 — the caller decides what to tell her.
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
        except requests.RequestException as exc:
            _logger.warning('[modryn.sms] transport failure to=%s: %s', number, exc)
            return False, 'transport_error'

        if response.status_code in (200, 201):
            return True, response.json().get('sid', 'sent')

        # Twilio explains itself in the body; log it or the failure is undebuggable.
        _logger.warning('[modryn.sms] twilio rejected to=%s status=%s body=%s',
                        number, response.status_code, response.text[:500])
        return False, 'twilio_%s' % response.status_code
