(function () {
  "use strict";

  var finished = null;
  var reasons = new Set();
  var ratings = { overall: 0, quality: 0, speed: 0, resource_efficiency: 0 };

  var btnYes = document.getElementById("btn-yes");
  var btnNo = document.getElementById("btn-no");
  var reasonsBlock = document.getElementById("reasons-block");
  var ratingsBlock = document.getElementById("ratings-block");
  var btnSubmit = document.getElementById("btn-submit");
  var btnSkip = document.getElementById("btn-skip");
  var confirmation = document.getElementById("confirmation");
  var ratingsHint = document.getElementById("ratings-hint");

  function setBinary(value) {
    finished = value;
    if (value === true) {
      reasons.clear();
      reasonsBlock.querySelectorAll(".chip").forEach(function (chip) {
        chip.setAttribute("aria-pressed", "false");
      });
    }
    btnYes.setAttribute("aria-pressed", value === true ? "true" : "false");
    btnNo.setAttribute("aria-pressed", value === false ? "true" : "false");
    reasonsBlock.hidden = value !== false;
    ratingsBlock.hidden = false;
    confirmation.hidden = true;
    updateSubmit();
    if (value === false) {
      var firstChip = reasonsBlock.querySelector(".chip");
      if (firstChip) firstChip.focus();
    }
  }

  function paintStars(row) {
    var key = row.getAttribute("data-rating");
    var value = ratings[key];
    row.querySelectorAll(".stars button").forEach(function (btn) {
      var v = Number(btn.getAttribute("data-value"));
      btn.classList.toggle("lit", v <= value);
      btn.setAttribute("aria-pressed", v === value ? "true" : "false");
    });
  }

  function allRated() {
    return ratings.overall > 0 && ratings.quality > 0 && ratings.speed > 0 &&
      ratings.resource_efficiency > 0;
  }

  function updateSubmit() {
    var ready = finished !== null && allRated();
    btnSubmit.disabled = !ready;
    if (finished === null) {
      ratingsHint.textContent = "First answer Yes or No above, then rate each row.";
    } else if (!allRated()) {
      ratingsHint.textContent = "Select a star rating for each row to enable Submit.";
    } else {
      ratingsHint.textContent = "All ratings set. Submit or Skip.";
    }
  }

  btnYes.addEventListener("click", function () { setBinary(true); });
  btnNo.addEventListener("click", function () { setBinary(false); });

  reasonsBlock.querySelectorAll(".chip").forEach(function (chip) {
    chip.addEventListener("click", function () {
      var key = chip.getAttribute("data-reason");
      if (reasons.has(key)) {
        reasons.delete(key);
        chip.setAttribute("aria-pressed", "false");
      } else {
        reasons.add(key);
        chip.setAttribute("aria-pressed", "true");
      }
    });
  });

  document.querySelectorAll(".rating-row").forEach(function (row) {
    var key = row.getAttribute("data-rating");
    row.querySelectorAll(".stars button").forEach(function (btn) {
      btn.addEventListener("click", function () {
        ratings[key] = Number(btn.getAttribute("data-value"));
        paintStars(row);
        updateSubmit();
      });
    });
  });

  function showConfirmation(text) {
    confirmation.textContent = text;
    confirmation.hidden = false;
  }

  async function sendToLocalMcp(payload) {
    if (window.location.protocol === "file:") {
      showConfirmation("Preview only. Opened directly from disk, so nothing was saved. The installed local rating tool saves reviews.");
      return;
    }
    btnSubmit.disabled = true;
    btnSubmit.textContent = "Saving…";
    try {
      var response = await fetch("api/submit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      var result = await response.json();
      if (!response.ok) throw new Error(result.error || "The local rating window could not send this review.");
      if (result.status === "skipped") {
        showConfirmation("Skipped. No rating was saved.");
      } else {
        showConfirmation("Thanks. Your rating is saved on this computer.");
      }
      btnSubmit.hidden = true;
      btnSkip.hidden = true;
    } catch (error) {
      showConfirmation(error.message + " You can retry or Skip.");
      btnSubmit.disabled = false;
      btnSubmit.textContent = "Submit";
    }
  }

  btnSubmit.addEventListener("click", function () {
    if (btnSubmit.disabled) return;
    sendToLocalMcp({
      completion: finished ? "yes" : "no",
      gap_reasons: Array.from(reasons),
      ratings: ratings
    });
  });

  btnSkip.addEventListener("click", function () {
    sendToLocalMcp({ skip: true });
  });

  function readable(value) {
    if (value === undefined || value === null || value === "" || value === "unknown") return "Unavailable";
    if (typeof value === "object") {
      var entries = Object.entries(value);
      if (!entries.length) return "None observed";
      return entries.map(function (entry) { return entry[0] + " (" + entry[1] + ")"; }).join(", ");
    }
    return String(value);
  }

  function updateSummary(data) {
    var badge = document.getElementById("sample-badge");
    badge.hidden = true;
    document.getElementById("summary-heading").textContent = "This run";
    document.getElementById("summary-task").textContent = readable(data.task);
    document.getElementById("summary-result").textContent = readable(data.result);
    document.getElementById("summary-action").textContent = readable(data.action);
    var context = data.context || {};
    var resources = data.resources || {};
    document.getElementById("summary-model").textContent = readable(context.model);
    document.getElementById("summary-tools").textContent = readable(context.connections);
    document.getElementById("summary-context").textContent =
      "Runtime: " + readable(context.runtime) +
      " · Agent: " + readable(context.agent) +
      " · Model: " + readable(context.model) +
      " · Tools: " + readable(context.connections) +
      " · Workflow: " + readable(context.loops) +
      " · Roles: " + readable(context.roles);
    document.getElementById("summary-usage").textContent =
      "Tokens " + readable(resources.tokens) +
      " · " + readable(resources.tool_calls) + " tool calls";
    document.getElementById("summary-resources").textContent =
      "Time " + readable(resources.elapsed_time) +
      " · Tokens " + readable(resources.tokens) +
      " · Cost " + readable(resources.cost) +
      " · Tool calls " + readable(resources.tool_calls);
  }

  if (window.location.protocol !== "file:") {
    fetch("api/review", { cache: "no-store" })
      .then(function (response) {
        if (!response.ok) throw new Error("Run summary unavailable");
        return response.json();
      })
      .then(updateSummary)
      .catch(function () {
        showConfirmation("Run summary could not be loaded. You can still rate this run, but check the local connection first.");
      });
  }

  updateSubmit();
})();
