/* Schema-driven config editor for qilab_web_control.
 *
 * Reads qilab's JSON Schema and the current config document (injected as
 * <script type="application/json"> blobs), renders a structured form, and
 * submits the edited document as JSON to /config/save. Adding a new noise
 * model / field / node type to the schema makes it appear here automatically.
 */
(function () {
  "use strict";

  var schemaEl = document.getElementById("qilab-schema");
  var docEl = document.getElementById("qilab-document");
  var body = document.getElementById("config-body");
  var form = document.getElementById("config-form");
  var statusEl = document.getElementById("config-save-status");
  if (!schemaEl || !body || !form) return;

  var schema, sourceDoc;
  try {
    schema = JSON.parse(schemaEl.textContent || "null");
    sourceDoc = JSON.parse((docEl && docEl.textContent) || "null") || {};
  } catch (err) {
    body.textContent = "Failed to parse schema/document: " + err;
    return;
  }
  if (!schema) {
    body.textContent = "Schema unavailable.";
    return;
  }

  var defs = schema.$defs || {};
  var nodeProps = (defs.node && defs.node.properties) || {};
  var NODE_TYPES = (nodeProps.node_type && nodeProps.node_type.enum) || ["GENERIC"];
  var DELAY_KEYS = Object.keys(((defs.operation_delays || {}).properties) || {});

  function branches(groupName) {
    var group = defs[groupName] || {};
    return (group.oneOf || []).map(function (branch) {
      var props = branch.properties || {};
      var fields = Object.keys(props)
        .filter(function (key) { return key !== "type"; })
        .map(function (key) {
          var prop = props[key] || {};
          return {
            name: key,
            integer: prop.type === "integer",
            minimum: prop.minimum,
            exclusiveMinimum: prop.exclusiveMinimum,
            unit: prop.unit || "",
          };
        });
      return { type: (props.type || {}).const, fields: fields };
    });
  }

  var QUBIT_MODELS = branches("qubit_noise_model");
  var ENT_MODELS = branches("entanglement_noise_model");

  function modelByType(models, type) {
    return (
      models.find(function (m) { return m.type === type; }) ||
      models[0] ||
      { type: type, fields: [] }
    );
  }

  function ensureType(models, type) {
    return models.some(function (m) { return m.type === type; })
      ? type
      : (models[0] && models[0].type);
  }

  // ---- state init (mirrors output shape, scalar values kept as strings) ----
  function fieldValues(obj) {
    var values = {};
    if (obj && typeof obj === "object") {
      Object.keys(obj).forEach(function (key) {
        if (key !== "type") values[key] = String(obj[key]);
      });
    }
    return values;
  }

  function initEnt() {
    var obj = sourceDoc.entanglement_noise_model;
    return {
      enabled: !!obj,
      type: ensureType(ENT_MODELS, (obj && obj.type)),
      values: fieldValues(obj),
    };
  }

  function initArray(arr) {
    arr = arr || {};
    var nm = arr.noise_model || {};
    return {
      num_qubits: arr.num_qubits != null ? String(arr.num_qubits) : "",
      type: ensureType(QUBIT_MODELS, nm.type),
      values: fieldValues(nm),
    };
  }

  function initDelays(node) {
    var delays = node.quantum_operations_delay_ns;
    var values = {};
    DELAY_KEYS.forEach(function (key) {
      values[key] = delays && delays[key] != null ? String(delays[key]) : "";
    });
    return { enabled: !!delays, values: values };
  }

  function initNode(node) {
    node = node || {};
    var arrays =
      Array.isArray(node.qubit_arrays) && node.qubit_arrays.length
        ? node.qubit_arrays.map(initArray)
        : [initArray({})];
    return {
      name: node.name != null ? String(node.name) : "",
      node_type: NODE_TYPES.indexOf(node.node_type) >= 0 ? node.node_type : "GENERIC",
      delays: initDelays(node),
      arrays: arrays,
    };
  }

  var state = {
    ent: initEnt(),
    nodes:
      Array.isArray(sourceDoc.nodes) && sourceDoc.nodes.length
        ? sourceDoc.nodes.map(initNode)
        : [initNode({})],
  };

  // ---- tiny DOM helper ----
  function h(tag, attrs, children) {
    var node = document.createElement(tag);
    attrs = attrs || {};
    Object.keys(attrs).forEach(function (key) {
      var value = attrs[key];
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.slice(0, 2) === "on") node[key] = value;
      else if (value !== undefined && value !== null && value !== false) {
        node.setAttribute(key, value);
      }
    });
    (children || []).forEach(function (child) {
      if (child == null) return;
      node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    });
    return node;
  }

  function numberInput(value, field, oninput) {
    var attrs = {
      type: "number",
      class: "field-input",
      value: value != null ? value : "",
      step: field.integer ? "1" : "any",
      required: true,
      oninput: oninput,
    };
    if (field.minimum !== undefined) {
      attrs.min = field.minimum;
    } else if (field.exclusiveMinimum !== undefined) {
      attrs.min = field.integer ? field.exclusiveMinimum + 1 : field.exclusiveMinimum;
    }
    return h("input", attrs);
  }

  function typeSelect(models, holder) {
    return h(
      "select",
      {
        class: "field-input",
        onchange: function (event) { holder.type = event.target.value; render(); },
      },
      models.map(function (model) {
        return h("option", { value: model.type, selected: model.type === holder.type }, [model.type]);
      })
    );
  }

  function modelFields(models, holder) {
    var wrap = h("div", { class: "model-fields" });
    modelByType(models, holder.type).fields.forEach(function (field) {
      var label = field.name + (field.unit ? " (" + field.unit + ")" : "");
      var input = numberInput(holder.values[field.name], field, function (event) {
        holder.values[field.name] = event.target.value;
      });
      wrap.appendChild(h("label", { class: "field" }, [h("span", { text: label }), input]));
    });
    return wrap;
  }

  // ---- renderers ----
  function renderEnt() {
    var section = h("div", { class: "config-section" }, [
      h("div", { class: "label", text: "Entanglement noise model" }),
      h("label", { class: "field-inline" }, [
        h("input", {
          type: "checkbox",
          checked: state.ent.enabled,
          onchange: function (event) { state.ent.enabled = event.target.checked; render(); },
        }),
        h("span", { text: "Set entanglement noise model (defaults to BASIC t2_value 1000 if unset)" }),
      ]),
    ]);
    if (state.ent.enabled) {
      section.appendChild(
        h("label", { class: "field" }, [h("span", { text: "type" }), typeSelect(ENT_MODELS, state.ent)])
      );
      section.appendChild(modelFields(ENT_MODELS, state.ent));
    }
    return section;
  }

  function renderArray(node, arr, idx) {
    return h("div", { class: "array-block" }, [
      h("div", { class: "status-row" }, [
        h("span", { class: "label", text: "Qubit array " + (idx + 1) }),
        h(
          "button",
          {
            type: "button",
            class: "btn btn-remove",
            disabled: node.arrays.length <= 1,
            onclick: function () { node.arrays.splice(idx, 1); render(); },
          },
          ["Remove array"]
        ),
      ]),
      h("label", { class: "field" }, [
        h("span", { text: "num_qubits" }),
        numberInput(arr.num_qubits, { integer: true, exclusiveMinimum: 0 }, function (event) {
          arr.num_qubits = event.target.value;
        }),
      ]),
      h("label", { class: "field" }, [
        h("span", { text: "noise_model.type" }),
        typeSelect(QUBIT_MODELS, arr),
      ]),
      modelFields(QUBIT_MODELS, arr),
    ]);
  }

  function renderDelays(node) {
    var wrap = h("div", { class: "config-subsection" }, [
      h("label", { class: "field-inline" }, [
        h("input", {
          type: "checkbox",
          checked: node.delays.enabled,
          onchange: function (event) { node.delays.enabled = event.target.checked; render(); },
        }),
        h("span", { text: "Set operation delays (ns)" }),
      ]),
    ]);
    if (node.delays.enabled) {
      var grid = h("div", { class: "delay-grid" });
      DELAY_KEYS.forEach(function (key) {
        grid.appendChild(
          h("label", { class: "field" }, [
            h("span", { text: key }),
            numberInput(node.delays.values[key], { integer: true, minimum: 0 }, function (event) {
              node.delays.values[key] = event.target.value;
            }),
          ])
        );
      });
      wrap.appendChild(grid);
    }
    return wrap;
  }

  function renderNode(node, idx) {
    var arraysWrap = h("div", { class: "arrays-wrap" }, [
      h("div", { class: "label", text: "Qubit arrays" }),
    ]);
    node.arrays.forEach(function (arr, i) { arraysWrap.appendChild(renderArray(node, arr, i)); });
    arraysWrap.appendChild(
      h(
        "button",
        {
          type: "button",
          class: "btn btn-add",
          onclick: function () { node.arrays.push(initArray({})); render(); },
        },
        ["+ Add qubit array"]
      )
    );

    return h("div", { class: "node-block" }, [
      h("div", { class: "status-row" }, [
        h("span", { class: "label", text: "Node " + (idx + 1) }),
        h(
          "button",
          {
            type: "button",
            class: "btn btn-remove",
            disabled: state.nodes.length <= 1,
            onclick: function () { state.nodes.splice(idx, 1); render(); },
          },
          ["Remove node"]
        ),
      ]),
      h("label", { class: "field" }, [
        h("span", { text: "name" }),
        h("input", {
          type: "text",
          class: "field-input",
          value: node.name,
          oninput: function (event) { node.name = event.target.value; },
        }),
      ]),
      h("label", { class: "field" }, [
        h("span", { text: "node_type" }),
        h(
          "select",
          { class: "field-input", onchange: function (event) { node.node_type = event.target.value; } },
          NODE_TYPES.map(function (type) {
            return h("option", { value: type, selected: type === node.node_type }, [type]);
          })
        ),
      ]),
      renderDelays(node),
      arraysWrap,
    ]);
  }

  function render() {
    body.innerHTML = "";
    body.appendChild(renderEnt());
    var nodesWrap = h("div", { class: "nodes-wrap" }, [h("div", { class: "label", text: "Nodes" })]);
    state.nodes.forEach(function (node, i) { nodesWrap.appendChild(renderNode(node, i)); });
    nodesWrap.appendChild(
      h(
        "button",
        {
          type: "button",
          class: "btn btn-add",
          onclick: function () { state.nodes.push(initNode({})); render(); },
        },
        ["+ Add node"]
      )
    );
    body.appendChild(nodesWrap);
  }

  // ---- serialize state -> JSON document ----
  function toNumber(raw, integer) {
    if (raw === undefined || raw === null || String(raw).trim() === "") return undefined;
    var value = integer ? parseInt(raw, 10) : Number(raw);
    return Number.isFinite(value) ? value : raw; // keep raw string so the server flags it
  }

  function delayValue(raw) {
    if (raw === undefined || String(raw).trim() === "") return 0;
    var value = parseInt(raw, 10);
    return Number.isFinite(value) ? value : raw;
  }

  function modelObj(models, holder) {
    var obj = { type: holder.type };
    modelByType(models, holder.type).fields.forEach(function (field) {
      var value = toNumber(holder.values[field.name], field.integer);
      if (value !== undefined) obj[field.name] = value;
    });
    return obj;
  }

  function serialize() {
    var doc = {};
    if (state.ent.enabled) doc.entanglement_noise_model = modelObj(ENT_MODELS, state.ent);
    doc.nodes = state.nodes.map(function (node) {
      var out = {};
      if (node.name && node.name.trim() !== "") out.name = node.name.trim();
      out.node_type = node.node_type;
      if (node.delays.enabled) {
        var delays = {};
        DELAY_KEYS.forEach(function (key) { delays[key] = delayValue(node.delays.values[key]); });
        out.quantum_operations_delay_ns = delays;
      }
      out.qubit_arrays = node.arrays.map(function (arr) {
        return { num_qubits: toNumber(arr.num_qubits, true), noise_model: modelObj(QUBIT_MODELS, arr) };
      });
      return out;
    });
    return doc;
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (!form.reportValidity()) return;
    if (statusEl) statusEl.textContent = "Saving…";
    fetch("/config/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(serialize()),
    })
      .then(function () { window.location.href = "/?tab=config"; })
      .catch(function (err) {
        if (statusEl) statusEl.textContent = "Save request failed: " + err;
      });
  });

  render();
})();
