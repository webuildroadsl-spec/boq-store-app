/*
 * BOQ manual-entry grid (Section 4.2): a small dependency-free
 * spreadsheet-style table for BOQItem rows. It talks to
 * boq.views.bill_items_save, which validates every row through the
 * same BOQItemForm that used to back a Django formset — this file is
 * purely the UI layer, not where the business rules live.
 *
 * Supported:
 *   - Tab / Shift+Tab: native browser field order (left to right, top
 *     to bottom, matching the COLUMNS order below).
 *   - Arrow Up / Down: move focus one row up/down, same column.
 *   - Arrow Left / Right: move to the adjacent cell when the text
 *     cursor is already at that edge of the field (so normal in-cell
 *     cursor movement still works).
 *   - Enter: move down one row (adding a new blank row first if
 *     you're on the last one).
 *   - Pasting a multi-cell block (e.g. copied from Excel: tab-
 *     separated columns, newline-separated rows) into any text cell
 *     fills the following cells and rows from that point, adding rows
 *     as needed.
 *   - "Add row" appends a blank row; a row's "x" button removes it —
 *     immediately for a never-saved row, via a small delete request
 *     for one already saved.
 *
 * Not attempted: cell range selection/highlighting, multi-cell
 * clipboard copy (only paste), undo. The acceptance test this exists
 * for is about correct calculation, not full spreadsheet parity.
 */
