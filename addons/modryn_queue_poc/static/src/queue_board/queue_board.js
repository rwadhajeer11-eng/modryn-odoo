import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

// The whole realtime probe. Everything below the imports is what a live board
// costs in Odoo: subscribe to a channel, patch local state on each push.
export class QueueBoard extends Component {
    static template = "modryn_queue_poc.QueueBoard";
    static props = {};

    setup() {
        this.orm = useService("orm");
        this.bus = useService("bus_service");
        this.state = useState({ entries: [] });

        onWillStart(async () => {
            await this.load();
            // addChannel + subscribe is the entire client-side contract; the
            // websocket itself is already running for Discuss.
            this.bus.addChannel("modryn_queue");
            this.bus.subscribe("modryn_queue/update", (payload) => this.onUpdate(payload));
        });
    }

    async load() {
        this.state.entries = await this.orm.searchRead(
            "modryn.queue.entry",
            [["state", "!=", "done"]],
            ["name", "phone", "client_type", "state"],
            { order: "create_date asc, id asc" }
        );
    }

    onUpdate(payload) {
        const existing = this.state.entries.find((e) => e.id === payload.id);
        if (payload.state === "done") {
            this.state.entries = this.state.entries.filter((e) => e.id !== payload.id);
        } else if (existing) {
            Object.assign(existing, payload);
        } else {
            // Append, never sort by arrival time on the client: the server's
            // create_date is the fair order and the board must not re-rank it.
            this.state.entries.push(payload);
        }
    }

    async call(entry) {
        await this.orm.call("modryn.queue.entry", "action_call_next", [[entry.id]]);
    }

    async done(entry) {
        await this.orm.call("modryn.queue.entry", "action_done", [[entry.id]]);
    }

    get waiting() {
        return this.state.entries.filter((e) => e.state === "waiting");
    }
}

registry.category("actions").add("modryn_queue_board", QueueBoard);
