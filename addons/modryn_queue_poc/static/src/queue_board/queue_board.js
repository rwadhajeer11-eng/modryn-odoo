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
            // Re-read, never patch from the push. The push is a bare signal
            // now - it carries an id and nothing else - because the channel it
            // rides on is a guessable string that any client can subscribe to,
            // and it used to carry the customer's name, her phone and the staff
            // note about her. load() goes through the ORM, which is
            // permission-checked; the payload never was.
            this.bus.subscribe("modryn_queue/update", () => this.load());
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