(function () {
  "use strict";

  var dataEl = document.getElementById("grid-data");
  if (!dataEl) {
    return; // read-only view: no grid, nothing to do.
  }

  var config = JSON.parse(dataEl.textContent);
  var gridRoot = document.getElementById("boq-grid");
  var statusEl = document.getElementById("grid-status");
  var billTotalEl = document.getElementById("bill-total");
  var addRowBtn = document.getElementById("add-row-btn");
  var saveBtn = document.getElementById("save-grid-btn");

  // One editable field per column, in on-screen (and tab/paste) order.
  var COLUMNS = [
    "item_reference",
    "description",
    "item_type",
    "unit",
    "quantity",
    "rate",
    "section",
    "parent_item",
  ];
  // Amount is shown but computed, not editable; excluded from COLUMNS
  // and therefore from paste targets and arrow navigation.
  var PASTE_COLUMNS = [
    "item_reference",
    "description",
    "item_type",
    "unit",
    "quantity",
    "rate",
    "section",
  ];

  var rows = config.items.map(function (item) {
    return Object.assign({}, item);
  });
  var rowErrors = {}; // index -> {field: [messages]}

  function getCookie(name) {
    var match = document.cookie.match("(^|;)\\s*" + name + "\\s*=\\s*([^;]+)");
    return match ? decodeURIComponent(match.pop()) : "";
  }

  function computeAmountPreview(row) {
    if (row.item_type === config.heading_type) {
      return "";
    }
    if (config.lump_sum_types.indexOf(row.item_type) !== -1) {
      var r = parseFloat(row.rate);
      return isNaN(r) ? "" : r.toFixed(2);
    }
    var q = parseFloat(row.quantity);
    var rate = parseFloat(row.rate);
    if (!isNaN(q) && !isNaN(rate)) {
      // Preview only — the server is the source of truth for the
      // saved amount (it rounds with ROUND_HALF_UP).
      return (q * rate).toFixed(2);
    }
    return "";
  }

  function optionEl(value, label, selectedValue) {
    var opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    if (String(value) === String(selectedValue == null ? "" : selectedValue)) {
      opt.selected = true;
    }
    return opt;
  }

  function buildSelect(field, value, choices, colIndex, rowIndex) {
    var select = document.createElement("select");
    select.appendChild(optionEl("", "---------", value));
    choices.forEach(function (choice) {
      select.appendChild(optionEl(choice[0], choice[1], value));
    });
    decorateCell(select, field, colIndex, rowIndex);
    return select;
  }

  function buildTextInput(field, value, colIndex, rowIndex) {
    var input = document.createElement("input");
    input.type = "text";
    input.value = value == null ? "" : value;
    decorateCell(input, field, colIndex, rowIndex);
    return input;
  }

  function decorateCell(el, field, colIndex, rowIndex) {
    el.dataset.field = field;
    el.dataset.col = String(colIndex);
    el.dataset.row = String(rowIndex);
    el.addEventListener("input", onCellInput);
    el.addEventListener("change", onCellInput);
    el.addEventListener("keydown", onCellKeydown);
    el.addEventListener("paste", onCellPaste);
  }

  function unitChoices() {
    return config.units.map(function (u) {
      return [u.id, u.code + (u.name ? " — " + u.name : "")];
    });
  }

  function sectionChoices() {
    return config.sections.map(function (s) {
      return [s.id, s.code];
    });
  }

  function headingChoices(excludeIndex) {
    var out = [];
    rows.forEach(function (row, i) {
      if (i !== excludeIndex && row.item_type === config.heading_type && row.item_reference) {
        out.push([row.id != null ? row.id : "new:" + i, row.item_reference + " — " + row.description]);
      }
    });
    return out;
  }

  function buildRowCells(row, rowIndex) {
    var cells = [];
    COLUMNS.forEach(function (field, colIndex) {
      var td = document.createElement("td");
      var control;
      if (field === "item_type") {
        control = buildSelect(field, row.item_type, config.item_types, colIndex, rowIndex);
      } else if (field === "unit") {
        control = buildSelect(field, row.unit, unitChoices(), colIndex, rowIndex);
      } else if (field === "section") {
        control = buildSelect(field, row.section, sectionChoices(), colIndex, rowIndex);
      } else if (field === "parent_item") {
        control = buildSelect(field, row.parent_item, headingChoices(rowIndex), colIndex, rowIndex);
      } else {
        control = buildTextInput(field, row[field], colIndex, rowIndex);
      }
      td.appendChild(control);
      cells.push(td);
    });
    return cells;
  }

  function render() {
    gridRoot.innerHTML = "";
    var table = document.createElement("table");
    table.border = "1";
    table.cellPadding = "4";

    var thead = document.createElement("thead");
    var headRow = document.createElement("tr");
    ["Ref", "Description", "Type", "Unit", "Quantity", "Rate", "Section", "Parent (heading)", "Amount", ""].forEach(
      function (label) {
        var th = document.createElement("th");
        th.textContent = label;
        headRow.appendChild(th);
      }
    );
    thead.appendChild(headRow);
    table.appendChild(thead);

    var tbody = document.createElement("tbody");
    rows.forEach(function (row, i) {
      var tr = document.createElement("tr");
      tr.dataset.index = String(i);
      buildRowCells(row, i).forEach(function (td) {
        tr.appendChild(td);
      });

      var amountTd = document.createElement("td");
      amountTd.textContent = row.amount || computeAmountPreview(row);
      tr.appendChild(amountTd);

      var deleteTd = document.createElement("td");
      var deleteBtn = document.createElement("button");
      deleteBtn.type = "button";
      deleteBtn.textContent = "✕";
      deleteBtn.addEventListener("click", function () {
        deleteRow(i);
      });
      deleteTd.appendChild(deleteBtn);
      tr.appendChild(deleteTd);

      tbody.appendChild(tr);

      var errors = rowErrors[i];
      if (errors) {
        var errorTr = document.createElement("tr");
        var errorTd = document.createElement("td");
        errorTd.colSpan = COLUMNS.length + 2;
        errorTd.style.color = "red";
        var messages = [];
        Object.keys(errors).forEach(function (field) {
          errors[field].forEach(function (item) {
            messages.push((field !== "__all__" ? field + ": " : "") + (item.message || item));
          });
        });
        errorTd.textContent = messages.join(" — ");
        errorTr.appendChild(errorTd);
        tbody.appendChild(errorTr);
      }
    });
    table.appendChild(tbody);
    gridRoot.appendChild(table);
  }

  function onCellInput(event) {
    var el = event.target;
    var row = rows[Number(el.dataset.row)];
    var field = el.dataset.field;
    var value = el.value;
    if (field === "parent_item" && value.indexOf("new:") === 0) {
      // A heading that hasn't been saved yet — client-only reference,
      // resolved to a real id after the next successful save.
      row[field] = null;
      row._pendingParentRef = value;
    } else {
      row[field] = value === "" ? null : value;
    }
    if (["quantity", "rate", "item_type"].indexOf(field) !== -1) {
      var tr = el.closest("tr");
      var amountCell = tr.children[COLUMNS.length];
      amountCell.textContent = computeAmountPreview(row);
    }
  }

  function focusCell(rowIndex, colIndex) {
    var selector =
      'tr[data-index="' + rowIndex + '"] [data-col="' + colIndex + '"]';
    var el = gridRoot.querySelector(selector);
    if (el) {
      el.focus();
      if (typeof el.select === "function" && el.tagName === "INPUT") {
        el.select();
      }
    }
  }

  function onCellKeydown(event) {
    var el = event.target;
    var rowIndex = Number(el.dataset.row);
    var colIndex = Number(el.dataset.col);

    if (event.key === "ArrowDown") {
      event.preventDefault();
      focusCell(rowIndex + 1, colIndex);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      focusCell(rowIndex - 1, colIndex);
    } else if (event.key === "ArrowLeft") {
      if (el.tagName !== "INPUT" || el.selectionStart === 0) {
        event.preventDefault();
        focusCell(rowIndex, colIndex - 1);
      }
    } else if (event.key === "ArrowRight") {
      if (el.tagName !== "INPUT" || el.selectionStart === el.value.length) {
        event.preventDefault();
        focusCell(rowIndex, colIndex + 1);
      }
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (rowIndex === rows.length - 1) {
        addRow();
      }
      focusCell(rowIndex + 1, colIndex);
    }
  }

  function onCellPaste(event) {
    var text = (event.clipboardData || window.clipboardData).getData("text");
    if (!text || (text.indexOf("\t") === -1 && text.indexOf("\n") === -1)) {
      return; // a plain single-cell paste: let the browser handle it.
    }
    event.preventDefault();

    var el = event.target;
    var startRow = Number(el.dataset.row);
    var startCol = PASTE_COLUMNS.indexOf(el.dataset.field);
    if (startCol === -1) {
      startCol = 0;
    }

    var lines = text.replace(/\r/g, "").split("\n").filter(function (line, i, arr) {
      return !(i === arr.length - 1 && line === "");
    });

    lines.forEach(function (line, lineIndex) {
      var targetRow = startRow + lineIndex;
      while (targetRow >= rows.length) {
        rows.push(blankRow());
      }
      var cells = line.split("\t");
      cells.forEach(function (cellValue, cellIndex) {
        var field = PASTE_COLUMNS[startCol + cellIndex];
        if (!field) {
          return;
        }
        setPastedValue(rows[targetRow], field, cellValue.trim());
      });
    });

    render();
    focusCell(startRow + lines.length - 1, startCol);
  }

  function setPastedValue(row, field, value) {
    if (field === "item_type") {
      var typeMatch = config.item_types.find(function (choice) {
        return choice[0].toLowerCase() === value.toLowerCase() || choice[1].toLowerCase() === value.toLowerCase();
      });
      row.item_type = typeMatch ? typeMatch[0] : row.item_type;
    } else if (field === "unit") {
      var unitMatch = config.units.find(function (u) {
        return u.code.toLowerCase() === value.toLowerCase();
      });
      row.unit = unitMatch ? unitMatch.id : row.unit;
    } else if (field === "section") {
      var sectionMatch = config.sections.find(function (s) {
        return s.code.toLowerCase() === value.toLowerCase();
      });
      row.section = sectionMatch ? sectionMatch.id : row.section;
    } else {
      row[field] = value;
    }
  }

  function blankRow() {
    return {
      id: null,
      item_reference: "",
      description: "",
      item_type: "",
      unit: null,
      quantity: "",
      rate: "",
      amount: "",
      section: null,
      parent_item: null,
      sort_order: 0,
    };
  }

  function addRow() {
    rows.push(blankRow());
    render();
    focusCell(rows.length - 1, 0);
  }

  function deleteRow(index) {
    var row = rows[index];
    if (!row.id) {
      rows.splice(index, 1);
      delete rowErrors[index];
      render();
      return;
    }
    if (!window.confirm("Remove item " + (row.item_reference || "") + "?")) {
      return;
    }
    postToServer([{ id: row.id, delete: true }]).then(function (result) {
      if (result.ok) {
        rows.splice(index, 1);
        applyServerState(result);
      } else {
        setStatus("Could not delete that row.", true);
      }
    });
  }

  function setStatus(message, isError) {
    statusEl.textContent = message;
    statusEl.style.color = isError ? "red" : "green";
  }

  function applyServerState(result) {
    rows = result.items.map(function (item) {
      return Object.assign({}, item);
    });
    rowErrors = {};
    billTotalEl.textContent = result.bill_total;
    render();
  }

  function postToServer(payloadRows) {
    return fetch(config.save_url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCookie("csrftoken"),
      },
      body: JSON.stringify({ rows: payloadRows }),
    }).then(function (response) {
      return response.json().then(function (data) {
        return data;
      });
    });
  }

  function save() {
    setStatus("Saving…", false);
    var payload = rows.map(function (row) {
      return {
        id: row.id,
        item_reference: row.item_reference,
        description: row.description,
        item_type: row.item_type,
        unit: row.unit,
        quantity: row.quantity,
        rate: row.rate,
        section: row.section,
        parent_item: row.parent_item,
        sort_order: 0,
      };
    });
    // Auto-number sort_order by current on-screen order, so entry
    // order is what's remembered — not a field the user has to manage.
    payload.forEach(function (row, i) {
      row.sort_order = i;
    });

    postToServer(payload).then(function (result) {
      if (result.ok) {
        applyServerState(result);
        setStatus("Saved.", false);
      } else if (result.errors) {
        rowErrors = result.errors;
        render();
        setStatus("Some rows have errors — see below.", true);
      } else {
        setStatus(result.error || "Save failed.", true);
      }
    }, function () {
      setStatus("Save failed — check your connection.", true);
    });
  }

  addRowBtn.addEventListener("click", addRow);
  saveBtn.addEventListener("click", save);
  render();
})();
