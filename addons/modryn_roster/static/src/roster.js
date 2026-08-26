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
        this.bindWeekControls();
        const publish = document.getElementById("modryn_publish_week");
        if (publish) {
            publish.addEventListener("click", () => this.publish());
        }
    }

    // The note, Send, and the manager's shift-type switches sit OUTSIDE the
    // grid, so the grid's delegated listener never sees them. Bound explicitly
    // rather than moved inside it: her answer is about the WEEK, not about any
    // one shift card.
    bindWeekControls() {
        const send = document.getElementById("modryn_send_week");
        if (send) {
            send.addEventListener("click", () => this.send());
        }
        document.querySelectorAll(".modryn_block_type").forEach((box) => {
            box.addEventListener("change", () => this.blockTypes());
        });
    }

    async send() {
        const note = document.getElementById("modryn_week_note");
        const result = await rpc("/roster/send", {
            week: this.week,
            note: note ? note.value : "",
        });
        // window_closed is the case worth naming. She typed a note, pressed
        // Send, and the deadline passed while the page sat open — reloading
        // without a word would look exactly like a successful save.
        if (result && result.error) {
            window.alert(
                result.error === "window_closed"
                    ? "Submissions for that week have closed."
                    : result.error
            );
        }
        window.location.reload();
    }

    // Replace-set, matching the route: every box is read every time, so two
    // managers on two phones cannot each toggle from a different reading of
    // the same state.
    async blockTypes() {
        const types = [...document.querySelectorAll(".modryn_block_type")]
            .filter((box) => box.checked)
            .map((box) => box.dataset.type);
        await rpc("/roster/block", { week: this.week, types });
        window.location.reload();
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
