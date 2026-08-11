from datetime import datetime, time, timedelta

import pytz

from odoo import http
from odoo.http import request

TZ = pytz.timezone('Asia/Jerusalem')

GROUP_STAFF = 'modryn_staff.group_boutique_staff'
GROUP_MANAGER = 'modryn_staff.group_shift_manager'


def _pct(part, whole):
    return round(100.0 * part / whole) if whole else 0


def _range_stats(cr, date_from, date_to, employee_id=None):
    """The KPI dictionary over one UTC window, straight SQL.

    Live queries, no stored aggregates: a boutique closes a thousand-odd
    bookings a year, and a stored rollup is a staleness bug waiting for a
    backfill. Contact-based conversion keys on the customer PHONE — partners
    are phone search-or-create, so phone IS the contact identity here.
    """
    emp = " AND modryn_employee_id = %(emp)s" if employee_id else ""
    params = {'dfrom': date_from, 'dto': date_to, 'emp': employee_id}

    cr.execute("""
        SELECT count(*) FILTER (WHERE modryn_outcome = 'sold')      AS sold,
               count(*) FILTER (WHERE modryn_outcome = 'not_sold')  AS not_sold,
               count(*) FILTER (WHERE modryn_outcome = 'no_show')   AS no_show,
               coalesce(sum(modryn_sale_amount)
                        FILTER (WHERE modryn_outcome = 'sold'), 0)  AS revenue,
               count(DISTINCT modryn_customer_phone)
                        FILTER (WHERE modryn_outcome = 'sold')      AS contacts_sold,
               count(DISTINCT modryn_customer_phone)
                        FILTER (WHERE modryn_outcome IN ('sold', 'not_sold'))
                                                                    AS contacts_completed,
               count(*) FILTER (WHERE modryn_outcome = 'not_sold'
                                AND modryn_feedback_sent_at IS NOT NULL)
                                                                    AS feedback_sent
          FROM calendar_event
         WHERE modryn_is_booking IS TRUE
           AND modryn_cancelled_at IS NULL
           AND modryn_outcome IS NOT NULL
           AND start >= %%(dfrom)s AND start < %%(dto)s%s
    """ % emp, params)
    row = cr.dictfetchone()

    completed = row['sold'] + row['not_sold']
    closed = completed + row['no_show']
    stats = {
        'sold': row['sold'],
        'not_sold': row['not_sold'],
        'no_show': row['no_show'],
        'closed': closed,
        'revenue': row['revenue'],
        'atv': round(row['revenue'] / row['sold']) if row['sold'] else 0,
        'conversion': _pct(row['sold'], completed),
        'contact_conversion': _pct(row['contacts_sold'], row['contacts_completed']),
        'no_show_rate': _pct(row['no_show'], closed),
        'feedback_sent': row['feedback_sent'],
    }

    # Bookings FOR the window that were cancelled, and past ones still
    # unresolved — the number the nag badge exists to drive to zero.
    cr.execute("""
        SELECT count(*) FILTER (WHERE modryn_cancelled_at IS NOT NULL) AS cancelled,
               count(*) FILTER (WHERE modryn_cancelled_at IS NULL
                                AND modryn_outcome IS NULL
                                AND start < (now() at time zone 'UTC')) AS unclosed
          FROM calendar_event
         WHERE modryn_is_booking IS TRUE
           AND start >= %%(dfrom)s AND start < %%(dto)s%s
    """ % emp, params)
    row = cr.dictfetchone()
    stats['cancelled'] = row['cancelled']
    stats['unclosed'] = row['unclosed']

    task_emp = " AND employee_id = %(emp)s" if employee_id else ""
    cr.execute("""
        SELECT count(*) FILTER (WHERE template_id IS NULL
                                AND task_type IN ('follow_up', 'no_show_recovery')) AS fu_total,
               count(*) FILTER (WHERE template_id IS NULL
                                AND task_type IN ('follow_up', 'no_show_recovery')
                                AND state = 'done')                                 AS fu_done,
               count(*) FILTER (WHERE template_id IS NOT NULL)                      AS cl_total,
               count(*) FILTER (WHERE template_id IS NOT NULL AND state = 'done')   AS cl_done
          FROM modryn_task
         WHERE due_at >= %%(dfrom)s AND due_at < %%(dto)s%s
    """ % task_emp, params)
    row = cr.dictfetchone()
    stats.update({
        'followups_total': row['fu_total'],
        'followups_done': row['fu_done'],
        'followup_rate': _pct(row['fu_done'], row['fu_total']),
        'checklist_total': row['cl_total'],
        'checklist_done': row['cl_done'],
        'checklist_rate': _pct(row['cl_done'], row['cl_total']),
    })
    return stats


