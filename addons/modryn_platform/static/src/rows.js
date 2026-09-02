import { Interaction } from "@web/public/interaction";
import { registry } from "@web/core/registry";

/**
 * "Add another" for the two lists on this site that have no natural end: a
 * shop's partners, and the sign-ins MODRYN issued it.
 *
 * WHY A BUTTON AND NOT MORE BLANK ROWS. Both lists used to render a fixed
 * handful of spare pairs — six partners, three sign-ins — on the reasoning that
 * blank rows are dropped server-side and therefore cost nothing. They cost
 * something: a shop with two partners showed four empty boxes it had to be read
 * past, and a shop with seven had no way to say so without saving and coming
 * back. A button says "as many as you want" in a way six boxes cannot.
 *
 * NOTHING IS REMOVED HERE. Clearing a name is still how a row is dropped,
 * because that already works with no JavaScript at all and a remove button that
 * only sometimes exists is worse than a rule that always holds.
 *
 * THE BUTTON IS HIDDEN IN THE MARKUP and shown here. If this file fails to
 * load, the page keeps the blank rows it was served and every form still
 * saves — nobody is left pressing a button that does nothing.
 *
 * An Interaction and not a DOMContentLoaded listener: this bundle is deferred
 * and that event has already fired by the time it runs. That has cost this
 * project twice.
 */
export class ModrynPlatformRows extends Interaction {
    static selector = "[data-modryn-rows]";

    setup() {
        this.stack = this.el.querySelector("[data-modryn-stack]");
        this.button = this.el.querySelector("[data-modryn-add]");
    }

    start() {
        if (!this.stack || !this.button) {
            return;
        }
        this.button.hidden = false;
        this.button.addEventListener("click", () => this.addRow());
    }

    addRow() {
        const rows = this.stack.querySelectorAll("[data-modryn-row]");
        const last = rows[rows.length - 1];
        if (!last) {
            return;
        }
        // The LAST row, which is always one of the blank ones: cloning the
        // first would copy a partner's name into the new row on the edit form.
        const row = last.cloneNode(true);
        for (const input of row.querySelectorAll("input")) {
            input.value = "";
            // A cloned node keeps the attribute even after value is cleared,
            // and the attribute is what a form reset would put back.
            input.removeAttribute("value");
        }
        this.stack.appendChild(row);
        const first = row.querySelector("input");
        if (first) {
            first.focus();
        }
    }
}

registry.category("public.interactions").add("modryn_platform.rows", ModrynPlatformRows);
