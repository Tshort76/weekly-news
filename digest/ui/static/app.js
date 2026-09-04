// The only JavaScript in the app: keep the progress list live while a run goes.
//
// An EventSource rather than polling, and it resumes rather than restarts —
// the id of the last event seen goes back with the reconnect, so closing the
// laptop and opening it again picks up where the list left off instead of
// showing a run that appears to have started over.
(function () {
  var list = document.getElementById("progress");
  if (!list) { return; }

  var source = new EventSource("/progress");

  source.addEventListener("progress", function (message) {
    var event = JSON.parse(message.data);
    // The server may replay events the page already has after a reconnect.
    if (list.querySelector('[data-seq="' + event.seq + '"]')) { return; }
    var line = document.createElement("li");
    line.setAttribute("data-seq", event.seq);
    line.textContent = describe(event);
    list.appendChild(line);
    list.dataset.last = event.seq;
  });

  // The run is over: reload once so the page shows the finished edition rather
  // than a progress list nothing will add to again.
  source.addEventListener("done", function () {
    source.close();
    window.location.reload();
  });

  source.onerror = function () {
    // The browser retries on its own, carrying Last-Event-ID. Nothing to do.
  };

  function describe(event) {
    var d = event.detail || {};
    if (event.stage === "entry") {
      return "writing " + d.n + " of " + d.of + ": " + (d.title || "");
    }
    var parts = Object.keys(d).map(function (key) { return key + " " + d[key]; });
    return event.stage + (parts.length ? " — " + parts.join(", ") : "");
  }
})();
