/* The staff home page's two buttons and its refresh.
 *
 * Plain browser JS, no OWL: the page is server-rendered and personal, and the
 * two actions it offers already exist as jsonrpc routes with their own
 * server-side permission checks (/atelier/advance is own-task-only for
 * non-managers, /tasks/done is staff-or-own). Guarded by an element only the
 * home template renders, because this file rides web.assets_frontend onto
 * every page.
 */
(function () {
    "use strict";

    async function call(url, params) {
        const response = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ jsonrpc: "2.0", method: "call", params: params }),
        });
        const data = await response.json();
        return (data && data.result) || {};
    }

    // A server code turned into a sentence she can act on. The words are
    // rendered by the template as hidden text - this file is plain script and
    // cannot import _t, and Odoo does not extract data-* attributes, so text
    // nodes are the only place the translator can reach.
    // An unrecognised code is shown as it came rather than swallowed: a mystery
    // word beats a button that does nothing.
    function fail(code) {
        const box = document.getElementById("modryn_home_error");
        if (!box) { return; }
        const said = document.querySelector(
            '#modryn_home_messages [data-code="' + code + '"]');
        box.textContent = said ? said.textContent.trim() : code;
        box.classList.remove("d-none");
    }

    function bind() {
        document.querySelectorAll("[data-task-advance]").forEach(function (btn) {
            btn.addEventListener("click", async function () {
                const result = await call("/atelier/advance", {
                    task_id: parseInt(btn.dataset.taskAdvance, 10),
                    target: btn.dataset.target,
                });
                if (result.error) { return fail(result.error); }
                window.location.reload();
            });
        });
        document.querySelectorAll("[data-task-done]").forEach(function (btn) {
            btn.addEventListener("click", async function () {
                const result = await call("/tasks/done", {
                    task_id: parseInt(btn.dataset.taskDone, 10),
                });
                if (result.error) { return fail(result.error); }
                window.location.reload();
            });
        });
    }

    function armRefresh() {
        // ponytail: a visibility-guarded 60s reload, not a bus subscription —
        // the page is short and personal; upgrade to a bus refetch on the
        // existing modryn_queue channel if staleness ever annoys anyone.
        setTimeout(function () {
            if (document.visibilityState === "visible") {
                window.location.reload();
            } else {
                armRefresh();
            }
        }, 60000);
    }

    function init() {
        if (!document.getElementById("modryn_home_error")) { return; }
        bind();
        armRefresh();
    }

    if (document.readyState !== "loading") {
        init();
    } else {
        document.addEventListener("DOMContentLoaded", init);
    }
})();
