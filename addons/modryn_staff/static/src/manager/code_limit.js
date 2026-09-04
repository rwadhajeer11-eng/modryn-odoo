/* The discount-code form's "what comes off" answer.
 *
 * All this file does is swap the LABEL above the number box between "how many
 * percent" and "how many shekels". Both words are rendered by the server as
 * text nodes and one of them is hidden — never built here — because Odoo does
 * not extract strings from JavaScript, so a label written in this file would be
 * English forever on a product whose first language is Hebrew.
 *
 * With JavaScript off the form still works: the label reads "how many percent",
 * the dropdown still posts its answer, and the controller reads whichever of
 * the two the chosen kind needs.
 *
 * Plain script and no OWL, matching home.js next door, and guarded by an
 * element only this panel renders — web.assets_frontend puts this file on every
 * page in the product.
 */
(function () {
    "use strict";

    function apply(select) {
        const amount = select.value === "amount";
        const pct = document.querySelector(".modryn_code_pct");
        const ils = document.querySelector(".modryn_code_ils");
        const box = document.getElementById("code_percent");
        if (!pct || !ils || !box) { return; }
        pct.hidden = amount;
        ils.hidden = !amount;
        // A percentage cannot pass 100; a sum of money can be anything. The
        // server checks both again — this only stops the browser accepting a
        // number the server is about to refuse.
        if (amount) {
            box.removeAttribute("max");
        } else {
            box.setAttribute("max", "100");
        }
    }

    function init() {
        const select = document.getElementById("code_kind");
        if (!select) { return; }
        apply(select);
        select.addEventListener("change", function () { apply(select); });
    }

    if (document.readyState !== "loading") {
        init();
    } else {
        document.addEventListener("DOMContentLoaded", init);
    }
})();
