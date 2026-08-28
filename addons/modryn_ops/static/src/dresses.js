/* The catalogue's one destructive-ish action, made deliberate.
 *
 * Setting how many of a size are on the rail is a replace-set, not a nudge: the
 * owner counts the rail and types what she sees, and a mistyped 0 quietly puts a
 * dress out of stock on the shop. So the Set button now asks first.
 *
 * The confirmation is drawn INTO THE ROW, never window.confirm(). The browser's
 * own box is system chrome in a font this boutique does not use, it cannot be
 * translated, and it blocks the tab - on a screen that is meant to feel like a
 * luxury house it reads as an error the site has suffered rather than a question
 * it is asking.
 *
 * Plain script in an IIFE, guarded by an element only this page renders, because
 * the file rides web.assets_frontend onto every page in the product. The same
 * shape as modryn_staff's home.js.
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

    ready(function () {
        const page = document.getElementById("modryn_dresses_page");
        if (!page) {
            return;
        }

        page.querySelectorAll("form[data-confirm-stock]").forEach(function (form) {
            const ask = form.querySelector("[data-role='ask']");
            const yes = form.querySelector("[data-role='yes']");
            const no = form.querySelector("[data-role='no']");
            const box = form.querySelector("[data-role='confirm']");
            if (!ask || !yes || !no || !box) {
                return;
            }

            // The typed number at the moment she asked, so Cancel puts back what
            // was there rather than leaving her edit half-applied.
            const input = form.querySelector("input[name='stock']");
            let before = input ? input.value : null;

            ask.addEventListener("click", function (ev) {
                ev.preventDefault();
                before = input ? input.value : before;
                ask.classList.add("d-none");
                box.classList.remove("d-none");
            });

            no.addEventListener("click", function (ev) {
                ev.preventDefault();
                if (input && before !== null) {
                    input.value = before;
                }
                box.classList.add("d-none");
                ask.classList.remove("d-none");
            });

            // Yes is the form's real submit button, so the POST is an ordinary
            // form post with its CSRF token - no fetch, no second code path that
            // could drift from the one the server already trusts.
            yes.addEventListener("click", function () {
                box.classList.add("d-none");
            });
        });
    });
})();
