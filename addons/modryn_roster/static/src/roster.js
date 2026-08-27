import { _t } from "@web/core/l10n/translation";
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

    // Send sits OUTSIDE the grid's delegated listener's reach when it is
    // rendered above the table, so it is bound explicitly. This function exists
    // because that already bit once.
    bindWeekControls() {
        const send = document.getElementById("modryn_send_week");
        if (send) {
            send.addEventListener("click", () => this.send());
        }
    }

    // A line on the page, never window.alert. The alert is a system chrome box
    // in the browser's own font that cannot be translated, cannot be styled,
    // and blocks the tab until it is dismissed - on a boutique screen it reads
    // as an error the site has suffered rather than an answer it is giving.
    notice(text) {
        let box = document.getElementById("modryn_roster_notice");
        if (!box) {
            box = document.createElement("div");
            box.id = "modryn_roster_notice";
            box.className = "modryn_roster_notice";
            box.setAttribute("role", "alert");
            this.el.parentNode.insertBefore(box, this.el);
        }
        box.textContent = text;
        box.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }

    async send() {
        const note = document.getElementById("modryn_week_note");
        const result = await rpc("/roster/send", {
            week: this.week,
            note: note ? note.value : "",
        });
        // She typed a note, pressed Send, and the deadline passed while the page
        // sat open - reloading without a word would look exactly like a
        // successful save.
        if (result && result.error) {
            this.notice(result.message || _t("That week can no longer be changed."));
            window.setTimeout(() => window.location.reload(), 2500);
            return;
        }
        window.location.reload();
    }

    // A cell is addressed by the DAY and the PART OF DAY it stands for, never
    // by a slot id: the whole point of the grid is that she can offer Friday
    // evening before the boutique has invented a Friday evening shift.
    async onClick(ev) {
        const cell = ev.target.closest(".modryn_avail_cell");
        if (!cell || cell.disabled) {
            return;
        }
        // Paint first, then confirm. Twenty-one cells means she taps several in
        // a row, and a round trip before any feedback makes every one of them
        // feel broken. If the server disagrees, the class goes back below.
        const wasOn = cell.classList.contains("is_on");
        this.paint(cell, !wasOn);
        // is_saving is the press itself, and it is a SEPARATE state from is_on.
        // The gold settles in a fifth of a second; the tap has to answer in the
        // same instant her finger lands, or she presses the cell again thinking
        // the first one missed - which toggles the offer straight back off and
        // is precisely how "it does not work" happens on a working page.
        cell.classList.add("is_saving");

        const result = await rpc("/roster/available", {
            day: cell.dataset.day,
            shift_type: cell.dataset.type,
            week: this.week,
        });
        cell.classList.remove("is_saving");

        if (result && result.error) {
            this.paint(cell, wasOn);
            // A refused toggle - the week closed or was published while she was
            // looking at it - has to say so, not silently do nothing. `message`
            // is the translated sentence; `error` is the machine code, which is
            // no use to a person but is what the load test matches on.
            this.notice(result.message || _t("That week can no longer be changed."));
            // Only a refusal costs a reload, and only because the reason for it
            // (a closed window, a published week) changes the whole page. The
            // delay is so the sentence is read before the page replaces it.
            window.setTimeout(() => window.location.reload(), 2500);
            return;
        }
        // No reload on success. The old code threw away the grid the server had
        // just built and reloaded the page for every single tap.
        this.repaint(result);
    }

    paint(cell, on) {
        cell.classList.toggle("is_on", on);
        cell.setAttribute("aria-pressed", on ? "true" : "false");
        // The glyph carries the state as well as the colour, so the cell still
        // reads correctly to somebody who cannot tell the gold from the grey.
        // The glyph AND the hidden label, because paint() replaces the whole
        // cell: rendering only the icon dropped the words the server had put
        // there, and after one tap a screen reader read every cell as blank.
        cell.innerHTML = on
            ? '<i class="fa fa-check" aria-hidden="true"></i>'
            : "";
        const label = document.createElement("span");
        label.className = "visually-hidden";
        label.textContent = on ? _t("I can work this") : _t("Not available");
        cell.appendChild(label);
    }

    // The manager's cards are rendered from the same response, so her view of
    // who offered what stays true without a reload either.
    repaint(result) {
        if (!result || !Array.isArray(result.days)) {
            return;
        }
        const byKey = {};
        for (const day of result.days) {
            for (const c of day.cells) {
                byKey[`${c.day}|${c.shift_type}`] = c;
            }
        }
        for (const cell of this.el.querySelectorAll(".modryn_avail_cell")) {
            const c = byKey[`${cell.dataset.day}|${cell.dataset.type}`];
            if (c) {
                this.paint(cell, c.mine);
            }
        }
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
