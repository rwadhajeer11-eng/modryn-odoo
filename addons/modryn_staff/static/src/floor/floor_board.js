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
        this.state.unclosed = board.unclosed_count || 0;
        this.state.checklist = board.checklist || [];
        this.state.opsTasks = board.ops_tasks || [];
        if (board.finished) {
            this.state.finish = {
                customer: board.finished.customer,
                phone: board.finished.phone,
                variants: board.finished.variants,
                form: { variant_id: "", piece_ids: [], note: "", due_date: "", seamstress_id: "" },
            };
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

    get colleagues() {
        const me = this.state.me;
        return this.state.staff.filter((s) => !me || s.id !== me.id);
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
        const result = await rpc("/atelier/task/create", {
            customer_name: finish.customer,
            customer_phone: finish.phone,
            variant_id: form.variant_id ? parseInt(form.variant_id, 10) : null,
            piece_ids: form.piece_ids,
            note: form.note,
            seamstress_id: form.seamstress_id ? parseInt(form.seamstress_id, 10) : null,
            due_date: form.due_date || null,
        });
        if (result && result.error) {
            // The modal stays open so nothing typed is lost; the one realistic
            // error here is a missing customer, which cannot happen from this path.
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

    get waitingCount() {
        return this.state.queue.filter((e) => e.state === "waiting").length;
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
