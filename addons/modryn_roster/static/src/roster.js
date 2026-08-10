import { rpc } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";
import { Interaction } from "@web/public/interaction";

// A plain page, not an OWL component: the roster has no live feed to hold open,
// so it re-renders by reloading rather than carrying a second client-side model
// of the same data the server already renders.
export class RosterPage extends Interaction {
    static selector = ".modryn_roster_grid";

    setup() {
        this.week = parseInt(this.el.dataset.week || "0", 10);
    }

    start() {
        this.el.addEventListener("click", (ev) => this.onClick(ev));
        this.el.addEventListener("change", (ev) => this.onChange(ev));
        const publish = document.getElementById("modryn_publish_week");
        if (publish) {
            publish.addEventListener("click", () => this.publish());
        }
    }

    async onClick(ev) {
        const button = ev.target.closest(".modryn_avail_btn");
        if (!button) {
            return;
        }
        const result = await rpc("/roster/available", {
            slot_id: parseInt(button.dataset.slot, 10),
            week: this.week,
        });
        // A refused toggle (the week went out while she was looking at it) has
        // to say so, not silently do nothing.
        if (result && result.error) {
            window.alert(result.error);
        }
        window.location.reload();
    }

    async onChange(ev) {
        const box = ev.target.closest(".modryn_assign_box");
        if (!box) {
            return;
        }
        await rpc("/roster/assign", {
            slot_id: parseInt(box.dataset.slot, 10),
            employee_id: parseInt(box.dataset.employee, 10),
            working: box.checked,
            week: this.week,
        });
        window.location.reload();
    }

    async publish() {
        await rpc("/roster/publish", { week: this.week });
        window.location.reload();
    }
}

registry.category("public.interactions").add("modryn_roster.roster_page", RosterPage);
