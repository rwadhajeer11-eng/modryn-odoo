/* The rentals box answers from the second letter typed.
 *
 * WHY THIS EXISTS AT ALL, when the same page already has a GET form that
 * works: whoever is asking is usually holding a phone with the bride on it,
 * and pressing Enter and waiting for a page is one step too many. The form
 * stays, and still works with this file switched off — the server renders the
 * first answer and Enter still submits it. This only replaces the rows.
 *
 * EVERY WORD IT PRINTS IS RENDERED BY THE SERVER, in a hidden block the panel
 * draws. Odoo does not extract strings from JavaScript, so a sentence built
 * here would be English forever on a product whose first language is Hebrew —
 * the trap home.js next door already records.
 *
 * Plain script and no OWL, matching the two files beside it, and guarded by an
 * element only this panel renders: web.assets_frontend puts this on every page
 * in the product.
 */
(function () {
    "use strict";

    // Two, the same number the server refuses below — a box that asks the
    // server about one letter asks it about every rental in the shop.
    var MIN = 2;
    // Long enough that typing a name is one request and not eight, short
    // enough that it still feels like the list is following the fingers.
    var PAUSE = 180;

    var timer = null;
    var inflight = 0;

    function words() {
        var out = {};
        var box = document.getElementById("modryn_rental_words");
        if (!box) { return out; }
        box.querySelectorAll("[data-word]").forEach(function (el) {
            out[el.dataset.word] = el.textContent.trim();
        });
        return out;
    }

    function money(value) {
        return "₪" + Number(value || 0).toLocaleString("en-US");
    }

    function line(term, value, ltr) {
        if (!value) { return ""; }
        var dt = document.createElement("dt");
        dt.textContent = term;
        var dd = document.createElement("dd");
        dd.textContent = value;
        if (ltr) { dd.setAttribute("dir", "ltr"); }
        return [dt, dd];
    }

    // Built with the DOM and never with innerHTML: a customer's name and the
    // note beside it are typed by people, and one apostrophe in a gown's name
    // should not be able to rewrite the page it is printed on.
    function card(row, w) {
        var art = document.createElement("article");
        art.className = "modryn_card modryn_rental" + (row.late ? " is_late" : "");

        var marks = document.createElement("div");
        marks.className = "modryn_card_marks";
        var name = document.createElement("span");
        name.className = "modryn_strong";
        name.textContent = row.name;
        marks.appendChild(name);

        var badge = document.createElement("span");
        if (row.late) {
            badge.className = "modryn_badge is_late";
            badge.textContent = row.days_late + " " + (w.late || "days late");
        } else if (!row.returned) {
            badge.className = "modryn_badge is_wait";
            badge.textContent = w.out || "";
        } else {
            badge.className = "modryn_badge is_free";
            badge.textContent = w.returned || "";
        }
        marks.appendChild(badge);
        if (row.kind) {
            var kind = document.createElement("span");
            kind.className = "modryn_badge is_muted";
            kind.textContent = row.kind;
            marks.appendChild(kind);
        }
        art.appendChild(marks);

        if (row.phone) {
            var phone = document.createElement("div");
            phone.className = "modryn_sub";
            phone.setAttribute("dir", "ltr");
            phone.textContent = row.phone;
            art.appendChild(phone);
        }
        var dress = document.createElement("div");
        dress.className = "modryn_strong";
        dress.textContent = row.dress;
        art.appendChild(dress);

        var dl = document.createElement("dl");
        dl.className = "modryn_person";
        [
            [w.worth, row.retail ? money(row.retail) : "", true],
            [w.paid, row.rental ? money(row.rental) : "", true],
            [w.taken, row.taken, true],
            [w.wedding, row.wedding, true],
            [w.returned, row.returned, true],
        ].forEach(function (spec) {
            var pair = line(spec[0], spec[1], spec[2]);
            if (pair) { dl.appendChild(pair[0]); dl.appendChild(pair[1]); }
        });
        art.appendChild(dl);

        if (row.note) {
            var note = document.createElement("div");
            note.className = "modryn_sub";
            note.textContent = row.note;
            art.appendChild(note);
        }
        return art;
    }

    function draw(rows, w) {
        var box = document.getElementById("modryn_rental_rows");
        if (!box) { return; }
        box.textContent = "";
        if (!rows.length) {
            var none = document.createElement("p");
            none.className = "modryn_empty";
            none.textContent = w.none || "";
            box.appendChild(none);
            return;
        }
        rows.forEach(function (row) { box.appendChild(card(row, w)); });
    }

    async function ask(query) {
        // Every answer carries the query it was asked with, and a late one is
        // dropped: typing quickly sends several, and without this the list can
        // settle on the answer to a prefix rather than to what is in the box.
        var mine = (inflight += 1);
        var response = await fetch("/manage/rentals/search", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                jsonrpc: "2.0", method: "call", params: { q: query },
            }),
        });
        var data = await response.json();
        if (mine !== inflight) { return; }
        var result = (data && data.result) || {};
        if (result.error) { return; }
        draw(result.rows || [], words());
    }

    function init() {
        var box = document.getElementById("modryn_rental_q");
        if (!box) { return; }
        box.addEventListener("input", function () {
            var query = box.value.trim();
            window.clearTimeout(timer);
            if (query.length < MIN) {
                // Below two letters the page goes back to what the SERVER
                // drew, rather than to an empty list: "everything still out"
                // is the right answer to an empty box, and blanking it would
                // read as "there are no rentals".
                if (!query.length) { window.location.href = "/manage/team-screen?view=rentals"; }
                return;
            }
            timer = window.setTimeout(function () { ask(query); }, PAUSE);
        });
    }

    if (document.readyState !== "loading") {
        init();
    } else {
        document.addEventListener("DOMContentLoaded", init);
    }
})();
