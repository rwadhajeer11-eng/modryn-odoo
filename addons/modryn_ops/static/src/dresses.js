/* The catalogue's two live pieces: a search that answers as she types, and a
 * stock change that says what it is about to do before it does it.
 *
 * Plain script in an IIFE, guarded by an element only this page renders, because
 * the file rides web.assets_frontend onto every page in the product. The same
 * shape as modryn_staff's home.js.
 *
 * Sentences come from hidden text the template renders, never from string
 * literals here: this file cannot import _t, and Odoo does not extract
 * data-* attributes, so text nodes are the only place a translator can reach.
 */
(function () {
    "use strict";

    function ready(fn) {
        if (document.readyState !== "loading") {
            fn();
        } else {
            document.addEventListener("DOMContentLoaded", fn);
        }
    }

    // One of the sentences the template rendered, by key, with %(n)s filled in.
    function say(page, code, count) {
        const el = page.querySelector('#modryn_dress_words [data-code="' + code + '"]');
        const text = el ? el.textContent.trim() : code;
        // {count}, not a printf placeholder: %(...)s in a template is Odoo's
        // own external-id reference syntax and is eaten at load time.
        return text.replace("{count}", count);
    }

    // ------------------------------------------------------------- searching
    //
    // Filtered in the browser and not on the server: a boutique's rail is tens
    // of dresses, they are all already on the page, and a round trip per
    // keystroke would make the first two letters feel slower than scrolling.
    function bindSearch(page) {
        const box = page.querySelector("#modryn_dress_search");
        const empty = page.querySelector("#modryn_dress_noresult");
        if (!box) {
            return;
        }
        const cards = Array.from(page.querySelectorAll("[data-dress]"));

        // Prefix, not "contains". She asked for the dresses that START with what
        // she has typed - typing 10 should offer 1042 and 1099, not every dress
        // with a 10 buried in the middle of its description.
        //
        // Per WORD as well as per field, so "כלה אמילי" is found by typing
        // "אמי": a name is several words and only the first of them starts the
        // string.
        const starts = (haystack, needle) =>
            haystack.split(/\s+/).some((word) => word.startsWith(needle));

        function apply() {
            const q = box.value.trim().toLowerCase();
            // Under two characters is not a search yet - it is the beginning of
            // one, and hiding the whole rail after a single letter reads as the
            // catalogue having emptied itself.
            const searching = q.length >= 2;
            let shown = 0;
            cards.forEach(function (card) {
                let hit = true;
                if (searching) {
                    const name = (card.dataset.name || "").toLowerCase();
                    const serial = (card.dataset.serial || "").toLowerCase();
                    const kind = (card.dataset.kind || "").toLowerCase();
                    // A kind matches the WHOLE kind, so typing a category brings
                    // back every dress in it rather than the one whose name
                    // happens to begin the same way.
                    hit = starts(name, q) || starts(serial, q) || starts(kind, q);
                }
                card.hidden = !hit;
                if (hit) {
                    shown += 1;
                }
            });
            if (empty) {
                empty.hidden = shown !== 0;
            }
        }

        box.addEventListener("input", apply);
        // Enter must not submit anything: the box is not a form, and a stray
        // Enter on a page full of forms is how a stock number gets saved by
        // accident.
        box.addEventListener("keydown", function (ev) {
            if (ev.key === "Enter") {
                ev.preventDefault();
            }
        });
        apply();
    }

    // -------------------------------------------------- changing how many
    //
    // Setting how many of a size are on the rail is a replace-set: the owner
    // counts the rail and types what she sees. So the button says what the
    // change actually IS - two more, or three fewer - before it happens, because
    // "save 5" and "add 5" are different sentences and only one of them is true.
    function bindStock(page) {
        page.querySelectorAll("form[data-confirm-stock]").forEach(function (form) {
            const ask = form.querySelector("[data-role='ask']");
            const yes = form.querySelector("[data-role='yes']");
            const no = form.querySelector("[data-role='no']");
            const boxEl = form.querySelector("[data-role='confirm']");
            const wordsEl = form.querySelector("[data-role='words']");
            const input = form.querySelector("input[name='stock']");
            if (!ask || !yes || !no || !boxEl || !input) {
                return;
            }

            // What the rail holds right now, straight from the server-rendered
            // value - never from a previous edit, so pressing Set twice cannot
            // compound its own arithmetic.
            const current = parseInt(input.dataset.current || input.value, 10) || 0;

            ask.addEventListener("click", function (ev) {
                ev.preventDefault();
                const wanted = parseInt(input.value, 10);
                if (isNaN(wanted) || wanted < 0) {
                    return;
                }
                const delta = wanted - current;
                if (wordsEl) {
                    wordsEl.textContent =
                        delta > 0 ? say(page, "add", delta)
                        : delta < 0 ? say(page, "remove", -delta)
                        : say(page, "same", 0);
                }
                // Nothing to confirm when nothing changed; the button simply
                // stops rather than asking her to approve a no-op.
                if (delta === 0) {
                    boxEl.hidden = false;
                    yes.hidden = true;
                    return;
                }
                yes.hidden = false;
                ask.hidden = true;
                boxEl.hidden = false;
            });

            // Editing the number closes the pending question. Otherwise the
            // sentence goes stale: it would still read "Take 1 off this size?"
            // over a box she has since changed to something else, and Confirm
            // would save the new number under the old sentence. An edit means
            // the question has to be asked again.
            input.addEventListener("input", function () {
                if (!boxEl.hidden) {
                    boxEl.hidden = true;
                    ask.hidden = false;
                    yes.hidden = false;
                }
            });

            no.addEventListener("click", function (ev) {
                ev.preventDefault();
                // Back to what the rail actually holds, not to whatever was in
                // the box a moment ago: cancel means "forget this edit".
                input.value = current;
                boxEl.hidden = true;
                ask.hidden = false;
            });

            // Yes is the form's own submit button, so the POST is an ordinary
            // form post with its CSRF token - no fetch, and no second code path
            // that could drift from the one the server already trusts.
            yes.addEventListener("click", function () {
                boxEl.hidden = true;
            });
        });
    }

    // One press instead of a select-and-drag on a phone. The button is hidden
    // in the markup and shown here, so with this file gone the box and the
    // Open link still work and there is nothing dead to press.
    //
    // execCommand FIRST and the clipboard API second, which is the opposite of
    // the usual advice and is right here: navigator.clipboard needs a secure
    // context, and a boutique on http://<shop>.localtest.me is not one - a
    // subdomain does not inherit localhost's exemption. The old call works on
    // both, so it is the one that gets tried first.
    function bindShopLink(page) {
        const box = page.querySelector("#modryn_shop_link");
        if (!box) {
            return;
        }
        const input = box.querySelector("[data-modryn-link]");
        const button = box.querySelector("[data-modryn-copy]");
        if (!input || !button) {
            return;
        }
        button.hidden = false;
        const label = button.textContent;

        button.addEventListener("click", function () {
            input.focus();
            input.select();
            // iOS ignores select() on a readonly input without this.
            input.setSelectionRange(0, input.value.length);

            let copied = false;
            try {
                copied = document.execCommand("copy");
            } catch (err) {
                copied = false;
            }
            if (!copied && navigator.clipboard) {
                navigator.clipboard.writeText(input.value).catch(function () {});
                copied = true;
            }
            if (copied) {
                // say() reads the word out of the hidden block, the only place
                // in this file a translator can reach: it cannot import _t and
                // Odoo does not extract data-* attributes.
                button.textContent = say(page, "copied", 0);
                setTimeout(function () {
                    button.textContent = label;
                }, 1500);
            }
        });
    }

    ready(function () {
        const page = document.getElementById("modryn_dresses_page");
        if (!page) {
            return;
        }
        bindSearch(page);
        bindStock(page);
        bindShopLink(page);
    });
})();
