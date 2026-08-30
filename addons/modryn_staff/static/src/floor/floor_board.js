import { Component, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { useService } from "@web/core/utils/hooks";
import { useDraggable } from "@web/core/utils/draggable";

// Same channel modryn_queue_poc publishes on: one realtime mechanism for the
// whole floor — walk-ins, assignments and bookings all arrive through it.
const QUEUE_CHANNEL = "modryn_queue";

export class FloorBoard extends Component {
    static template = "modryn_staff.FloorBoard";
    static props = {};

    setup() {
        this.bus = useService("bus_service");
        this.rootRef = useRef("root");
        this.state = useState({
            pending: [],
            queue: [],
            bookings: [],
            staff: [],
            rooms: [],
            me: null,
            sos: [],
            sosForm: null,
            myTasks: [],
            atelier: { pieces: [] },
            canAssign: false,
            canTake: false,
            error: null,
            // modryn_roster: "she isn't on today's rota". Transient by decision,
            // exactly like error above — every apply() clears it and the bus
            // refreshes this board on every floor event, so it is a nudge at the
            // moment of the drop, not a notice that sits there. Who actually
            // worked off-rota is the roster's record to keep, not the board's.
            warning: null,
            dragging: false,
            // The finish modal. null = closed; otherwise the /floor/finish payload
            // plus the form the manager is filling in.
            finish: null,
            // The booking-outcome modal (modryn_ops). null = closed.
            outcomeForm: null,
            // Managers only: past bookings still without an outcome.
            unclosed: 0,
            // modryn_ops: today's opening/closing checklist + follow-up work.
            checklist: [],
            opsTasks: [],
            // modryn_ops: the customer-profile modal. null = closed.
            customer: null,
            // modryn_ops: my own month, private to me. null = unavailable.
            myStats: null,
            // modryn_ops: an error belonging to the OPEN modal — rendered
            // inside it, because state.error sits behind the modal backdrop.
            modalError: null,
            // modryn_ops: the unclosed-bookings list the nag badge opens.
            unclosedList: null,
        });

        onWillStart(async () => {
            await this.refresh();
            this.bus.addChannel(QUEUE_CHANNEL);
            this.onBusEvent = () => this.refresh();
            this.bus.subscribe("modryn_queue/update", this.onBusEvent);
            // My-month numbers are private and change slowly — fetched once,
            // not on every board round-trip.
            try {
                const mine = await rpc("/floor/my/stats", {});
                if (mine && mine.ok) {
                    this.state.myStats = { ...mine.stats, open_followups: mine.open_followups };
                }
            } catch {
                this.state.myStats = null;
            }
        });

        onWillUnmount(() => {
            if (this.onBusEvent) {
                this.bus.unsubscribe("modryn_queue/update", this.onBusEvent);
            }
        });

        // Odoo's pointer-based drag hook: unlike HTML5 drag-and-drop it works on
        // tablets, which is what actually sits at a boutique's front desk.
        // Chips carry data-employee; drop zones carry data-drop-target /
        // data-drop-id / data-drop-primary. The hovered zone is tracked during
        // onDrag because the hook reports only the dragged element on drop.
        useDraggable({
            ref: this.rootRef,
            elements: ".modryn_chip[data-employee]",
            enable: () => this.state.canAssign,
            cursor: "grabbing",
            onDragStart: () => {
                this.state.dragging = true;
            },
            onDrag: ({ element, x, y }) => {
                this.markDropTarget(this.zoneAt(x, y, element));
            },
            onDragEnd: () => {
                this.state.dragging = false;
                this.markDropTarget(null);
            },
            onDrop: async ({ element, x, y }) => {
                const zone = this.zoneAt(x, y, element);
                this.markDropTarget(null);
                if (!zone) {
                    return;
                }
                const employeeId = parseInt(element.dataset.employee, 10);
                const from = element.closest("[data-drop-target]");
                const zoneKind = zone.dataset.dropTarget;

                if (zoneKind === "bench") {
                    // Dragging a chip home = taking her off the card she came from.
                    if (from && from.dataset.dropTarget !== "bench") {
                        await this.call("/floor/unassign", {
                            target: from.dataset.dropTarget,
                            target_id: parseInt(from.dataset.dropId, 10),
                            employee_id: employeeId,
                        });
                    }
                    return;
                }
                await this.call("/floor/assign", {
                    target: zoneKind,
                    target_id: parseInt(zone.dataset.dropId, 10),
                    employee_id: employeeId,
                    as_primary: zone.dataset.dropPrimary === "1",
                });
            },
        });
    }

    // ------------------------------------------------------------------ data
    async refresh() {
        this.apply(await rpc("/floor/data", {}));
    }

    async call(route, params) {
        // The manager's next deliberate action supersedes the last notice. A
        // bus-driven refresh is not an action and leaves it standing.
        this.state.warning = null;
        this.apply(await rpc(route, params));
    }

    apply(board) {
        // A payload can carry BOTH a board and an error (a room collision
        // returns the fresh truth plus a message). Discarding the board in
        // that case left the select lying about where the customer is — so
        // only an error-ONLY payload short-circuits.
        if (!board || (board.error && !board.queue)) {
            this.state.error = board ? this.errorText(board.error) : "unreachable";
            return;
        }
        this.state.error = board.error ? this.errorText(board.error) : null;
        // Already a translated sentence from the server, not a code — it does
        // not go through errorText().
        //
        // Only /floor/assign ever sends this key, and it must survive the board
        // refresh that the SAME assignment triggers: writing modryn_employee_id
        // pushes on the modryn_queue channel (assignment.py), the open board
        // re-renders within milliseconds, and an unconditional reset here made
        // the off-rota notice a sub-second flash — the feature's only
        // assign-time output, invisible. Cleared on the manager's next action
        // instead, in call().
        if ("warning" in board) {
            this.state.warning = board.warning || null;
        }
        this.state.pending = board.pending || [];
        this.state.queue = board.queue;
        this.state.bookings = board.bookings;
        this.state.staff = board.staff;
        this.state.rooms = board.rooms || [];
        this.state.me = board.me || null;
        this.state.sos = board.sos || [];
        this.state.myTasks = board.my_tasks || [];
        this.state.atelier = board.atelier || { pieces: [] };
        this.state.canAssign = board.can_assign;
        this.state.canTake = board.can_take;
        this.state.unclosed = board.unclosed_count || 0;
        this.state.checklist = board.checklist || [];
        this.state.opsTasks = board.ops_tasks || [];
        if (board.finished) {
            this.state.finish = {
                customer: board.finished.customer,
                phone: board.finished.phone,
                variants: board.finished.variants,
                entryId: board.finished.entry_id,
                // What she has typed into the dress box, and the one she picked.
                dressQuery: "",
                dressLabel: "",
                // What has been recorded so far: "" until she says. Once said,
                // the question collapses into a line of text - re-asking a
                // question already answered is how a double sale gets recorded.
                outcome: "",
                // Which ending is waiting on a yes. null = nothing pending.
                confirm: null,
                // Has she asked for the workshop? Closed until she does: a
                // bride whose gown fits needs no task, and a form sitting open
                // with a required date reads as something she must finish.
                workshop: false,
                form: { variant_id: "", piece_ids: [], note: "", due_date: "", seamstress_id: "", priority: "1" },
                error: "",
            };
        }
    }

    // The dresses matching what she has typed, at most a handful.
    //
    // Prefix and not "contains", per WORD as well as per field: typing 10 offers
    // 1042 and 1099 rather than every dress with a 10 buried in it, and "אמי"
    // finds "שמלת כלה אמילי" because a name is several words and only the first
    // of them starts the string. The same rule the catalogue's own search uses,
    // so the two screens behave the same way.
    get dressMatches() {
        const f = this.state.finish;
        if (!f) {
            return [];
        }
        const q = (f.dressQuery || "").trim().toLowerCase();
        if (q.length < 2) {
            return [];
        }
        const starts = (hay, needle) =>
            String(hay || "").toLowerCase().split(/\s+/).some((w) => w.startsWith(needle));
        return f.variants
            .filter((v) => starts(v.name, q) || starts(v.serial, q) || starts(v.kind, q))
            // Capped, because a kind can match hundreds and a list that long
            // inside a dialog is the wall this replaced.
            .slice(0, 12);
    }

    pickDress(v) {
        const f = this.state.finish;
        f.form.variant_id = String(v.id);
        f.dressLabel = v.label;
        // The box is cleared so the list collapses: she has chosen, and a list
        // still hanging open over the rest of the form reads as unfinished.
        f.dressQuery = "";
    }

    clearDress() {
        const f = this.state.finish;
        f.form.variant_id = "";
        f.dressLabel = "";
        f.dressQuery = "";
        // A pending "she took this one" is about a dress that is no longer
        // chosen. Leaving it open would confirm the wrong gown.
        if (f.confirm === "sold") {
            f.confirm = null;
        }
    }

    // ------------------------------------------------------- how it ended
    askOutcome(kind) {
        const f = this.state.finish;
        if (kind === "sold" && !f.form.variant_id) {
            // Nothing to take off the rail. Said here rather than swallowed by
            // the server, so she is told before she has confirmed anything.
            f.error = this.errorText("missing_dress");
            return;
        }
        f.error = "";
        f.confirm = kind;
    }

    cancelOutcome() {
        this.state.finish.confirm = null;
    }

    openWorkshop() {
        this.state.finish.workshop = true;
    }

    // The sentence on the confirmation strip. Spelled out with the dress in it,
    // because "are you sure?" over a list of a thousand gowns tells her nothing
    // about which one she is about to take off the rail.
    get confirmSentence() {
        const f = this.state.finish;
        if (!f || !f.confirm) {
            return "";
        }
        if (f.confirm === "not_sold") {
            return _t("Record that %s left without buying anything?", f.customer);
        }
        const v = f.variants.find((x) => String(x.id) === String(f.form.variant_id));
        if (!v) {
            return "";
        }
        return _t("Take one %(dress)s off the rail? %(from)s left, then %(to)s.", {
            dress: v.label,
            from: v.stock,
            to: Math.max(0, v.stock - 1),
        });
    }

    async confirmOutcome() {
        const f = this.state.finish;
        const kind = f.confirm;
        if (!kind) {
            return;
        }
        const board = await rpc("/floor/walkin/outcome", {
            entry_id: f.entryId,
            outcome: kind,
            variant_id: kind === "sold" ? parseInt(f.form.variant_id, 10) : null,
        });
        if (board && board.error) {
            f.confirm = null;
            f.error = this.errorText(board.error);
            return;
        }
        // The modal stays OPEN. She may still have work for the workshop, and
        // closing the dialog under her the moment she answers the first
        // question would lose the second one.
        f.confirm = null;
        f.outcome = kind;
        f.error = "";
        this.applyBoardOnly(board);
    }

    // The board underneath, refreshed, with the dialog left standing.
    //
    // apply() rebuilds state.finish only when the payload carries a `finished`
    // key, and /floor/walkin/outcome never sends one - so the open dialog
    // survives this on its own. Said out loud because the first version saved
    // and restored it, which implied a danger that is not there and would have
    // quietly hidden a real one if apply() ever started clearing it.
    applyBoardOnly(board) {
        if (board && board.queue) {
            this.apply(board);
        }
    }

    // ------------------------------------------------------------------- dnd
    zoneAt(x, y, dragged) {
        // GEOMETRY, not DOM hit-testing. During a drag Odoo's hook suppresses
        // pointer-events across the page (that is how it runs its own
        // hit-tests), so elementFromPoint/elementsFromPoint return nothing but
        // <html> — a probe showed exactly that, and it silently swallowed every
        // drop. Bounding rects don't care about pointer-events. The innermost
        // (smallest) zone containing the pointer wins, so the primary slot
        // beats the card it sits in, and the card beats the page.
        let best = null;
        let bestArea = Infinity;
        for (const zone of this.rootRef.el.querySelectorAll("[data-drop-target]")) {
            if (dragged && dragged.contains(zone)) {
                continue;
            }
            const r = zone.getBoundingClientRect();
            if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) {
                const area = r.width * r.height;
                if (area < bestArea) {
                    best = zone;
                    bestArea = area;
                }
            }
        }
        return best;
    }

    markDropTarget(zone) {
        for (const el of this.rootRef.el.querySelectorAll(".modryn_drop_hover")) {
            el.classList.remove("modryn_drop_hover");
        }
        if (zone) {
            zone.classList.add("modryn_drop_hover");
        }
    }

    // ------------------------------------------------- click fallback (a11y)
    async assignFromSelect(target, targetId, ev) {
        const employeeId = parseInt(ev.target.value, 10);
        ev.target.value = "";
        if (!employeeId) {
            return;
        }
        await this.call("/floor/assign", {
            target,
            target_id: targetId,
            employee_id: employeeId,
        });
    }

    async unassign(target, targetId, employeeId) {
        await this.call("/floor/unassign", {
            target,
            target_id: targetId,
            employee_id: employeeId,
        });
    }

    // ----------------------------------------------------------- rooms
    // Is the signed-in employee on this card at all - as its primary or as a
    // helper? The board already knows who she is (me_id), so this is the same
    // question the release route asks server-side, and the two must agree or a
    // button appears that the route then refuses.
    iAmOn(card) {
        // state.me and card.helpers are the shapes /floor/data really sends -
        // an object, and a list of {id, name}. Guessing me_id / helper_ids gave
        // a button that rendered and could never be true, so "Back to the line"
        // would simply never have appeared.
        const me = this.state.me && this.state.me.id;
        if (!me) {
            return false;
        }
        return card.employee_id === me
            || (card.helpers || []).some((h) => h.id === me);
    }

    async take(target, id) {
        this.apply(await rpc("/floor/take", { target, target_id: id }));
    }

    async release(target, id) {
        this.apply(await rpc("/floor/release", { target, target_id: id }));
    }

    async setNote(id, ev) {
        this.apply(await rpc("/floor/note", { target_id: id, note: ev.target.value }));
    }

    async setClientType(id, ev) {
        this.apply(await rpc("/floor/client-type", {
            target_id: id, client_type: ev.target.value }));
    }

    async setRoom(target, targetId, ev) {
        const roomId = ev.target.value ? parseInt(ev.target.value, 10) : null;
        const board = await rpc("/floor/room", {
            target,
            target_id: targetId,
            room_id: roomId,
        });
        // A room collision comes back with the board intact plus a message;
        // apply() now keeps both — the select snaps back to the truth AND the
        // message shows, instead of the error discarding the fresh board.
        this.apply(board);
    }

    roomName(roomId) {
        const room = this.state.rooms.find((r) => r.id === roomId);
        return room ? room.name : "";
    }

    // ------------------------------------------------------------- SOS
    openSos(card, cardId) {
        this.state.sosForm = { card, card_id: cardId, target_id: "", note: "" };
    }

    closeSos() {
        this.state.sosForm = null;
    }

    async sendSos() {
        const form = this.state.sosForm;
        await this.call("/floor/sos", {
            target: form.target_id ? "employee" : "manager",
            target_id: form.target_id ? parseInt(form.target_id, 10) : null,
            card: form.card,
            card_id: form.card_id,
            note: form.note,
        });
        this.state.sosForm = null;
    }

    async ackSos(callId) {
        await this.call("/floor/sos/ack", { call_id: callId });
    }

    async resolveSos(callId) {
        await this.call("/floor/sos/resolve", { call_id: callId });
    }

    get incomingSos() {
        // The overlay is for calls I must answer. My own call stays visible as a
        // quiet strip instead, so I can see somebody picked it up.
        const me = this.state.me;
        if (!me) {
            return [];
        }
        return this.state.sos.filter((c) => c.caller_id !== me.id);
    }

    get myOwnSos() {
        const me = this.state.me;
        return me ? this.state.sos.filter((c) => c.caller_id === me.id) : [];
    }

    // Is this walk-in with somebody right now? The one definition of it, used
    // by both panels: the with-the-team list is built from it, and the queue
    // list is built from its negation. Two copies would drift into a bride
    // shown twice or shown nowhere.
    isWithSomebody(entry) {
        return Boolean(entry.employee_id) && entry.state === "called" && !entry.outcome;
    }

    // The line: people who are actually waiting. Somebody a stylist has taken
    // is on the panel below and is not waiting for anybody.
    get waitingQueue() {
        return this.state.queue.filter((e) => !this.isWithSomebody(e));
    }

    get colleagues() {
        const me = this.state.me;
        return this.state.staff.filter((s) => !me || s.id !== me.id);
    }

    // The customers being served right now, gathered under the woman serving
    // them. Walk-ins and today's bookings alike: from where a manager is
    // standing they are the same thing, a person in the shop with somebody.
    //
    // A booking with an outcome already recorded drops out - it is over, and a
    // panel about who is busy NOW should not still be listing it.
    get withTeam() {
        const groups = new Map();
        const put = (id, name, row) => {
            if (!groups.has(id)) {
                groups.set(id, { id, name, customers: [] });
            }
            groups.get(id).customers.push(row);
        };
        for (const e of this.state.queue) {
            // 'called' and not merely assigned. A manager can hand a WAITING
            // customer to a stylist - "you take this one next" - and that woman
            // is not with her yet. Listing her here made the panel claim
            // something untrue and, worse, offered a "Back to the line" that
            // did nothing: the release refuses a card that is not called.
            if (this.isWithSomebody(e)) {
                put(e.employee_id, e.employee_name, {
                    key: `q${e.id}`,
                    id: e.id,
                    kind: "queue",
                    name: e.name,
                    detail: e.phone || "",
                    helpers: e.helpers || [],
                });
            }
        }
        for (const b of this.state.bookings) {
            // Happening, not merely today. An appointment at five in the
            // afternoon is not somebody a stylist is with at ten in the
            // morning, and this panel is about the room as it stands.
            if (b.employee_id && b.in_progress && !b.outcome) {
                put(b.employee_id, b.employee_name, {
                    key: `b${b.id}`,
                    id: b.id,
                    kind: "booking",
                    name: b.title,
                    detail: b.time,
                    helpers: b.helpers || [],
                    booking: b,
                });
            }
        }
        // Ordered by name so the panel does not reshuffle itself under a
        // manager's finger every time the bus pushes a refresh.
        return [...groups.values()].sort((a, b) => (a.name || "").localeCompare(b.name || ""));
    }

    // How many people are being served, across every stylist.
    get withTeamCount() {
        return this.withTeam.reduce((n, g) => n + g.customers.length, 0);
    }

    // May the signed-in user end this one? The same rule the server enforces:
    // a manager, or the woman actually holding her. Drawn from it rather than
    // guessed, so a button that appears always works.
    mayClose(row) {
        if (this.state.canAssign) {
            return true;
        }
        const me = this.state.me;
        if (!me) {
            return false;
        }
        const card = row.kind === "queue"
            ? this.state.queue.find((e) => e.id === row.id)
            : this.state.bookings.find((b) => b.id === row.id);
        return Boolean(card) && (card.employee_id === me.id
            || (card.helpers || []).some((h) => h.id === me.id));
    }

    // "Finished" means different things to the two kinds, and both already
    // have a screen for it - this only sends her to the right one.
    endVisit(row) {
        if (row.kind === "queue") {
            return this.finish(row.id);
        }
        return this.openOutcome(row.booking);
    }

    async finish(entryId) {
        await this.call("/floor/finish", { entry_id: entryId });
    }

    // ------------------------------------------------- booking outcome modal
    // Served by modryn_ops when installed. A stylist may close her OWN
    // booking; a manager closes (or, with force, changes) any — the server
    // re-checks both, this only decides what to draw.
    canClose(b) {
        return this.state.canAssign || (this.state.me && b.employee_id === this.state.me.id);
    }

    outcomeLabel(outcome) {
        return { sold: _t("Sold"), not_sold: _t("Not sold"), no_show: _t("No-show") }[outcome] || outcome;
    }

    openOutcome(b) {
        // Changing a recorded outcome is manager-only; the button simply
        // doesn't render for staff when b.outcome is set (see canClose use).
        // A correction PREFILLS the recorded figures — a re-save must carry
        // the existing sale, not silently zero it.
        this.state.unclosedList = null;
        this.state.modalError = null;
        this.state.outcomeForm = {
            event_id: b.id,
            customer: b.title,
            kind: b.outcome || "",
            amount: b.sale_amount ? String(b.sale_amount) : "",
            items: b.sale_items || "",
            note: "",
            existing: b.outcome || "",
        };
    }

    closeOutcome() {
        this.state.outcomeForm = null;
        this.state.modalError = null;
    }

    pickOutcome(kind) {
        this.state.outcomeForm.kind = kind;
    }

    async saveOutcome() {
        const f = this.state.outcomeForm;
        if (!f || !f.kind) {
            return;
        }
        const board = await rpc("/floor/finish/booking", {
            event_id: f.event_id,
            outcome: f.kind,
            amount: f.amount ? parseFloat(f.amount) : 0,
            items: f.items,
            note: f.note,
            force: Boolean(f.existing),
        });
        if (board && board.error && !board.queue) {
            // Inside the modal, in words — a code behind the backdrop reads
            // as "Save did nothing".
            this.state.modalError = this.errorText(board.error);
            return;
        }
        this.state.outcomeForm = null;
        this.state.modalError = null;
        // On a sale the payload carries `finished`, so apply() opens the
        // alteration handoff modal next — same chain as a walk-in.
        this.apply(board);
    }

    // ---------------------------------------- unclosed bookings (modryn_ops)
    async openUnclosed() {
        const result = await rpc("/floor/unclosed", {});
        if (result && result.error) {
            this.state.error = this.errorText(result.error);
            return;
        }
        this.state.unclosedList = result.unclosed || [];
    }

    closeUnclosed() {
        this.state.unclosedList = null;
    }

    async acceptPending(entryId) {
        await this.call("/floor/accept", { entry_id: entryId });
    }

    async redirectPending(entryId) {
        await this.call("/floor/redirect", { entry_id: entryId });
    }

    // ---------------------------------------------------------- finish modal
    closeFinish() {
        this.state.finish = null;
    }

    togglePiece(pieceId, ev) {
        const list = this.state.finish.form.piece_ids;
        const index = list.indexOf(pieceId);
        if (ev.target.checked && index === -1) {
            list.push(pieceId);
        } else if (!ev.target.checked && index !== -1) {
            list.splice(index, 1);
        }
    }

    async createTask() {
        const finish = this.state.finish;
        const form = finish.form;
        // The queue orders by priority and due date, so a task without a due
        // date is refused server-side — catch it here first, before the typed
        // form is at risk.
        if (!form.due_date) {
            finish.error = this.errorText("missing_due");
            return;
        }
        const result = await rpc("/atelier/task/create", {
            customer_name: finish.customer,
            customer_phone: finish.phone,
            variant_id: form.variant_id ? parseInt(form.variant_id, 10) : null,
            piece_ids: form.piece_ids,
            note: form.note,
            seamstress_id: form.seamstress_id ? parseInt(form.seamstress_id, 10) : null,
            due_date: form.due_date,
            priority: form.priority,
        });
        if (result && result.error) {
            // The modal stays open so nothing typed is lost — and the reason
            // is shown, not swallowed.
            finish.error = this.errorText(result.error);
            return;
        }
        this.state.finish = null;
        await this.refresh();
    }

    // Server error codes -> words a person on the floor can act on. Unknown
    // codes fall through verbatim rather than hiding.
    errorText(code) {
        return {
            forbidden: _t("You don't have permission for that."),
            not_found: _t("That record is gone — refresh the board."),
            already_set: _t("An outcome is already recorded. Only a manager can change it."),
            cancelled: _t("This booking was cancelled — it doesn't get an outcome."),
            invalid_amount: _t("Please enter a valid amount."),
            invalid_date: _t("Please enter a valid date."),
            missing_dress: _t("Pick which dress she took first."),
            missing_due: _t("Please pick a due date — the workshop queue runs on it."),
            missing_priority: _t("Please pick a priority."),
            invalid_budget: _t("Please enter a valid budget."),
        }[code] || code;
    }

    // --------------------------------------- customer profile (modryn_ops)
    async openCustomer(name, phone) {
        if (!phone) {
            return;
        }
        const result = await rpc("/floor/customer", { phone });
        if (result && result.error) {
            this.state.error = result.error;
            return;
        }
        const c = result.customer;
        this.state.modalError = null;
        this.state.customer = {
            name: c ? c.name : name,
            phone,
            isNew: !c,
            // 'budget' is present in the payload only for managers — its
            // absence is the server's field-level ACL, mirrored here.
            hasBudget: Boolean(c && "budget" in c),
            form: {
                wedding_date: c ? c.wedding_date : "",
                party: c ? c.party : "",
                measurements: c ? c.measurements : "",
                notes: c ? c.notes : "",
                budget: c && "budget" in c ? c.budget || "" : "",
            },
            category: c ? c.category : "",
        };
    }

    closeCustomer() {
        this.state.customer = null;
        this.state.modalError = null;
    }

    async saveCustomer() {
        const c = this.state.customer;
        const params = {
            phone: c.phone,
            name: c.name,
            wedding_date: c.form.wedding_date || null,
            party: c.form.party,
            measurements: c.form.measurements,
            notes: c.form.notes,
        };
        if (c.hasBudget || (c.isNew && this.state.canAssign)) {
            params.budget = c.form.budget === "" ? 0 : parseFloat(c.form.budget);
        }
        const result = await rpc("/floor/customer/save", params);
        if (result && result.error) {
            this.state.modalError = this.errorText(result.error);
            return;
        }
        this.state.customer = null;
        this.state.modalError = null;
    }

    // ------------------------------------------- floor tasks (modryn_ops)
    async doneOpsTask(taskId) {
        const result = await rpc("/tasks/done", { task_id: taskId });
        if (result && result.error) {
            this.state.error = this.errorText(result.error);
        }
        // Refresh EVEN on error: the native checkbox click already toggled
        // the DOM, and only a redraw from server truth un-lies it.
        await this.refresh();
    }

    async reopenOpsTask(taskId) {
        const result = await rpc("/tasks/reopen", { task_id: taskId });
        if (result && result.error) {
            this.state.error = this.errorText(result.error);
        }
        await this.refresh();
    }

    // ------------------------------------------------------- my alterations
    async advanceTask(taskId, target) {
        const result = await rpc("/atelier/advance", { task_id: taskId, target });
        if (result && result.ok) {
            await this.refresh();
        }
    }

    clientTypeLabel(entry) {
        return entry.client_type === "bride" ? _t("Bride") : _t("Evening");
    }

    // The number over the line counts the line. It counted state === 'waiting'
    // only, which leaves out somebody called over with nobody assigned to her -
    // she is on the list below the number and was not in it. A count that does
    // not describe the rows under it is worse than no count.
    get waitingCount() {
        return this.waitingQueue.length;
    }

    get freeCount() {
        return this.state.staff.filter((s) => !s.occupied).length;
    }

    get seamstresses() {
        // Any staff member can take alteration work; the owner's role names are
        // free-form data, so filtering by role text would be guessing.
        return this.state.staff;
    }
}

registry.category("public_components").add("modryn_staff.floor_board", FloorBoard);
