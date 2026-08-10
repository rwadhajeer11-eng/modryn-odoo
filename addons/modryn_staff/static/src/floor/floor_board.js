import { Component, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { useService } from "@web/core/utils/hooks";

// Same channel modryn_queue_poc publishes on. Reusing it means a walk-in checking
// in at the door reaches this terminal through machinery that already exists,
// rather than a second realtime mechanism invented for the frontend.
const QUEUE_CHANNEL = "modryn_queue";

export class FloorBoard extends Component {
    static template = "modryn_staff.FloorBoard";
    static props = {};

    setup() {
        this.bus = useService("bus_service");
        this.state = useState({
            queue: [],
            bookings: [],
            staff: [],
            canAssign: false,
            error: null,
        });

        onWillStart(async () => {
            await this.refresh();
            // The bus service is in web.assets_frontend, so a public page gets
            // the same websocket the back office uses — no polling loop.
            this.bus.addChannel(QUEUE_CHANNEL);
            this.onBusEvent = () => this.refresh();
            this.bus.subscribe("modryn_queue/update", this.onBusEvent);
        });

        onWillUnmount(() => {
            if (this.onBusEvent) {
                this.bus.unsubscribe("modryn_queue/update", this.onBusEvent);
            }
        });
    }

    async refresh() {
        this.apply(await rpc("/floor/data", {}));
    }

    apply(board) {
        if (!board || board.error) {
            this.state.error = board ? board.error : "unreachable";
            return;
        }
        this.state.error = null;
        this.state.queue = board.queue;
        this.state.bookings = board.bookings;
        this.state.staff = board.staff;
        this.state.canAssign = board.can_assign;
    }

    // A refresh always comes from the server's own view of the board rather than
    // patching local state: assignment changes occupancy for a DIFFERENT record
    // (the employee), which the client cannot compute for itself.
    async assignQueue(entryId, ev) {
        const employeeId = parseInt(ev.target.value, 10);
        if (!employeeId) {
            return;
        }
        this.apply(await rpc("/floor/assign/queue", { entry_id: entryId, employee_id: employeeId }));
    }

    async assignBooking(eventId, ev) {
        const employeeId = parseInt(ev.target.value, 10);
        if (!employeeId) {
            return;
        }
        this.apply(await rpc("/floor/assign/booking", { event_id: eventId, employee_id: employeeId }));
    }

    async finish(entryId) {
        this.apply(await rpc("/floor/queue/done", { entry_id: entryId }));
    }

    // Chosen here rather than inline in the template: a string literal inside a
    // t-esc expression is code to the extractor, not a text node, so it would
    // never reach the .po file.
    clientTypeLabel(entry) {
        return entry.client_type === "bride" ? _t("Bride") : _t("Evening");
    }

    get waitingCount() {
        return this.state.queue.filter((e) => e.state === "waiting").length;
    }

    get freeCount() {
        return this.state.staff.filter((s) => !s.occupied).length;
    }
}

registry.category("public_components").add("modryn_staff.floor_board", FloorBoard);
