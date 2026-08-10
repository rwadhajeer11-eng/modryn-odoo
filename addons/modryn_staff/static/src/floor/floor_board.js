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
            myTasks: [],
            atelier: { pieces: [] },
            canAssign: false,
            error: null,
            dragging: false,
            // The finish modal. null = closed; otherwise the /floor/finish payload
            // plus the form the manager is filling in.
            finish: null,
        });

        onWillStart(async () => {
            await this.refresh();
            this.bus.addChannel(QUEUE_CHANNEL);
            this.onBusEvent = () => this.refresh();
            this.bus.subscribe("modryn_queue/update", this.onBusEvent);
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
        this.apply(await rpc(route, params));
    }

    apply(board) {
        if (!board || board.error) {
            this.state.error = board ? board.error : "unreachable";
            return;
        }
        this.state.error = null;
        this.state.pending = board.pending || [];
        this.state.queue = board.queue;
        this.state.bookings = board.bookings;
        this.state.staff = board.staff;
        this.state.myTasks = board.my_tasks || [];
        this.state.atelier = board.atelier || { pieces: [] };
        this.state.canAssign = board.can_assign;
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

    async finish(entryId) {
        await this.call("/floor/finish", { entry_id: entryId });
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