class ModrynOpsReports(http.Controller):
    """Conversion and completion, computed from outcomes and tasks.

    Attribution is the ORIGINAL stylist (modryn_employee_id) by settled
    decision; the closer is stored on every outcome, so switching to
    last-touch later is a query change, not a migration.

    Per-stylist numbers are PRIVATE: a stylist reaches only /floor/my/stats,
    which is hard-scoped to her own employee record. No leaderboards.
    """

    def _user(self):
        user = request.env.user
        return None if (not user or user._is_public()) else user

    def _is_staff(self):
        user = self._user()
        return bool(user) and user.has_group(GROUP_STAFF)

    def _is_manager(self):
        user = self._user()
        return bool(user) and user.has_group(GROUP_MANAGER)

    def _my_employee(self):
        user = self._user()
        if not user:
            return None
        return request.env['hr.employee'].sudo().search(
            [('user_id', '=', user.id)], limit=1)

    @staticmethod
    def _parse_range(date_from, date_to):
        """Jerusalem dates in, UTC window out. Defaults to the current month."""
        today = datetime.now(TZ).date()
        try:
            start = datetime.strptime(date_from, '%Y-%m-%d').date() if date_from else today.replace(day=1)
        except (TypeError, ValueError):
            start = today.replace(day=1)
        try:
            end = datetime.strptime(date_to, '%Y-%m-%d').date() if date_to else today
        except (TypeError, ValueError):
            end = today
        if end < start:
            start, end = end, start
        dfrom = TZ.localize(datetime.combine(start, time.min)).astimezone(pytz.utc).replace(tzinfo=None)
        dto = TZ.localize(datetime.combine(end + timedelta(days=1), time.min)).astimezone(pytz.utc).replace(tzinfo=None)
        return start, end, dfrom, dto

    @http.route('/manage/reports', type='http', auth='user', website=True, sitemap=False)
    def reports(self, **kw):
        if not self._is_manager():
            return request.not_found()
        start, end, dfrom, dto = self._parse_range(kw.get('from'), kw.get('to'))
        cr = request.env.cr
        stats = _range_stats(cr, dfrom, dto)

        cr.execute("""
            SELECT e.id, e.name,
                   count(*)                                              AS closed,
                   count(*) FILTER (WHERE c.modryn_outcome = 'sold')     AS sold,
                   count(*) FILTER (WHERE c.modryn_outcome = 'not_sold') AS not_sold,
                   count(*) FILTER (WHERE c.modryn_outcome = 'no_show')  AS no_show,
                   coalesce(sum(c.modryn_sale_amount)
                            FILTER (WHERE c.modryn_outcome = 'sold'), 0) AS revenue
              FROM calendar_event c
              JOIN hr_employee e ON e.id = c.modryn_employee_id
             WHERE c.modryn_is_booking IS TRUE
               AND c.modryn_cancelled_at IS NULL
               AND c.modryn_outcome IS NOT NULL
               AND c.start >= %s AND c.start < %s
             GROUP BY e.id, e.name
             ORDER BY sold DESC, revenue DESC, e.name
        """, (dfrom, dto))
        stylists = []
        for row in cr.dictfetchall():
            completed = row['sold'] + row['not_sold']
            stylists.append({
                # hr_employee.name is a plain varchar (related to
                # resource_resource.name, not translated) — no jsonb handling.
                'name': row['name'],
                'closed': row['closed'],
                'sold': row['sold'],
                'no_show': row['no_show'],
                'conversion': _pct(row['sold'], completed),
                'revenue': round(row['revenue']),
                'atv': round(row['revenue'] / row['sold']) if row['sold'] else 0,
            })

        return request.render('modryn_ops.manage_reports', {
            'stats': stats,
            'stylists': stylists,
            'date_from': start.strftime('%Y-%m-%d'),
            'date_to': end.strftime('%Y-%m-%d'),
            'active_tab': 'reports',
        })

    @http.route('/floor/my/stats', type='jsonrpc', auth='user')
    def my_stats(self):
        """The signed-in stylist's own month — nobody else's. The scoping is
        the employee lookup itself: there is no parameter to widen."""
        if not self._is_staff():
            return {'error': 'forbidden'}
        me = self._my_employee()
        if not me:
            return {'error': 'no_employee'}
        _start, _end, dfrom, dto = self._parse_range(None, None)
        stats = _range_stats(request.env.cr, dfrom, dto, employee_id=me.id)
        open_followups = request.env['modryn.task'].sudo().search_count([
            ('employee_id', '=', me.id), ('state', '=', 'open'),
            ('task_type', 'in', ('follow_up', 'no_show_recovery'))])
        return {'ok': True, 'stats': stats, 'open_followups': open_followups}
