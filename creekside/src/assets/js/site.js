(function () {
  // Mobile navigation
  var toggle = document.querySelector(".navtoggle");
  var nav = document.getElementById("nav");
  if (toggle && nav) {
    toggle.addEventListener("click", function () {
      var open = toggle.getAttribute("aria-expanded") === "true";
      toggle.setAttribute("aria-expanded", String(!open));
      nav.classList.toggle("is-open", !open);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && nav.classList.contains("is-open")) {
        toggle.setAttribute("aria-expanded", "false");
        nav.classList.remove("is-open");
        toggle.focus();
      }
    });
  }

  // Conversion tracking: every element with data-track="name:placement"
  // pushes a cta_click event to the dataLayer (picked up by GA4 / GTM).
  window.dataLayer = window.dataLayer || [];
  document.addEventListener("click", function (e) {
    var el = e.target.closest("[data-track]");
    if (!el) return;
    var parts = el.getAttribute("data-track").split(":");
    var payload = {
      event: "cta_click",
      cta_name: parts[0],
      cta_placement: parts[1] || "",
      cta_url: el.getAttribute("href") || "",
      page_path: location.pathname
    };
    window.dataLayer.push(payload);
    if (typeof window.gtag === "function") window.gtag("event", "cta_click", payload);
  });
  document.addEventListener("submit", function (e) {
    var name = e.target.getAttribute("data-track-form");
    if (name) window.dataLayer.push({ event: "form_submit", form_name: name, page_path: location.pathname });
  });

  // "Open now" badge from CMS hours
  var badge = document.querySelector("[data-open-badge]");
  if (badge) {
    try {
      var days = JSON.parse(badge.getAttribute("data-hours"));
      var now = new Date();
      var d = days[(now.getDay() + 6) % 7];
      var mins = now.getHours() * 60 + now.getMinutes();
      var t = function (s) { var p = s.split(":"); return +p[0] * 60 + +p[1]; };
      var open = mins >= t(d.open) && mins < t(d.close);
      badge.textContent = open ? "Open now · until " + d.label.split("–")[1].trim() : "Today: " + d.label;
      badge.classList.add(open ? "is-open" : "is-closed");
      badge.hidden = false;
    } catch (err) {}
  }

  // Gentle reveal on scroll
  if ("IntersectionObserver" in window && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add("is-in"); io.unobserve(en.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px" });
    document.querySelectorAll("[data-reveal]").forEach(function (el) { el.classList.add("reveal"); io.observe(el); });
  }
})();
