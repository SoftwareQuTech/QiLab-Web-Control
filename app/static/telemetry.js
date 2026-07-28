/* Click-to-sort for the server-rendered telemetry tables.
 *
 * Progressive enhancement only: the table already arrives sorted usefully, so
 * with JS off it stays readable, just not re-sortable. Matches config.js's
 * plain-ES5 IIFE style -- there is no build step in this project.
 *
 * Column types are inferred from the DATA, not the header text, so a new
 * profiler column (Avg[ns], say) sorts numerically with no change here.
 */
(function () {
  "use strict";

  function cellText(row, index) {
    var cell = row.children[index];
    return cell ? (cell.textContent || "").trim() : "";
  }

  // A column is numeric only if every non-empty cell parses as a finite number.
  // Checked per column rather than per cell so one stray label cannot flip an
  // otherwise-numeric column into string ordering for every row.
  function isNumericColumn(rows, index) {
    var sawValue = false;
    for (var i = 0; i < rows.length; i++) {
      var text = cellText(rows[i], index);
      if (text === "") {
        continue;
      }
      if (!isFinite(Number(text))) {
        return false;
      }
      sawValue = true;
    }
    return sawValue;
  }

  function sortTable(table, index, ascending) {
    var tbody = table.tBodies[0];
    if (!tbody) {
      return;
    }
    var rows = Array.prototype.slice.call(tbody.rows);
    var numeric = isNumericColumn(rows, index);
    var direction = ascending ? 1 : -1;

    rows.sort(function (left, right) {
      var a = cellText(left, index);
      var b = cellText(right, index);
      if (numeric) {
        // Empty sorts last in both directions rather than pretending to be 0.
        if (a === "" && b === "") return 0;
        if (a === "") return 1;
        if (b === "") return -1;
        return (Number(a) - Number(b)) * direction;
      }
      return a.localeCompare(b) * direction;
    });

    for (var i = 0; i < rows.length; i++) {
      tbody.appendChild(rows[i]);
    }
  }

  function markSorted(headers, activeIndex, ascending) {
    for (var i = 0; i < headers.length; i++) {
      var header = headers[i];
      if (i === activeIndex) {
        header.setAttribute("aria-sort", ascending ? "ascending" : "descending");
        header.classList.add("sorted");
      } else {
        header.removeAttribute("aria-sort");
        header.classList.remove("sorted");
      }
    }
  }

  function enhance(table) {
    var headRow = table.tHead && table.tHead.rows[0];
    if (!headRow) {
      return;
    }
    var headers = Array.prototype.slice.call(headRow.cells);
    var state = { index: -1, ascending: true };

    headers.forEach(function (header, index) {
      header.classList.add("sortable");
      header.setAttribute("tabindex", "0");
      header.setAttribute("role", "button");

      function activate() {
        // Re-clicking a column flips it; a new column starts ascending.
        state.ascending = state.index === index ? !state.ascending : true;
        state.index = index;
        sortTable(table, index, state.ascending);
        markSorted(headers, index, state.ascending);
      }

      header.addEventListener("click", activate);
      header.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          activate();
        }
      });
    });
  }

  var tables = document.querySelectorAll("table[data-sortable]");
  for (var i = 0; i < tables.length; i++) {
    enhance(tables[i]);
  }
})();
