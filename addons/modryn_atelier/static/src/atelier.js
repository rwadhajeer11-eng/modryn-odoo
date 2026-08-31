import { Interaction } from "@web/public/interaction";
import { registry } from "@web/core/registry";

/**
 * Find a dress by typing, on the workshop's New task form.
 *
 * The field was a <select> of every published variant. On a boutique with three
 * gowns that is fine; on a real rail it is a list nobody can find anything in,
 * and the one screen where somebody is standing at a counter with a bride's
 * dress in her hands is the worst place to make her scroll.
 *
 * THE SAME RULE THE FLOOR'S FINISH PICKER USES, on purpose: from two characters,
 * matching the start of any WORD in the name, the serial or the kind, capped at
 * twelve. Two screens that search the same catalogue must not behave differently
 * - a saleswoman learns it once. See floor_board.js `dressMatches`, which this
 * mirrors deliberately rather than sharing, because that one lives inside an OWL
 * component with its own state and this page has no OWL at all.
 *
 * WORD-prefix and not string-prefix: "אמילי" has to find "שמלת כלה אמילי",
 * where the name is several words and only the first of them starts the string.
 *
 * An Interaction rather than a DOMContentLoaded listener: this bundle is
 * deferred, that event has already fired by the time it runs, and the page then
 * renders with a search box that does nothing. That has already cost this
 * project once, on the roster planner.
 */
export class AtelierDressPicker extends Interaction {
    static selector = ".modryn_dress_pick";

    setup() {
        this.box = this.el.querySelector(".modryn_dress_query");
        this.hits = this.el.querySelector(".modryn_dress_results");
        this.chosen = this.el.querySelector("input[name='variant_id']");
        this.label = this.el.querySelector(".modryn_dress_chosen");
        // Read once: the list is rendered by the server and never changes while
        // the page is open.
        this.rows = [...this.el.querySelectorAll(".modryn_dress_row")].map((r) => ({
            id: r.dataset.variant,
            label: r.dataset.label || "",
            name: r.dataset.name || "",
            serial: r.dataset.serial || "",
            kind: r.dataset.kind || "",
        }));
    }

    start() {
        this.box.addEventListener("input", () => this.search());
        // Enter inside a search box must not submit the form: she is looking
        // for a dress, not saying the task is finished.
        this.box.addEventListener("keydown", (ev) => {
            if (ev.key === "Enter") {
                ev.preventDefault();
            }
        });
        this.hits.addEventListener("click", (ev) => {
            const button = ev.target.closest("[data-variant]");
            if (button) {
                this.pick(button.dataset.variant, button.dataset.label);
            }
        });
        const clear = this.el.querySelector(".modryn_dress_clear");
        if (clear) {
            clear.addEventListener("click", () => this.pick("", ""));
        }
    }

    matches(query) {
        const q = query.trim().toLowerCase();
        if (q.length < 2) {
            return [];
        }
        const startsAWord = (hay) =>
            String(hay || "")
                .toLowerCase()
                .split(/\s+/)
                .some((word) => word.startsWith(q));
        return this.rows
            .filter((r) => startsAWord(r.name) || startsAWord(r.serial) || startsAWord(r.kind))
            // Capped: a kind can match hundreds, and a list that long pushes the
            // rest of the form off the screen.
            .slice(0, 12);
    }

    search() {
        const found = this.matches(this.box.value);
        this.hits.replaceChildren();
        for (const row of found) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "btn btn-sm btn-outline-dark modryn_dress_hit";
            button.dataset.variant = row.id;
            button.dataset.label = row.label;
            button.textContent = row.label;
            this.hits.appendChild(button);
        }
    }

    pick(id, label) {
        this.chosen.value = id;
        this.label.textContent = label;
        // The box is cleared so the list collapses: she has chosen, and a list
        // still hanging open over the rest of the form reads as unfinished.
        this.box.value = "";
        this.hits.replaceChildren();
    }
}

registry
    .category("public.interactions")
    .add("modryn_atelier.dress_picker", AtelierDressPicker);
