/* The discount-code form's "how long it lasts" answer.
 *
 * All three boxes are rendered by the server and this file only HIDES the two
 * that do not apply. That order matters: with JavaScript off, or before this
 * file runs, the manager sees every box and the form still works — the
 * controller reads whichever of them the chosen answer needs and ignores the
 * rest. Building the boxes here instead would have made a plain HTML form
 * depend on a script to be fillable at all.
 *
 * Plain script and no OWL, matching home.js next door, and guarded by an
 * element only this panel renders — web.assets_frontend puts this file on
 * every page in the product.
 */
(function () {
    "use strict";

    function apply(select) {
        const kind = select.value;
        const times = document.getElementById("code_times");
        const until = document.getElementById("code_until");
        if (!times || !until) { return; }
        // The COLUMN, not the input: hiding the box and leaving its label
        // floating above nothing is the version of this that looks broken.
        times.closest("[class*='col-']").hidden = kind !== "times";
        until.closest("[class*='col-']").hidden = kind !== "until";
    }

    function init() {
        const select = document.getElementById("code_limit");
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
