import { rpc } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";
import { Interaction } from "@web/public/interaction";

/**
 * The presses on the week planner, on the Shifts screen.
 *
 * An Interaction and NOT a DOMContentLoaded listener, which is what the first
 * version was and why nothing happened when a name was pressed: Odoo's asset
 * bundles are deferred, so DOMContentLoaded has already fired by the time a
 * module in them runs, and a listener registered for it is never called. The
 * page rendered perfectly and every button was inert. RosterPage next door
 * already had the answer.
 *
 * ONE delegated listener on the panel. A handler bound per button is a handler
 * lost the moment anything repaints - the trap roster.js also records.
 */
export class WeekPlanner extends Interaction {
    static selector = ".modryn_planner";

    start() {
        this.el.addEventListener("click", (ev) => this.onClick(ev));
    }

    async onClick(ev) {
        const pick = ev.target.closest(".modryn_planner_pick");
        if (pick) {
            pick.disabled = true;
            await rpc("/roster/assign", {
                slot_id: parseInt(pick.dataset.slot, 10),
                employee_id: parseInt(pick.dataset.employee, 10),
                working: pick.dataset.working === "1",
            });
            this.refresh();
            return;
        }

        const publish = ev.target.closest(".modryn_planner_publish");
        if (publish) {
            publish.disabled = true;
            await rpc("/roster/publish", {
                week: parseInt(publish.dataset.week || "0", 10),
            });
            this.refresh();
        }
    }

    // The routes answer with the grid, but the page AROUND it - the published
    // badge, the window, whether Publish is still pressable - is rendered by
    // the server. Reloading keeps all of those honest rather than updating
    // three and forgetting the fourth.
    refresh() {
        window.location.reload();
    }
}

registry.category("public.interactions").add("modryn_roster.week_planner", WeekPlanner);
