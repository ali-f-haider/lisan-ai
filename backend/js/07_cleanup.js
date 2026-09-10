// ===== No clean button: voice cleanup is automatic only =====
(function () {
    function dropClean() { var b = document.getElementById("cvClean"); if (b) b.remove(); }
    dropClean();
    new MutationObserver(dropClean).observe(document.body, { childList: true, subtree: true });
})();