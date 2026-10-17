(function() {
  var display = document.getElementById("countdown-display");
  var messageEl = document.getElementById("maintenance-message");
  if (!display) return;

  function update() {
    fetch("/slates/maintenance.json", { cache: "no-store" })
      .then(function(res) {
        if (!res.ok) throw new Error("No active maintenance window file");
        return res.json();
      })
      .then(function(data) {
        if (data.message && messageEl) {
          messageEl.textContent = data.message;
        }
        if (!data.until) {
          display.textContent = "Estimated resumption: shortly";
          return;
        }
        var target = new Date(data.until).getTime();
        var now = new Date().getTime();
        var diff = target - now;

        if (isNaN(target)) {
          display.textContent = "Estimated resumption: shortly";
          return;
        }

        if (diff <= 0) {
          display.textContent = "Services resuming now...";
          setTimeout(function() { window.location.reload(); }, 2000);
          return;
        }

        var hours = Math.floor(diff / (1000 * 60 * 60));
        var minutes = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
        var seconds = Math.floor((diff % (1000 * 60)) / 1000);

        var text = "Resuming in ";
        if (hours > 0) text += hours + "h ";
        text += (minutes < 10 ? "0" : "") + minutes + "m ";
        text += (seconds < 10 ? "0" : "") + seconds + "s";
        display.textContent = text;
      })
      .catch(function() {
        display.textContent = "Maintenance in progress";
      });
  }

  update();
  setInterval(update, 1000);
})();
