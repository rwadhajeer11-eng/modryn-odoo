/* The heart: a bride marks the dresses she wants to come back to.
 *
 * IN THE BROWSER AND NOWHERE ELSE. A bridal site has no accounts - a customer
 * identifies herself by a code sent to her phone, and only when she books - so
 * a heart that needed an account would be a heart nobody could press. What she
 * likes stays on her phone: no row, no cookie sent to us, nothing to lose and
 * nothing to ask her permission for.
 *
 * The cost is honest and small: hearts do not follow her from her phone to her
 * laptop. For "these three, show my mother later" that is the right trade.
 *
 * Plain script in an IIFE guarded by elements only these pages render, the same
 * shape as modryn_ops/dresses.js, because this file rides web.assets_frontend
 * onto every page in the product.
 *
 * Sentences come from hidden text the template renders, never from string
 * literals here: this file cannot import _t, and Odoo does not extract
 * data-* attributes, so text nodes are the only place a translator can reach.
 */
(function () {
    "use strict";

    var KEY = "modryn_liked";

    function ready(fn) {
        if (document.readyState !== "loading") {
            fn();
        } else {
            document.addEventListener("DOMContentLoaded", fn);
        }
    }

    // Every read and write is wrapped: a private window, a browser set to
    // block site data, and an iframe preview all throw on localStorage rather
    // than returning empty, and a heart is not worth a broken page.
    function read() {
        try {
            var raw = window.localStorage.getItem(KEY);
            var ids = raw ? JSON.parse(raw) : [];
            return Array.isArray(ids) ? ids.map(String) : [];
        } catch (err) {
            return [];
        }
    }

    function write(ids) {
        try {
            window.localStorage.setItem(KEY, JSON.stringify(ids));
        } catch (err) {
            // Nothing to do and nothing to say: she pressed a heart and the
            // browser will not remember it. The page carries on.
        }
    }

    function say(root, code) {
        var el = root.querySelector('#modryn_liked_words [data-code="' + code + '"]');
        return el ? el.textContent.trim() : code;
    }

    function paint(button, on, root) {
        var icon = button.querySelector("i");
        if (icon) {
            icon.className = on ? "fa fa-heart" : "fa fa-heart-o";
        }
        button.classList.toggle("modryn_liked_on", on);
        button.setAttribute("aria-pressed", on ? "true" : "false");
        button.setAttribute("aria-label", say(root, on ? "remove" : "add"));
        button.setAttribute("title", button.getAttribute("aria-label"));
    }

    function bindHearts(root) {
        var buttons = root.querySelectorAll("[data-modryn-like]");
        if (!buttons.length) {
            return;
        }
        var liked = read();
        Array.prototype.forEach.call(buttons, function (button) {
            var id = String(button.dataset.modrynLike);
            button.hidden = false;
            paint(button, liked.indexOf(id) !== -1, root);

            button.addEventListener("click", function (ev) {
                // The heart sits inside the anchor that opens the dress on the
                // grid; without this, liking one navigates away from the page.
                ev.preventDefault();
                ev.stopPropagation();
                var now = read();
                var at = now.indexOf(id);
                if (at === -1) {
                    now.push(id);
                } else {
                    now.splice(at, 1);
                }
                write(now);
                // Every heart for this dress on the page, not only the one
                // pressed: the grid can show the same dress twice.
                Array.prototype.forEach.call(
                    root.querySelectorAll('[data-modryn-like="' + id + '"]'),
                    function (twin) { paint(twin, at === -1, root); }
                );
                refreshFilter(root);
            });
        });
    }

    // The "only the ones I liked" switch on the collection.
    function refreshFilter(root) {
        var toggle = root.querySelector("#modryn_liked_only");
        if (!toggle) {
            return;
        }
        var liked = read();
        var count = root.querySelector("#modryn_liked_count");
        if (count) {
            count.textContent = String(liked.length);
        }
        var on = toggle.checked && liked.length > 0;
        var empty = root.querySelector("#modryn_liked_empty");
        Array.prototype.forEach.call(root.querySelectorAll("[data-modryn-tile]"),
            function (tile) {
                tile.hidden = on && liked.indexOf(String(tile.dataset.modrynTile)) === -1;
            });
        if (empty) {
            empty.hidden = !(toggle.checked && liked.length === 0);
        }
    }

    function bindFilter(root) {
        var toggle = root.querySelector("#modryn_liked_only");
        if (!toggle) {
            return;
        }
        // Shown here: with this file gone, a switch that hides nothing would
        // just be a lie sitting on the page.
        var wrap = root.querySelector("#modryn_liked_switch");
        if (wrap) {
            wrap.hidden = false;
        }
        toggle.addEventListener("change", function () { refreshFilter(root); });
        refreshFilter(root);
    }

    ready(function () {
        var root = document.getElementById("wrap") || document.body;
        bindHearts(root);
        bindFilter(root);
    });
})();
