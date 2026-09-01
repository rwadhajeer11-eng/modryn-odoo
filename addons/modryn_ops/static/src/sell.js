import { Interaction } from "@web/public/interaction";
import { registry } from "@web/core/registry";

/**
 * The till: find a thing, put it on the list, price it, total it.
 *
 * SEARCHES FOUR WAYS, because a counter is four situations. She knows the gown
 * by name; she is holding the tag and reads the serial; the bride asked for
 * "something in lace" and she wants the kind; or the scanner is in her hand.
 * All four run through one box, so there is nothing to choose between first.
 *
 * THE SCANNER IS THE REASON FOR THE ENTER BRANCH. A barcode reader types the
 * digits faster than a person can and then presses Enter. Everywhere else in
 * this product Enter inside a search box is swallowed — pressing it must not
 * submit a half-filled form — but here, if what was typed is exactly one item's
 * barcode, Enter means "that one", and the row goes straight on the list. That
 * is the difference between a scanner that works and a scanner that fills a box
 * with digits somebody then has to click past.
 *
 * WORD-prefix matching from two characters, capped at twelve: the same rule the
 * workshop's picker and the floor's finish picker use. A saleswoman learns it
 * once. Mirrored rather than shared, as those two already are of each other —
 * one lives in an OWL component with its own state, one on a page with no OWL
 * at all, and this one owns a basket neither of them has.
 *
 * An Interaction and not a DOMContentLoaded listener: this bundle is deferred,
 * that event has already fired by the time it runs, and the page would render
 * with a search box that does nothing. That has cost this project twice.
 */
export class SellScreen extends Interaction {
    static selector = ".modryn_sell";

    setup() {
        this.query = this.el.querySelector(".modryn_sell_query");
        this.hits = this.el.querySelector(".modryn_sell_results");
        this.body = this.el.querySelector(".modryn_sell_body");
        this.totalCell = this.el.querySelector(".modryn_sell_total");
        this.empty = this.el.querySelector(".modryn_sell_none");
        this.freeLabel = this.el.querySelector(".modryn_free_label");
        this.freePrice = this.el.querySelector(".modryn_free_price");
        this.freeAdd = this.el.querySelector(".modryn_free_add");
        this.rows = [...this.el.querySelectorAll(".modryn_sell_row")].map((r) => ({
            id: r.dataset.variant,
            label: r.dataset.label || "",
            name: r.dataset.name || "",
            serial: r.dataset.serial || "",
            kind: r.dataset.kind || "",
            barcode: r.dataset.barcode || "",
            price: r.dataset.price || "0",
        }));
    }

    start() {
        this.query.addEventListener("input", () => this.search());
        this.query.addEventListener("keydown", (ev) => {
            if (ev.key !== "Enter") {
                return;
            }
            // Never submit from the search box.
            ev.preventDefault();
            const typed = this.query.value.trim();
            // The scanner's path: an exact barcode is not a search, it is a
            // choice already made.
            const scanned = this.rows.find((r) => r.barcode && r.barcode === typed);
            if (scanned) {
                this.add(scanned.id, scanned.label, scanned.price);
                return;
            }
            // Otherwise Enter takes the only match, when there is exactly one.
            const found = this.matches(typed);
            if (found.length === 1) {
                this.add(found[0].id, found[0].label, found[0].price);
            }
        });
        this.hits.addEventListener("click", (ev) => {
            const button = ev.target.closest("[data-variant]");
            if (button) {
                this.add(button.dataset.variant, button.dataset.label, button.dataset.price);
            }
        });
        this.freeAdd.addEventListener("click", () => {
            const label = this.freeLabel.value.trim();
            if (!label) {
                this.freeLabel.focus();
                return;
            }
            this.add("", label, this.freePrice.value || "0");
            this.freeLabel.value = "";
            this.freePrice.value = "";
        });
        this.body.addEventListener("click", (ev) => {
            const drop = ev.target.closest(".modryn_line_drop");
            if (drop) {
                drop.closest("tr").remove();
                this.retotal();
            }
        });
        // Any price edited by hand re-totals: the ticket price is a starting
        // point, and the shop discounts a single item without touching the
        // whole-sale discount below.
        this.body.addEventListener("input", (ev) => {
            if (ev.target.matches(".modryn_line_price")) {
                this.retotal();
            }
        });
        this.retotal();
    }

    matches(query) {
        const q = String(query || "").trim().toLowerCase();
        if (q.length < 2) {
            return [];
        }
        const startsAWord = (hay) =>
            String(hay || "")
                .toLowerCase()
                .split(/\s+/)
                .some((word) => word.startsWith(q));
        return this.rows
            .filter(
                (r) =>
                    startsAWord(r.name) ||
                    startsAWord(r.serial) ||
                    startsAWord(r.kind) ||
                    String(r.barcode).startsWith(q)
            )
            .slice(0, 12);
    }

    search() {
        this.hits.replaceChildren();
        for (const row of this.matches(this.query.value)) {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "btn btn-sm btn-outline-dark modryn_sell_hit";
            button.dataset.variant = row.id;
            button.dataset.label = row.label;
            button.dataset.price = row.price;
            button.textContent = row.label;
            this.hits.appendChild(button);
        }
    }

    add(variantId, label, price) {
        const tr = document.createElement("tr");

        const nameCell = document.createElement("td");
        nameCell.textContent = label;
        // The three parallel inputs the controller zips back together. The
        // label is posted as well as the id, because it is what the receipt
        // SAID: a gown renamed next season must not rewrite last year's sale.
        const hiddenVariant = document.createElement("input");
        hiddenVariant.type = "hidden";
        hiddenVariant.name = "line_variant";
        hiddenVariant.value = variantId || "";
        const hiddenLabel = document.createElement("input");
        hiddenLabel.type = "hidden";
        hiddenLabel.name = "line_label";
        hiddenLabel.value = label;
        nameCell.append(hiddenVariant, hiddenLabel);

        const priceCell = document.createElement("td");
        const priceInput = document.createElement("input");
        priceInput.type = "number";
        priceInput.name = "line_price";
        priceInput.className = "form-control form-control-sm modryn_line_price";
        priceInput.min = "0";
        priceInput.step = "1";
        priceInput.dir = "ltr";
        priceInput.value = String(parseFloat(price) || 0);
        priceCell.appendChild(priceInput);

        const dropCell = document.createElement("td");
        const drop = document.createElement("button");
        drop.type = "button";
        drop.className = "btn btn-sm btn-outline-dark modryn_line_drop";
        drop.textContent = "×";
        drop.setAttribute("aria-label", this.el.dataset.dropLabel || "Remove");
        dropCell.appendChild(drop);

        tr.append(nameCell, priceCell, dropCell);
        this.body.appendChild(tr);

        this.query.value = "";
        this.hits.replaceChildren();
        this.retotal();
    }

    retotal() {
        let total = 0;
        for (const input of this.body.querySelectorAll(".modryn_line_price")) {
            total += parseFloat(input.value) || 0;
        }
        this.totalCell.textContent = total.toLocaleString();
        const any = this.body.children.length > 0;
        // hidden, not style.display: the host page's reset makes [hidden] win.
        this.empty.hidden = any;
        this.el.querySelector(".modryn_sell_lines").hidden = !any;
    }
}

registry.category("public.interactions").add("modryn_ops.sell_screen", SellScreen);
